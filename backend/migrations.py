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

CURRENT_SCHEMA_VERSION = 2


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


# Ordered list — index 0 = migration 1, index N-1 = migration N
MIGRATIONS: list[Callable[[Engine], None]] = [
    _migration_1,
    _migration_2,
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
                text("INSERT INTO schema_version (id, version) VALUES (1, :v)"),
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
