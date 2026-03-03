from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from database import Base
from models import AgentRun
from routers.chat import ChatRequest, _persist_event, _stream_graph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _DummyChatSlot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _valid_session_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_stream_graph_continues_when_persist_event_fails(monkeypatch, caplog):
    """If _persist_event raises (e.g. DB failure), the stream should continue
    and a warning should be logged rather than silently dropping the error."""

    run_id = str(uuid.uuid4())

    async def _events(*_args, **_kwargs):
        yield {
            "event": "on_tool_start",
            "name": "search_web",
            "run_id": "tool-run-1",
            "metadata": {"tool_call_id": "call-search-1"},
            "data": {"input": {"query": "Apple"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "search_web",
            "run_id": "tool-run-1",
            "metadata": {"tool_call_id": "call-search-1"},
            "data": {
                "input": {"query": "Apple"},
                "output": json.dumps(
                    {"success": True, "data": [{"headline": "x"}], "sources": []}
                ),
            },
        }

    async def _noop_upsert(*_args, **_kwargs):
        return {"nodes_created": 0, "edges_created": 0, "node_ids": []}

    def _failing_persist(*_args, **_kwargs):
        raise RuntimeError("synthetic persist failure")

    monkeypatch.setattr("routers.chat._create_run", lambda *_args, **_kwargs: run_id)
    monkeypatch.setattr("routers.chat._fire_complete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("routers.chat.graph.astream_events", _events)
    monkeypatch.setattr("routers.chat.upsert_from_tool_results", _noop_upsert)
    monkeypatch.setattr("agent.ollama_queue.ollama_chat_slot", lambda: _DummyChatSlot())
    monkeypatch.setattr("routers.chat._persist_event", _failing_persist)

    request = ChatRequest(message="research Apple", session_id=_valid_session_id())

    with caplog.at_level("WARNING"):
        chunks = []
        async for chunk in _stream_graph(request):
            chunks.append(chunk)

    # Stream should still have emitted events despite persistence failures.
    assert chunks
    # At least one warning about failed event persistence should be logged.
    assert any("Failed to persist run event" in rec.getMessage() for rec in caplog.records)


def test_persist_event_warning_on_failure(monkeypatch, caplog):
    """Direct _persist_event failures should log a warning, not stay silent."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LocalSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    # Replace SessionLocal with a version whose commit always fails.
    class _FailingSession(LocalSession):  # type: ignore[misc]
        def commit(self):  # type: ignore[override]
            raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr("routers.chat.SessionLocal", _FailingSession)

    run_id = str(uuid.uuid4())
    db = LocalSession()
    try:
        db.add(AgentRun(id=run_id, session_id="session-1", mode="quick", status="running"))
        db.commit()
    finally:
        db.close()

    payload = {"tool": "search_web", "step_id": "tool-search_web-1"}

    with caplog.at_level("WARNING"):
        _persist_event(run_id, 1, "tool_start", payload)

    assert any("Failed to persist run event" in rec.getMessage() for rec in caplog.records)
