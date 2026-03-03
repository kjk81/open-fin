from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.messages import HumanMessage

from database import Base
from models import AgentRun, AgentRunEvent
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
async def test_stream_events_emit_trace_with_node_and_tool_ids(monkeypatch):
    run_id = str(uuid.uuid4())
    captured: list[tuple[str, dict]] = []

    async def _events(*_args, **_kwargs):
        yield {
            "event": "on_chain_start",
            "name": "route_finance_query",
            "run_id": "node-run-1",
            "data": {},
        }
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
                "output": json.dumps({"success": True, "data": [{"headline": "x"}], "sources": []}),
            },
        }
        yield {
            "event": "on_chain_end",
            "name": "route_finance_query",
            "run_id": "node-run-1",
            "data": {"output": {}},
        }

    async def _noop_upsert(*_args, **_kwargs):
        return {"nodes_created": 0, "edges_created": 0, "node_ids": []}

    monkeypatch.setattr("routers.chat._create_run", lambda *_args, **_kwargs: run_id)
    monkeypatch.setattr("routers.chat._fire_complete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("routers.chat.graph.astream_events", _events)
    monkeypatch.setattr("routers.chat.upsert_from_tool_results", _noop_upsert)
    monkeypatch.setattr("agent.ollama_queue.ollama_chat_slot", lambda: _DummyChatSlot())

    def _capture_event(_run_id: str, _seq: int, event_type: str, payload: dict):
        captured.append((event_type, payload))

    monkeypatch.setattr("routers.chat._fire_event", _capture_event)

    request = ChatRequest(message="research Apple", session_id=_valid_session_id())
    chunks = []
    async for chunk in _stream_graph(request):
        chunks.append(chunk)

    assert chunks
    by_type = {event_type: payload for event_type, payload in captured}

    chain_start = by_type["chain_start"]
    chain_end = by_type["chain_end"]
    tool_start = by_type["tool_start"]
    tool_end = by_type["tool_end"]

    assert chain_start["trace"]["run_id"] == run_id
    assert tool_start["trace"]["run_id"] == run_id
    assert chain_start["trace"]["node_id"] == chain_end["trace"]["node_id"]
    assert tool_start["trace"]["tool_call_id"] == "call-search-1"
    assert tool_end["trace"]["tool_call_id"] == "call-search-1"


def test_persisted_event_payload_contains_trace_object(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LocalSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("routers.chat.SessionLocal", LocalSession)

    run_id = str(uuid.uuid4())
    db = LocalSession()
    try:
        db.add(AgentRun(id=run_id, session_id="session-1", mode="quick", status="running"))
        db.commit()
    finally:
        db.close()

    payload = {
        "tool": "search_web",
        "step_id": "tool-search_web-1",
        "trace": {
            "run_id": run_id,
            "tool_call_id": "call-db-1",
            "tool_name": "search_web",
        },
    }
    _persist_event(run_id, 1, "tool_start", payload)

    db = LocalSession()
    try:
        row = db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run_id).one()
        parsed = json.loads(row.payload_json)
        assert "trace" in parsed
        assert parsed["trace"]["tool_call_id"] == "call-db-1"
    finally:
        db.close()
