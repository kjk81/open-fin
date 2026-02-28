"""Shared test fixtures for the Open-Fin backend test suite.

Provides:
- In-memory SQLite engines (sync + async) that are isolated per test session.
- ``db_session`` / ``async_db_session`` fixtures for DB tests.
- ``patch_db`` autouse-ready monkeypatch that redirects ``database.SessionLocal``
  and ``database.AsyncSessionLocal`` to the test database.
- Factory helpers for ``KGNode``, ``KGEdge``, ``ToolResult``, etc.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, date
from typing import Any, AsyncGenerator, Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from database import Base


# ---------------------------------------------------------------------------
# Sync in-memory SQLite engine (shared across the entire test session)
# ---------------------------------------------------------------------------

_TEST_SYNC_URL = "sqlite:///:memory:"
_TEST_ASYNC_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_engine(_TEST_SYNC_URL, connect_args={"check_same_thread": False})

@event.listens_for(test_engine, "connect")
def _set_pragma_sync(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


# ---------------------------------------------------------------------------
# Async in-memory SQLite engine
# ---------------------------------------------------------------------------

test_async_engine = create_async_engine(_TEST_ASYNC_URL)
TestAsyncSessionLocal = async_sessionmaker(
    test_async_engine, class_=AsyncSession, expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    """Create all tables once for the whole test session."""
    # Import all models so Base.metadata knows about them
    import models  # noqa: F401
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide a clean sync SQLAlchemy session that rolls back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
async def async_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a clean async SQLAlchemy session for a single test."""
    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestAsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def patch_db(monkeypatch, db_session):
    """Redirect ``database.SessionLocal`` to the test session factory.

    Usage: add ``patch_db`` to a test's parameter list (or mark it
    ``autouse=True`` in a specific test module).
    """
    import database

    def _factory():
        return db_session

    monkeypatch.setattr(database, "SessionLocal", TestSessionLocal)
    return db_session


@pytest.fixture()
def patch_async_db(monkeypatch):
    """Redirect ``database.AsyncSessionLocal`` to the test async factory."""
    import database
    monkeypatch.setattr(database, "AsyncSessionLocal", TestAsyncSessionLocal)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def make_kg_node(
    *,
    node_type: str = "company",
    name: str = "AAPL",
    metadata_json: str = "{}",
    is_deleted: bool = False,
) -> dict[str, Any]:
    """Return kwargs suitable for ``KGNode(**make_kg_node())``."""
    return {
        "node_type": node_type,
        "name": name,
        "metadata_json": metadata_json,
        "is_deleted": is_deleted,
        "updated_at": datetime.utcnow(),
    }


def make_kg_edge(
    *,
    source_id: int = 1,
    target_id: int = 2,
    relationship: str = "IN_SECTOR",
    weight: float = 1.0,
) -> dict[str, Any]:
    """Return kwargs suitable for ``KGEdge(**make_kg_edge())``."""
    return {
        "source_id": source_id,
        "target_id": target_id,
        "relationship": relationship,
        "weight": weight,
    }


def make_tool_result(
    *,
    data: Any = None,
    tool_name: str = "test_tool",
    success: bool = True,
    error: str | None = None,
) -> dict[str, Any]:
    """Return a dict that mimics ToolResult.model_dump()."""
    now = datetime.utcnow()
    return {
        "data": data,
        "sources": [],
        "timing": {
            "tool_name": tool_name,
            "started_at": now.isoformat(),
            "ended_at": now.isoformat(),
            "duration_ms": 0.0,
        },
        "success": success,
        "error": error,
    }
