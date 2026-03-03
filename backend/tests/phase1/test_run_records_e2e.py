"""Phase 1 — End-to-end tests for AgentRun and AgentRunEvent via chat router."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import AgentRun, AgentRunEvent
from routers import chat as chat_router


class _DummyChatSlot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_chat_stream_creates_run_and_persists_events(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    # Route all chat persistence to the in-memory database
    monkeypatch.setattr("routers.chat.SessionLocal", SessionLocal)

    # Use a deterministic run_id so we can assert on it later
    run_id = str(uuid.uuid4())

    def _create_run_override(session_id: str, mode: str) -> str:
        db = SessionLocal()
        try:
            db.add(
                AgentRun(
                    id=run_id,
                    session_id=session_id,
                    mode=mode,
                    status="running",
                )
            )
            db.commit()
        finally:
            db.close()
        return run_id

    # Replace _create_run and make _fire_event/_fire_complete synchronous
    monkeypatch.setattr("routers.chat._create_run", _create_run_override)

    def _fire_event_sync(_run_id: str, seq: int, event_type: str, payload: dict) -> None:
        chat_router._persist_event(_run_id, seq, event_type, payload)

    def _fire_complete_sync(_run_id: str, status: str) -> None:
        chat_router._complete_run(_run_id, status)

    monkeypatch.setattr("routers.chat._fire_event", _fire_event_sync)
    monkeypatch.setattr("routers.chat._fire_complete", _fire_complete_sync)

    async def _events(*_args, **_kwargs):
        # Minimal LangGraph trace: one chain lifecycle with no tools.
        yield {
            "event": "on_chain_start",
            "name": "generation_node",
            "run_id": "node-run-1",
            "data": {},
        }
        yield {
            "event": "on_chain_end",
            "name": "generation_node",
            "run_id": "node-run-1",
            "data": {"output": {}},
        }

    async def _noop_upsert(*_args, **_kwargs):
        return {"nodes_created": 0, "edges_created": 0, "node_ids": []}

    monkeypatch.setattr("routers.chat.graph.astream_events", _events)
    monkeypatch.setattr("routers.chat.upsert_from_tool_results", _noop_upsert)
    monkeypatch.setattr("agent.ollama_queue.ollama_chat_slot", lambda: _DummyChatSlot())

    request = chat_router.ChatRequest(message="hello world", session_id=str(uuid.uuid4()))

    # Exhaust the stream to ensure all side effects run
    chunks = []
    async for chunk in chat_router._stream_graph(request):
        chunks.append(chunk)

    assert chunks  # Stream produced at least one SSE chunk

    db = SessionLocal()
    try:
        run_row = db.query(AgentRun).filter(AgentRun.id == run_id).one()
        assert run_row.status in {"success", "error", "timeout"}
        assert run_row.completed_at is not None

        events = (
            db.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.seq.asc(), AgentRunEvent.id.asc())
            .all()
        )
        # At least chain_start and chain_end events should be recorded
        assert len(events) >= 2
        assert {e.type for e in events} >= {"chain_start", "chain_end"}
    finally:
        db.close()

