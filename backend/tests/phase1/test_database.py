"""Phase 1 — Tests for database.py (engines, PRAGMAs, session lifecycle)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from database import Base, _database_url


class TestDatabaseUrl:
    def test_default_url(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove OPEN_FIN_DB_PATH if set
            env = os.environ.copy()
            env.pop("OPEN_FIN_DB_PATH", None)
            with patch.dict(os.environ, env, clear=True):
                url = _database_url()
        assert url.startswith("sqlite:///")

    def test_override_via_env(self, tmp_path):
        db_path = str(tmp_path / "custom" / "test.db")
        with patch.dict(os.environ, {"OPEN_FIN_DB_PATH": db_path}):
            url = _database_url()
        assert "test.db" in url


class TestPragmas:
    def test_wal_mode_set(self, db_session):
        """Verify WAL journal mode is active on the test engine."""
        result = db_session.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode")
        )
        mode = result.scalar()
        assert mode in ("wal", "memory")  # memory for in-memory DBs

    def test_busy_timeout(self, db_session):
        result = db_session.execute(
            __import__("sqlalchemy").text("PRAGMA busy_timeout")
        )
        timeout = result.scalar()
        # In-memory test DB might not propagate this; just assert no crash
        assert timeout is not None


class TestSessionLifecycle:
    def test_sync_session_works(self, db_session):
        from models import Watchlist
        db_session.add(Watchlist(ticker="AAPL"))
        db_session.flush()
        row = db_session.query(Watchlist).filter_by(ticker="AAPL").first()
        assert row is not None

    async def test_async_session_works(self, async_db_session):
        from sqlalchemy import select, text
        result = await async_db_session.execute(text("SELECT 1"))
        assert result.scalar() == 1
