"""Sequential schema migration runner for Open-Fin.

Usage
-----
Called once during FastAPI lifespan startup (``backend/main.py``)::

    from migrations import run_migrations, CURRENT_SCHEMA_VERSION
    success, error = run_migrations(engine)

Adding a new migration
----------------------
1. Increment ``CURRENT_SCHEMA_VERSION``.
2. Write a ``_migration_N`` function that applies the change (idempotent).
3. Append it to ``MIGRATIONS``.

Migrations must be additive-only (add columns/tables, never drop or retype).
Each function receives the SQLAlchemy ``Engine`` and should be idempotent.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version constant — bump when adding a new migration below
# ---------------------------------------------------------------------------

CURRENT_SCHEMA_VERSION = 5


# ---------------------------------------------------------------------------
# Individual migration functions (1-indexed in MIGRATIONS list)
# ---------------------------------------------------------------------------

def _migration_1(engine: Engine) -> None:
    """Add subagent_fallback_order_json column to llm_settings."""
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("llm_settings")]
    if "subagent_fallback_order_json" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE llm_settings ADD COLUMN subagent_fallback_order_json TEXT")
            )
        logger.info("Migration 1: added subagent_fallback_order_json to llm_settings.")
    else:
        logger.debug("Migration 1: subagent_fallback_order_json already present, skipping.")


def _migration_2(engine: Engine) -> None:
    """Baseline: schema_version table was introduced at this version (no-op)."""
    # The table is created by Base.metadata.create_all() before migrations run.
    pass


def _migration_3(engine: Engine) -> None:
    """Add analysis_section_cache table."""
    insp = inspect(engine)
    if "analysis_section_cache" in insp.get_table_names():
        logger.debug("Migration 3: analysis_section_cache already exists, skipping.")
        return

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE analysis_section_cache ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ticker VARCHAR(20) NOT NULL,"
            "  section VARCHAR(30) NOT NULL,"
            "  content TEXT NOT NULL,"
            "  rating VARCHAR(30) DEFAULT '',"
            "  generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  source VARCHAR(20) DEFAULT 'llm',"
            "  ttl_seconds INTEGER DEFAULT 14400,"
            "  CONSTRAINT uq_analysis_ticker_section UNIQUE (ticker, section)"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_analysis_section_cache_ticker "
            "ON analysis_section_cache (ticker)"
        ))
    logger.info("Migration 3: created analysis_section_cache table.")


def _migration_4(engine: Engine) -> None:
    """Add ticker_notes table for per-ticker note history entries."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS ticker_notes ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ticker VARCHAR(20) NOT NULL,"
            "  content TEXT NOT NULL,"
            "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ticker_notes_ticker "
            "ON ticker_notes (ticker)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ticker_notes_created_at "
            "ON ticker_notes (created_at)"
        ))
    logger.info("Migration 4: ensured ticker_notes table and indexes exist.")


def _migration_5(engine: Engine) -> None:
    """Add agent_runs and agent_run_events tables for run persistence."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS agent_runs ("
            "  id VARCHAR(36) PRIMARY KEY,"
            "  session_id VARCHAR(64) NOT NULL,"
            "  mode VARCHAR(20) NOT NULL,"
            "  status VARCHAR(20) NOT NULL DEFAULT 'running',"
            "  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  completed_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_runs_session_id "
            "ON agent_runs (session_id)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS agent_run_events ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  run_id VARCHAR(36) NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,"
            "  seq INTEGER NOT NULL,"
            "  type VARCHAR(20) NOT NULL,"
            "  payload_json TEXT DEFAULT '{}',"
            "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_run_events_run_id "
            "ON agent_run_events (run_id)"
        ))
    logger.info("Migration 5: created agent_runs and agent_run_events tables.")


# Ordered list — index 0 = migration 1, index N-1 = migration N
MIGRATIONS: list[Callable[[Engine], None]] = [
    _migration_1,
    _migration_2,
    _migration_3,
    _migration_4,
    _migration_5,
]


# ---------------------------------------------------------------------------
# Version persistence helpers
# ---------------------------------------------------------------------------

def get_current_version(engine: Engine) -> int:
    """Read the persisted schema version.

    Returns 0 if the ``schema_version`` table does not exist or has no rows
    (legacy database that predates the versioning system).
    """
    insp = inspect(engine)
    if "schema_version" not in insp.get_table_names():
        return 0
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT version FROM schema_version LIMIT 1")
        ).fetchone()
        return int(row[0]) if row else 0


def set_version(engine: Engine, version: int) -> None:
    """Upsert the schema version row."""
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM schema_version LIMIT 1")
        ).fetchone()
        if existing:
            conn.execute(
                text(
                    "UPDATE schema_version "
                    "SET version = :v, migrated_at = CURRENT_TIMESTAMP"
                ),
                {"v": version},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO schema_version (id, version, migrated_at) "
                    "VALUES (1, :v, CURRENT_TIMESTAMP)"
                ),
                {"v": version},
            )


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def run_migrations(engine: Engine) -> tuple[bool, str | None]:
    """Apply all pending migrations sequentially.

    Parameters
    ----------
    engine:
        Bound SQLAlchemy engine for the Open-Fin database.

    Returns
    -------
    (success, error_detail)
        ``(True, None)`` when all migrations applied or DB already current.
        ``(False, str)`` when a migration step failed; the DB is left at the
        last successfully applied version.
    """
    current = get_current_version(engine)
    target = CURRENT_SCHEMA_VERSION

    if current > target:
        detail = (
            f"Database schema version {current} is newer than this version of "
            f"Open-Fin (expects up to {target}). "
            "Please update the application to continue."
        )
        logger.error(detail)
        return False, detail

    if current == target:
        logger.info("Schema is up to date (version %d).", current)
        return True, None

    logger.info(
        "Migrating schema from version %d to %d...", current, target
    )

    for v in range(current + 1, target + 1):
        fn = MIGRATIONS[v - 1]
        try:
            fn(engine)
            set_version(engine, v)
            logger.info("Migration %d (%s) applied.", v, fn.__doc__ or fn.__name__)
        except Exception as exc:
            detail = (
                f"Migration step {v} ({fn.__doc__ or fn.__name__}) failed: {exc}"
            )
            logger.exception(detail)
            return False, detail

    logger.info("Schema migrations complete. Now at version %d.", target)
    return True, None
