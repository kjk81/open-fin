"""Tests for the /api/chat router — SSE streaming, validation, error handling."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from routers.chat import router as chat_router, ChatRequest, SystemEventRequest, GRAPH_STREAM_TIMEOUT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_session_id() -> str:
    return str(uuid.uuid4())


def _parse_sse(body: str) -> list[dict]:
    """Parse an SSE response body into a list of JSON payloads."""
    events: list[dict] = []
    for line in body.strip().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _build_app(graph_mock=None, upsert_mock=None) -> FastAPI:
    """Create a minimal FastAPI app with patched deps for chat."""
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return app


# ---------------------------------------------------------------------------
# Async event generators for mocking graph.astream_events
# ---------------------------------------------------------------------------

async def _happy_path_events(*_args, **_kwargs):
    """Yield tool_start → tool_end → token events."""
    yield {
        "event": "on_tool_start",
        "name": "get_company_profile",
        "data": {"input": {"ticker": "AAPL"}},
    }
    yield {
        "event": "on_tool_end",
        "name": "get_company_profile",
        "data": {
            "input": {"ticker": "AAPL"},
            "output": json.dumps({"success": True, "data": {"symbol": "AAPL"}, "sources": []}),
        },
    }

    class _Chunk:
        content = "Apple looks strong."

    yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}


async def _error_events(*_args, **_kwargs):
    raise Exception("boom")
    yield  # pragma: no cover — makes this an async generator


async def _slow_events(*_args, **_kwargs):
    """Simulate a very slow LLM — sleeps longer than the timeout."""
    await asyncio.sleep(999)
    yield {"event": "on_chat_model_stream", "name": "", "data": {}}  # pragma: no cover


# ---------------------------------------------------------------------------
# Pydantic model validation tests (no HTTP needed)
# ---------------------------------------------------------------------------

class TestChatRequestValidation:
    """Pydantic-level validation of ChatRequest."""

    def test_valid_uuid_session_id(self):
        req = ChatRequest(message="hi", session_id=_valid_session_id())
        assert req.session_id

    @pytest.mark.parametrize(
        "bad_id",
        [
            "abc",
            "<script>alert(1)</script>",
            "not-a-uuid-at-all-nope",
            "12345678-1234-1234-1234-1234567890g",  # 'g' invalid
            "",
        ],
    )
    def test_invalid_session_id_rejected(self, bad_id):
        with pytest.raises(Exception):  # ValidationError
            ChatRequest(message="hi", session_id=bad_id)

    def test_empty_message_rejected(self):
        with pytest.raises(Exception):
            ChatRequest(message="", session_id=_valid_session_id())

    def test_context_refs_user_portfolio_accepted(self):
        req = ChatRequest(
            message="hi",
            session_id=_valid_session_id(),
            context_refs=["user_portfolio"],
        )
        assert req.context_refs == ["user_portfolio"]

    def test_context_refs_valid_ticker_accepted(self):
        req = ChatRequest(
            message="hi",
            session_id=_valid_session_id(),
            context_refs=["AAPL", "BRK.B"],
        )
        assert req.context_refs == ["AAPL", "BRK.B"]

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "../../etc/passwd",
            "<script>",
            "invalid ref with spaces",
            "a" * 20,
        ],
    )
    def test_context_refs_invalid_rejected(self, bad_ref):
        with pytest.raises(Exception):
            ChatRequest(
                message="hi",
                session_id=_valid_session_id(),
                context_refs=[bad_ref],
            )

    def test_context_refs_too_many_rejected(self):
        with pytest.raises(Exception):
            ChatRequest(
                message="hi",
                session_id=_valid_session_id(),
                context_refs=["AAPL"] * 25,
            )


class TestSystemEventRequestValidation:
    """Pydantic-level validation of SystemEventRequest."""

    def test_valid(self):
        req = SystemEventRequest(session_id=_valid_session_id(), content="test")
        assert req.content == "test"

    def test_invalid_session_id(self):
        with pytest.raises(Exception):
            SystemEventRequest(session_id="bad", content="test")


# ---------------------------------------------------------------------------
# SSE endpoint tests via httpx.AsyncClient
# ---------------------------------------------------------------------------

class TestChatSSE:
    """Integration tests for the /api/chat SSE endpoint."""

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        """Patch graph and KG for every test in this class."""
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

    async def test_sse_happy_path(self):
        """Full happy-path: tool_start, tool_end, token, done events."""
        self.graph_mock.astream_events = _happy_path_events
        app = self._app()

        status, body = await self._post_chat(app, {
            "message": "Analyze AAPL",
            "session_id": _valid_session_id(),
            "context_refs": [],
        })
        assert status == 200

        events = _parse_sse(body)
        types = [e["type"] for e in events]
        assert "tool_start" in types
        assert "tool_end" in types
        assert "token" in types
        assert types[-1] == "done"

    async def test_sse_tool_end_includes_duration(self):
        self.graph_mock.astream_events = _happy_path_events
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "Analyze AAPL",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        tool_end_events = [e for e in events if e["type"] == "tool_end"]
        assert len(tool_end_events) >= 1
        assert "duration_ms" in tool_end_events[0]
        assert isinstance(tool_end_events[0]["duration_ms"], int)

    async def test_invalid_session_id_returns_422(self):
        app = self._app()
        status, _ = await self._post_chat(app, {
            "message": "hi",
            "session_id": "not-a-uuid",
        })
        assert status == 422

    async def test_empty_message_returns_422(self):
        app = self._app()
        status, _ = await self._post_chat(app, {
            "message": "",
            "session_id": _valid_session_id(),
        })
        assert status == 422

    async def test_invalid_context_ref_returns_422(self):
        app = self._app()
        status, _ = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
            "context_refs": ["../../etc/passwd"],
        })
        assert status == 422

    async def test_error_event_on_graph_failure(self):
        """When the graph raises a generic exception, the client receives an error SSE event
        with a message that includes the exception type name."""
        self.graph_mock.astream_events = _error_events
        app = self._app()

        status, body = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        assert status == 200  # SSE streams start as 200
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        # Generic exceptions now include the type name for debuggability
        assert "Exception" in error_events[0]["content"]
        assert "detail" in error_events[0]
        assert "Exception" in error_events[0]["detail"]
        # Must no longer be the old opaque message
        assert error_events[0]["content"] != "An internal error occurred."

    async def test_timeout_yields_error_event(self, monkeypatch):
        """When the graph exceeds the timeout, client receives a timeout error."""
        monkeypatch.setattr("routers.chat.GRAPH_STREAM_TIMEOUT", 0.1)
        self.graph_mock.astream_events = _slow_events
        app = self._app()

        status, body = await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        assert status == 200
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "timed out" in error_events[0]["content"].lower()

    async def test_kg_upsert_skipped_when_no_tool_results(self):
        """When no tool results accumulate, upsert_from_tool_results is not called."""
        # An async generator that yields only a token
        async def _tokens_only(*_a, **_kw):
            class _C:
                content = "Hello"
            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _C()}}

        self.graph_mock.astream_events = _tokens_only
        app = self._app()

        await self._post_chat(app, {
            "message": "hi",
            "session_id": _valid_session_id(),
        })
        self.upsert_mock.assert_not_called()

    async def test_sources_event_emitted_when_present(self):
        """Sources accumulated from tool_end should be emitted as a sources event."""
        async def _events_with_sources(*_a, **_kw):
            yield {
                "event": "on_tool_end",
                "name": "web_search",
                "data": {
                    "input": {"query": "AAPL"},
                    "output": json.dumps({
                        "success": True,
                        "data": {},
                        "sources": [{"url": "https://example.com", "title": "Example"}],
                    }),
                },
            }

        self.graph_mock.astream_events = _events_with_sources
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "search AAPL",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        source_events = [e for e in events if e["type"] == "sources"]
        assert len(source_events) == 1
        assert source_events[0]["sources"][0]["url"] == "https://example.com"


class TestChunkContentNormalization:
    """Unit tests for the on_chat_model_stream chunk.content normalisation logic.

    These tests exercise the SSE endpoint's handling of non-string chunk.content
    values — the root cause of Issue 1 ([object Object] in AI Analysis panel).
    """

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        self.graph_mock = MagicMock()
        self.upsert_mock = AsyncMock(return_value={"nodes_created": 0, "edges_created": 0, "node_ids": []})
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

    async def test_list_content_is_joined_to_string(self):
        """chunk.content = list[dict] (structured blocks) → token event content is a string."""
        async def _list_content_events(*_a, **_kw):
            class _Chunk:
                content = [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}]
            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}

        self.graph_mock.astream_events = _list_content_events
        _, body = await self._post_chat(self._app(), {
            "message": "Analyze MSFT",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) == 1
        # Content must be a plain string — never a list or '[object Object]'
        assert isinstance(token_events[0]["content"], str)
        assert token_events[0]["content"] == "Hello world"

    async def test_list_content_with_non_text_items_skips_missing_text(self):
        """Items without a 'text' key contribute an empty string."""
        async def _partial_content(*_a, **_kw):
            class _Chunk:
                content = [{"type": "tool_call", "id": "xyz"}, {"type": "text", "text": "OK"}]
            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}

        self.graph_mock.astream_events = _partial_content
        _, body = await self._post_chat(self._app(), {
            "message": "test",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) == 1
        assert isinstance(token_events[0]["content"], str)
        assert token_events[0]["content"] == "OK"

    async def test_empty_list_content_emits_no_token(self):
        """An empty list normalises to '' which is falsy — no token event emitted."""
        async def _empty_list(*_a, **_kw):
            class _Chunk:
                content = []
            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}

        self.graph_mock.astream_events = _empty_list
        _, body = await self._post_chat(self._app(), {
            "message": "test",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) == 0

    async def test_string_content_passes_through_unchanged(self):
        """Normal str chunk.content is not modified."""
        async def _str_content(*_a, **_kw):
            class _Chunk:
                content = "MSFT is trading at $400."
            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}

        self.graph_mock.astream_events = _str_content
        _, body = await self._post_chat(self._app(), {
            "message": "test",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) == 1
        assert token_events[0]["content"] == "MSFT is trading at $400."

    async def test_runtime_error_surfaces_actionable_message(self):
        """Issue 2: RuntimeError from FallbackLLM → SSE error content is the actual
        RuntimeError message, not the old opaque 'An internal error occurred.'"""
        actionable_msg = (
            "No LLM provider available or all providers failed. "
            "Configure at least one provider in backend/.env (or the app settings)."
        )

        async def _runtime_error_events(*_a, **_kw):
            raise RuntimeError(actionable_msg)
            yield  # pragma: no cover

        self.graph_mock.astream_events = _runtime_error_events
        _, body = await self._post_chat(self._app(), {
            "message": "Should I buy @MSFT",
            "session_id": _valid_session_id(),
            "context_refs": ["MSFT"],
        })
        events = _parse_sse(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        # The full RuntimeError message must reach the client
        assert error_events[0]["content"] == actionable_msg
        assert "RuntimeError" in error_events[0]["detail"]
        # Old generic message must be gone
        assert error_events[0]["content"] != "An internal error occurred."

    async def test_kg_error_emits_kg_update_with_error_field(self):
        """Issue 3: when upsert_from_tool_results raises, the endpoint emits a
        kg_update event with nodes_created=0, edges_created=0, and an error field."""
        async def _tool_with_result(*_a, **_kw):
            yield {
                "event": "on_tool_end",
                "name": "get_company_profile",
                "data": {
                    "input": {"ticker": "MSFT"},
                    "output": json.dumps({"success": True, "data": {"symbol": "MSFT"}, "sources": []}),
                },
            }

        self.graph_mock.astream_events = _tool_with_result
        # Make upsert fail
        self.upsert_mock.side_effect = Exception("DB constraint violation")

        _, body = await self._post_chat(self._app(), {
            "message": "Should I buy @MSFT",
            "session_id": _valid_session_id(),
            "context_refs": ["MSFT"],
        })
        events = _parse_sse(body)
        kg_events = [e for e in events if e["type"] == "kg_update"]
        assert len(kg_events) == 1
        assert kg_events[0]["nodes_created"] == 0
        assert kg_events[0]["edges_created"] == 0
        assert "DB constraint violation" in kg_events[0]["error"]


class TestChatSystemEvent:
    """Tests for POST /api/chat/system_event."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.session_mock = MagicMock()
        self._p = patch(
            "routers.chat.SessionLocal",
            MagicMock(return_value=self.session_mock),
        )
        self._p.start()
        yield
        self._p.stop()

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_router, prefix="/api")
        return app

    async def test_valid_system_event(self):
        app = self._app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat/system_event", json={
                "session_id": _valid_session_id(),
                "content": "Portfolio synced",
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_invalid_session_id_system_event(self):
        app = self._app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat/system_event", json={
                "session_id": "not-uuid",
                "content": "test",
            })
        assert resp.status_code == 422

    async def test_empty_content_system_event(self):
        app = self._app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat/system_event", json={
                "session_id": _valid_session_id(),
                "content": "",
            })
        assert resp.status_code == 422

    async def test_db_error_returns_500(self):
        self.session_mock.commit.side_effect = Exception("db fail")
        app = self._app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat/system_event", json={
                "session_id": _valid_session_id(),
                "content": "oops",
            })
        assert resp.status_code == 500
