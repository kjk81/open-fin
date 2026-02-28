"""Phase 7 conftest — stub heavy transitive deps, provide router test fixtures."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub heavy modules that are not needed for unit tests
# ---------------------------------------------------------------------------

_HEAVY_MODULES = [
    "langchain_core",
    "langchain_core.tools",
    "langchain_core.language_models",
    "langchain_core.language_models.chat_models",
    "langchain_openai",
    "langchain_google_genai",
    "langchain_ollama",
    "langchain",
    "langchain.tools",
    "langgraph",
    "langgraph.graph",
    "langgraph.prebuilt",
    "langgraph.graph.message",
    "yfinance",
    "tavily",
    "exa_py",
    "alpaca_trade_api",
    "faiss",
    "fastembed",
]

for _mod in _HEAVY_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ---------------------------------------------------------------------------
# Lightweight message stubs (so isinstance() works in agent code)
# ---------------------------------------------------------------------------

class _BaseMessage:
    def __init__(self, content: str = ""):
        self.content = content


class HumanMessage(_BaseMessage):
    pass


class AIMessage(_BaseMessage):
    pass


class SystemMessage(_BaseMessage):
    pass


class AIMessageChunk(_BaseMessage):
    pass


class BaseMessage(_BaseMessage):
    pass


class ToolMessage(_BaseMessage):
    def __init__(self, content: str = "", tool_call_id: str = ""):
        super().__init__(content)
        self.tool_call_id = tool_call_id


_messages_mod = MagicMock()
_messages_mod.HumanMessage = HumanMessage
_messages_mod.AIMessage = AIMessage
_messages_mod.SystemMessage = SystemMessage
_messages_mod.AIMessageChunk = AIMessageChunk
_messages_mod.BaseMessage = BaseMessage
_messages_mod.ToolMessage = ToolMessage
sys.modules["langchain_core.messages"] = _messages_mod

# Patch langgraph.graph attributes
import langgraph.graph as _lg  # type: ignore

_lg.StateGraph = MagicMock()
_lg.START = "START"
_lg.END = "END"

import langgraph.graph.message as _lgm  # type: ignore

_lgm.add_messages = lambda x, y: x + y

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
async def _ensure_async_tables():
    """Create KG tables on the async test engine and clean between tests.

    The root conftest ``_create_tables`` only populates the *sync* engine.
    ``patch_async_db`` patches the session factory but never calls
    ``create_all`` on the async engine, so any test that opens
    ``TestAsyncSessionLocal()`` directly will hit 'no such table'.

    Also wipes *sync* rows between tests because
    ``upsert_ticker_snapshot`` commits via a standalone ``SessionLocal()``
    session whose writes are NOT rolled back by ``db_session``'s
    transaction.
    """
    from tests.conftest import test_async_engine, test_engine
    from database import Base
    import models  # noqa: F401

    async with test_async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Wipe rows between tests so they don't leak (async)
    async with test_async_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    # Wipe rows between tests so they don't leak (sync)
    with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def patch_async_db(monkeypatch):
    """Override root conftest ``patch_async_db`` to also patch the local
    reference inside ``agent.knowledge_graph`` (which does
    ``from database import AsyncSessionLocal``).
    """
    import database
    from tests.conftest import TestAsyncSessionLocal

    monkeypatch.setattr(database, "AsyncSessionLocal", TestAsyncSessionLocal)
    monkeypatch.setattr(
        "agent.knowledge_graph.AsyncSessionLocal", TestAsyncSessionLocal,
    )


@pytest.fixture()
def patch_db(monkeypatch, db_session):
    """Override root conftest ``patch_db`` to also patch the local
    reference inside ``agent.knowledge_graph``.
    """
    import database
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(database, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(
        "agent.knowledge_graph.SessionLocal", TestSessionLocal,
    )
    return db_session


@pytest.fixture()
def faiss_write_queue() -> asyncio.Queue:
    """Bounded asyncio queue for FAISS writer tests."""
    return asyncio.Queue(maxsize=100)


@pytest.fixture()
def mock_faiss_manager():
    """A MagicMock that behaves like FaissManager for unit tests."""
    import numpy as np

    mgr = MagicMock()
    mgr.embed_one.return_value = np.zeros(384, dtype=np.float32)
    mgr.embed.side_effect = lambda texts: np.zeros((len(texts), 384), dtype=np.float32)
    mgr.upsert_vectors = MagicMock()
    mgr.maybe_rebuild = MagicMock(return_value=False)
    mgr._rebuild_from_db = MagicMock()
    mgr.text_for_node = MagicMock(side_effect=lambda t, n, m=None: n)
    mgr.search = MagicMock(return_value=[])
    return mgr


@pytest.fixture()
def _patch_graph_and_kg():
    """Patch the graph and KG imports used by routers/chat.py.

    Returns a dict ``{"graph_mock": ..., "upsert_mock": ...}`` so tests
    can configure behaviour.
    """
    graph_mock = MagicMock()
    graph_mock.astream_events = AsyncMock()

    upsert_mock = AsyncMock(return_value={
        "nodes_created": 0,
        "edges_created": 0,
        "node_ids": [],
    })

    with (
        patch("routers.chat.graph", graph_mock),
        patch("routers.chat.upsert_from_tool_results", upsert_mock),
        patch("routers.chat.SessionLocal", MagicMock()),
    ):
        yield {"graph_mock": graph_mock, "upsert_mock": upsert_mock}
