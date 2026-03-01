"""Targeted tests verifying fixes for Issues 1-5.

Covers:
  Issue 1: _get_tool_bound_model fallback + bind_tools safety,
           route_finance_query graceful error handling
  Issue 2: intent_router routes NLP queries to ticker_deep_dive
  Issue 5: SSE error events include detail field
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from routers.chat import router as chat_router, GRAPH_STREAM_TIMEOUT


# ── Helpers ──────────────────────────────────────────────────────────────────

def _valid_session_id() -> str:
    return str(uuid.uuid4())


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.strip().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ── Issue 1: _get_tool_bound_model ───────────────────────────────────────────

class TestGetToolBoundModelFallback:
    """Verify _get_tool_bound_model falls back from subagent → agent."""

    def test_subagent_fallback_to_agent(self):
        """When subagent provider is unavailable, fallback to agent."""
        from agent.graph import _get_tool_bound_model

        fake_model = MagicMock()
        fake_model.bind_tools.return_value = fake_model

        def mock_get_model(role: str = "agent"):
            if role == "subagent":
                raise RuntimeError("No subagent provider")
            return fake_model

        with patch("agent.graph._get_model", side_effect=mock_get_model):
            result = _get_tool_bound_model([MagicMock()], role="subagent")
            assert result is fake_model
            fake_model.bind_tools.assert_called_once()

    def test_bind_tools_unsupported_raises_descriptive_error(self):
        """When model lacks bind_tools, raise RuntimeError with clear message."""
        from agent.graph import _get_tool_bound_model

        fake_model = MagicMock()
        fake_model.bind_tools.side_effect = AttributeError("no bind_tools")

        with patch("agent.graph._get_model", return_value=fake_model):
            with pytest.raises(RuntimeError, match="does not support tool binding"):
                _get_tool_bound_model([MagicMock()], role="agent")

    def test_agent_role_reraises_if_agent_unavailable(self):
        """When role='agent' and provider is unavailable, don't swallow RuntimeError."""
        from agent.graph import _get_tool_bound_model

        with patch("agent.graph._get_model", side_effect=RuntimeError("No agent provider")):
            with pytest.raises(RuntimeError, match="No agent provider"):
                _get_tool_bound_model([MagicMock()], role="agent")


# ── Issue 1/3: route_finance_query graceful error ────────────────────────────

class TestRouteFinanceQueryGraceful:
    """route_finance_query returns descriptive AIMessage on LLM failure."""

    async def test_llm_failure_returns_error_message(self):
        from agent.graph import route_finance_query
        from langchain_core.messages import HumanMessage

        fake_model = MagicMock()
        fake_model.ainvoke = AsyncMock(side_effect=RuntimeError("API overloaded"))

        state = {
            "messages": [HumanMessage("Analyze @AAPL")],
            "intent": "ticker_deep_dive",
            "tickers_mentioned": ["AAPL"],
            "context_refs": [],
            "injected_context": "",
            "tool_results": {},
            "finance_loop_count": 0,
        }

        with patch("agent.graph._get_tool_bound_model", return_value=fake_model):
            result = await route_finance_query(state)

        # Should NOT raise; should return a state with an error AI message
        last_msg = result["messages"][-1]
        assert "error" in last_msg.content.lower() or "RuntimeError" in last_msg.content


# ── Issue 2: intent_router NLP routing ───────────────────────────────────────

class TestIntentRouterNlpQueries:
    """NLP financial queries without explicit tickers route to ticker_deep_dive."""

    async def test_performance_keyword_no_ticker(self):
        """'How is Tesla doing?' should route to ticker_deep_dive."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {"messages": [HumanMessage("How is Tesla doing?")], "context_refs": []}
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_deep_dive_keyword_no_ticker(self):
        """'Analyze market trends' should route to ticker_deep_dive."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {"messages": [HumanMessage("analyze market trends")], "context_refs": []}
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_performance_with_ticker_still_works(self):
        """'How is @TSLA doing?' should still work and extract TSLA."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {"messages": [HumanMessage("How is @TSLA doing?")], "context_refs": []}
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"
        assert "TSLA" in result["tickers_mentioned"]

    async def test_pure_general_chat_unchanged(self):
        """Non-financial query like 'What is inflation?' should stay general_chat."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {"messages": [HumanMessage("What time is it?")], "context_refs": []}
        result = await intent_router(state)
        assert result["intent"] == "general_chat"


# ── Issue 5: SSE detail field ────────────────────────────────────────────────

class TestSSEDetailField:
    """SSE error events include a 'detail' field for debugging."""

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        self.graph_mock = MagicMock()
        self.upsert_mock = AsyncMock(return_value={
            "nodes_created": 0,
            "edges_created": 0,
            "node_ids": [],
        })
        self.session_mock = MagicMock()

        self._patches = [
            patch("routers.chat.graph", self.graph_mock),
            patch("routers.chat.upsert_from_tool_results", self.upsert_mock),
            patch("routers.chat.SessionLocal", MagicMock(return_value=self.session_mock)),
        ]
        for p in self._patches:
            p.start()
        yield
        for p in self._patches:
            p.stop()

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_router, prefix="/api")
        return app

    async def _post_chat(self, app, payload: dict) -> tuple[int, str]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat", json=payload, timeout=30)
            return resp.status_code, resp.text

    async def test_sse_error_has_detail_field(self):
        """Generic Exception SSE error includes detail with type name."""
        async def _error_events(*_args, **_kwargs):
            raise ValueError("bad value")
            yield  # pragma: no cover

        self.graph_mock.astream_events = _error_events
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["content"] == "An internal error occurred."
        assert "ValueError" in error_events[0]["detail"]
        assert "bad value" in error_events[0]["detail"]

    async def test_sse_runtime_error_has_detail(self):
        """RuntimeError SSE error includes detail."""
        async def _runtime_error(*_args, **_kwargs):
            raise RuntimeError("no provider configured")
            yield  # pragma: no cover

        self.graph_mock.astream_events = _runtime_error
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "RuntimeError" in error_events[0]["detail"]
        assert "no provider" in error_events[0]["detail"]

    async def test_sse_timeout_error_has_detail(self, monkeypatch):
        """Timeout SSE error includes detail with timeout info."""
        monkeypatch.setattr("routers.chat.GRAPH_STREAM_TIMEOUT", 0.1)

        async def _slow_events(*_args, **_kwargs):
            await asyncio.sleep(999)
            yield {"event": "on_chat_model_stream", "name": "", "data": {}}  # pragma: no cover

        self.graph_mock.astream_events = _slow_events
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "timed out" in error_events[0]["content"].lower()
        assert "TimeoutError" in error_events[0]["detail"]
