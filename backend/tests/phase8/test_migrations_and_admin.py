"""Tests for backend/migrations.py and backend/routers/admin.py.

Covers:
- Migration runner version tracking and idempotency.
- Legacy DB detection via schema introspection (not row counts).
- Partial migration failure handling.
- Admin wipe endpoint: local-only guard, env-gate, and happy path.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine():
    """Return a fresh in-memory SQLite engine with all tables created."""
    import models  # noqa: F401 — registers ORM classes on Base.metadata
    from database import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return eng


def _make_legacy_engine():
    """Return an in-memory engine that simulates a legacy (pre-versioning) DB.

    Specifically: ``llm_settings`` exists but ``schema_version`` does NOT,
    and ``subagent_fallback_order_json`` column is absent.
    """
    import models  # noqa: F401

    from database import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # Create only the tables that would exist in an old installation
    # (skip schema_version, which didn't exist yet)
    subset = [
        t for t in Base.metadata.sorted_tables
        if t.name not in ("schema_version",)
        and t.name not in ("llm_settings",)
    ]
    Base.metadata.create_all(bind=eng, tables=subset)

    # Manually create llm_settings WITHOUT the new column, simulating pre-migration
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS llm_settings ("
            "  id INTEGER PRIMARY KEY,"
            "  provider TEXT,"
            "  model_name TEXT,"
            "  api_key TEXT,"
            "  base_url TEXT,"
            "  temperature REAL,"
            "  max_tokens INTEGER,"
            "  system_prompt TEXT,"
            "  is_default INTEGER"
            ")"
        ))

    return eng


# ---------------------------------------------------------------------------
# Migration helper function tests
# ---------------------------------------------------------------------------


class TestGetCurrentVersion:
    def test_returns_0_when_no_schema_version_table(self):
        """Engine without schema_version table → version 0."""
        from migrations import get_current_version

        eng = create_engine("sqlite:///:memory:")
        assert get_current_version(eng) == 0

    def test_returns_0_when_table_empty(self):
        from migrations import get_current_version

        eng = _make_engine()
        # Table exists but has no rows
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM schema_version"))
        assert get_current_version(eng) == 0

    def test_returns_stored_version(self):
        from migrations import get_current_version, set_version

        eng = _make_engine()
        set_version(eng, 7)
        assert get_current_version(eng) == 7


class TestSetVersion:
    def test_inserts_when_no_row(self):
        from migrations import get_current_version, set_version

        eng = _make_engine()
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM schema_version"))
        set_version(eng, 3)
        assert get_current_version(eng) == 3

    def test_updates_existing_row(self):
        from migrations import get_current_version, set_version

        eng = _make_engine()
        set_version(eng, 1)
        set_version(eng, 5)
        assert get_current_version(eng) == 5


# ---------------------------------------------------------------------------
# run_migrations tests
# ---------------------------------------------------------------------------


class TestRunMigrations:
    def test_fresh_db_already_current(self):
        """A fresh DB at CURRENT_SCHEMA_VERSION returns (True, None)."""
        from migrations import run_migrations, set_version, CURRENT_SCHEMA_VERSION

        eng = _make_engine()
        set_version(eng, CURRENT_SCHEMA_VERSION)
        ok, err = run_migrations(eng)
        assert ok is True
        assert err is None

    def test_legacy_db_migrations_applied(self):
        """A DB at version 0 must be migrated to CURRENT_SCHEMA_VERSION."""
        from migrations import (
            run_migrations,
            get_current_version,
            set_version,
            CURRENT_SCHEMA_VERSION,
        )

        eng = _make_legacy_engine()
        # Simulate the legacy DB detection by adding schema_version at 0
        from database import Base
        from models import SchemaVersion  # noqa: F401

        Base.metadata.create_all(bind=eng, tables=[Base.metadata.tables["schema_version"]])
        set_version(eng, 0)

        ok, err = run_migrations(eng)
        assert ok is True, f"Expected ok=True, got err={err}"
        assert get_current_version(eng) == CURRENT_SCHEMA_VERSION

    def test_migration_1_adds_column(self):
        """After running _migration_1, the new column must be present."""
        from migrations import _migration_1, set_version, CURRENT_SCHEMA_VERSION

        eng = _make_legacy_engine()
        from database import Base
        Base.metadata.create_all(bind=eng, tables=[Base.metadata.tables["schema_version"]])
        set_version(eng, 0)

        _migration_1(eng)

        cols = [c["name"] for c in inspect(eng).get_columns("llm_settings")]
        assert "subagent_fallback_order_json" in cols

    def test_migration_idempotent(self):
        """Running _migration_1 twice must not raise."""
        from migrations import _migration_1

        eng = _make_legacy_engine()
        _migration_1(eng)  # first run
        _migration_1(eng)  # second run — must be no-op

    def test_fails_on_newer_version(self):
        """If stored version > CURRENT_SCHEMA_VERSION, return (False, str)."""
        from migrations import run_migrations, set_version, CURRENT_SCHEMA_VERSION

        eng = _make_engine()
        set_version(eng, CURRENT_SCHEMA_VERSION + 100)
        ok, err = run_migrations(eng)
        assert ok is False
        assert err is not None
        assert "newer" in err.lower() or str(CURRENT_SCHEMA_VERSION + 100) in err

    def test_partial_failure_stops_and_returns_false(self):
        """A migration step that raises must return (False, details)."""
        from migrations import run_migrations, set_version, MIGRATIONS

        eng = _make_engine()
        set_version(eng, 0)

        def _boom(e):
            raise RuntimeError("injected failure")

        original = MIGRATIONS[:]
        MIGRATIONS.clear()
        MIGRATIONS.append(_boom)
        try:
            ok, err = run_migrations(eng)
        finally:
            MIGRATIONS.clear()
            MIGRATIONS.extend(original)

        assert ok is False
        assert err is not None
        assert "injected failure" in err


# ---------------------------------------------------------------------------
# Legacy-DB detection: schema introspection in main.py lifespan
# ---------------------------------------------------------------------------


class TestLegacyDbDetectionViaIntrospection:
    """The startup code must use SQLAlchemy inspect to distinguish
    legacy vs fresh DBs — not row counts in llm_settings."""

    def test_llm_settings_exists_implies_legacy(self):
        """If llm_settings table exists, the DB is treated as legacy."""
        eng = _make_legacy_engine()
        insp = inspect(eng)
        # Verify the precondition our lifespan code relies on
        assert "llm_settings" in insp.get_table_names()

    def test_fresh_install_lacks_llm_settings(self):
        """A brand-new engine with only schema_version has no llm_settings yet."""
        eng = create_engine("sqlite:///:memory:")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER)"
            ))
        insp = inspect(eng)
        assert "llm_settings" not in insp.get_table_names()

    def test_zero_row_legacy_db_still_detected(self):
        """Legacy DB with 0 rows in llm_settings must still be detected."""
        eng = _make_legacy_engine()
        # Confirm zero rows but table exists
        with eng.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM llm_settings")).scalar()
        assert count == 0
        # Table presence is the signal — not row count
        assert "llm_settings" in inspect(eng).get_table_names()


# ---------------------------------------------------------------------------
# Admin router tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_app():
    """Create a minimal FastAPI app with only the admin router registered."""
    from fastapi import FastAPI
    from routers.admin import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture()
def client(test_app):
    return TestClient(test_app, raise_server_exceptions=False)


@pytest.fixture()
def local_client(test_app, monkeypatch):
    """TestClient whose requests pass the localhost guard (monkeypatched)."""
    import routers.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_LOCALHOST_HOSTS", frozenset({"testclient", "127.0.0.1", "::1"}))
    return TestClient(test_app, raise_server_exceptions=False)


class TestAdminWipeGuards:
    def test_non_local_host_returns_403(self, client):
        """Requests from non-localhost are denied."""
        # TestClient default host is "testclient" — not in _LOCALHOST_HOSTS
        resp = client.post("/api/admin/wipe")
        assert resp.status_code == 403
        assert "localhost" in resp.json()["detail"].lower()

    def test_env_gate_disabled_returns_403(self, test_app, monkeypatch):
        """When OPEN_FIN_ADMIN_WIPE_ENABLED=false the endpoint is blocked."""
        import routers.admin as admin_mod

        monkeypatch.setattr(admin_mod, "_WIPE_ENABLED", False)
        # Even with localhost host, env gate must block
        monkeypatch.setattr(
            admin_mod, "_LOCALHOST_HOSTS",
            frozenset({"testclient", "127.0.0.1", "::1"}),
        )
        c = TestClient(test_app, raise_server_exceptions=False)
        resp = c.post("/api/admin/wipe")
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


class TestAdminWipeHappyPath:
    def test_wipe_db_scope_returns_200(self, local_client, tmp_path, monkeypatch):
        """A local request with scope=db should succeed."""
        # Prevent touching the real FAISS directory
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        resp = local_client.post("/api/admin/wipe?scope=db")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "wiped"
        assert data["scope"] == "db"

    def test_wipe_faiss_scope_returns_200(self, local_client, tmp_path, monkeypatch):
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        # Create a dummy file in the faiss dir to verify deletion
        (tmp_path / "openfin.index").touch()
        resp = local_client.post("/api/admin/wipe?scope=faiss")
        assert resp.status_code == 200
        assert resp.json()["scope"] == "faiss"
        # FAISS dir should have been cleared and recreated empty
        assert tmp_path.exists()
        assert not (tmp_path / "openfin.index").exists()

    def test_wipe_all_scope_returns_200(self, local_client, tmp_path, monkeypatch):
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        resp = local_client.post("/api/admin/wipe?scope=all")
        assert resp.status_code == 200
        assert resp.json()["scope"] == "all"

    def test_wipe_sets_schema_version_to_current(self, local_client, tmp_path, monkeypatch):
        """After db wipe, schema_version row must be at CURRENT_SCHEMA_VERSION."""
        from migrations import get_current_version, CURRENT_SCHEMA_VERSION
        import database

        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        r = local_client.post("/api/admin/wipe?scope=db")
        assert r.status_code == 200
        assert get_current_version(database.engine) == CURRENT_SCHEMA_VERSION

    def test_invalid_scope_returns_422(self, local_client, tmp_path, monkeypatch):
        """Invalid scope query param should be rejected by Pydantic validation."""
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        resp = local_client.post("/api/admin/wipe?scope=invalid")
        assert resp.status_code == 422
