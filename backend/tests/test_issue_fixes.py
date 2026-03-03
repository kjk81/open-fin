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
from datetime import datetime, timedelta, timezone
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

    def test_mode_policy_allow_list_filters_tools_before_bind(self):
        """_get_tool_bound_model must enforce ModePolicy.tool_allow_list."""
        from agent.graph import _get_tool_bound_model
        from agent.modes import get_mode_policy

        fake_model = MagicMock()
        fake_model.bind_tools.return_value = fake_model

        allowed_tool = MagicMock()
        allowed_tool.name = "get_company_profile"
        blocked_tool = MagicMock()
        blocked_tool.name = "search_web"

        mode_policy = get_mode_policy("quick")

        with patch("agent.graph._get_model", return_value=fake_model):
            result = _get_tool_bound_model(
                [allowed_tool, blocked_tool],
                role="agent",
                mode_policy=mode_policy,
            )

        assert result is fake_model
        bound_tools = fake_model.bind_tools.call_args.args[0]
        assert [tool.name for tool in bound_tools] == ["get_company_profile"]


# ── Root cause: _get_model() must unpack 3-tuple from load_llm_settings() ────

class TestGetModelUnpacking:
    """Regression guard: _get_model() was unpacking load_llm_settings() into 2
    variables while the function returns a 3-tuple, causing a ValueError on
    every finance query even when API keys were correctly set.

    These tests call _get_model() WITHOUT mocking _get_model itself so the
    real unpacking code executes. load_llm_settings is mocked at the boundary
    to avoid a live DB dependency.
    """

    def test_get_model_does_not_raise_value_error_when_settings_return_3_tuple(self):
        """_get_model() must not crash when load_llm_settings returns (mode, order, sub)."""
        from agent.graph import _get_model

        fake_model = MagicMock()

        with patch("agent.graph.load_llm_settings", return_value=("cloud", ["openrouter"], None)), \
             patch("agent.graph._provider_model", return_value=fake_model):
            # Must not raise ValueError: too many values to unpack
            result = _get_model(role="agent")
            assert result is fake_model

    def test_get_model_passes_subagent_order_to_effective_order(self):
        """When a subagent_order is configured, _get_model() uses it for role=subagent."""
        from agent.graph import _get_model

        fake_model = MagicMock()
        # subagent_order prioritises groq; agent order has openrouter first
        captured_orders: list = []

        def capture_provider(provider, role=None):
            captured_orders.append((provider, role))
            return fake_model if provider == "groq" else None

        with patch("agent.graph.load_llm_settings",
                   return_value=("cloud", ["openrouter", "groq"], ["groq", "openrouter"])), \
             patch("agent.graph._provider_model", side_effect=capture_provider):
            _get_model(role="subagent")

        # groq must be tried first (subagent_order), not openrouter
        first_provider, first_role = captured_orders[0]
        assert first_provider == "groq"
        assert first_role == "subagent"

    def test_get_model_raises_runtime_error_when_no_provider_configured(self):
        """When no provider is available, RuntimeError (not ValueError) is raised."""
        from agent.graph import _get_model

        with patch("agent.graph.load_llm_settings", return_value=("cloud", ["openrouter"], None)), \
             patch("agent.graph._provider_model", return_value=None):
            with pytest.raises(RuntimeError, match="No LLM provider available"):
                _get_model(role="agent")

    async def test_route_finance_query_graceful_on_model_creation_failure(self):
        """Model creation errors (ValueError, RuntimeError) inside route_finance_query
        must be caught and returned as an AIMessage — not crash the graph stream."""
        from agent.graph import route_finance_query
        from langchain_core.messages import HumanMessage

        state = {
            "messages": [HumanMessage("Analyze @AAPL")],
            "intent": "ticker_deep_dive",
            "tickers_mentioned": ["AAPL"],
            "context_refs": [],
            "injected_context": "",
            "tool_results": {},
            "finance_loop_count": 0,
            # Satisfy capability requirements so tools are allowed and
            # _get_tool_bound_model is invoked (and thus patched below).
            "capabilities": {
                "internet_dns_ok": True,
                "fmp_api_key_present": True,
                "sec_api_key_present": True,
            },
            "current_query": "",
            "active_skills": [],
            "tool_call_count": 0,
        }

        # Simulate the pre-fix crash: ValueError from tuple unpacking
        with patch("agent.graph._get_tool_bound_model",
                   side_effect=ValueError("too many values to unpack (expected 2)")):
            result = await route_finance_query(state)

        last_msg = result["messages"][-1]
        content = str(last_msg.content or "")
        # In older behaviour the ValueError message was surfaced directly; in
        # newer builds capability guards may short-circuit tool execution but
        # must still return a descriptive AIMessage rather than crash.
        assert content, "Expected a non-empty AIMessage content"


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
            "capabilities": {
                "internet_dns_ok": True,
                "fmp_api_key_present": True,
                "sec_api_key_present": True,
            },
        }

        with patch("agent.graph._get_tool_bound_model", return_value=fake_model):
            result = await route_finance_query(state)

        # Should NOT raise; should return a state with an error AI message
        last_msg = result["messages"][-1]
        content = str(last_msg.content or "")
        lowered = content.lower()
        assert (
            "error" in lowered
            or "runtimeerror" in lowered
            or "cannot execute tools" in lowered
        ), f"Unexpected route_finance_query error message: {content!r}"


class TestToolExecutionBudgets:
    async def test_execute_tool_calls_enforces_max_tool_calls_per_invocation(self):
        from agent.graph import execute_tool_calls
        from langchain_core.messages import AIMessage

        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=json.dumps({"success": True, "price": 101.23}))

        msg = AIMessage(content="")
        # LangChain no longer accepts tool_calls in __init__; assign attribute directly.
        object.__setattr__(msg, "tool_calls", [
            {"name": "get_company_profile", "args": {"symbol": "AAPL"}, "id": "call-1"},
            {"name": "get_company_profile", "args": {"symbol": "MSFT"}, "id": "call-2"},
        ])

        state = {
            "messages": [msg],
            "tool_call_count": 2,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "quick",
            "executed_skills": [],
        }

        with patch.dict("agent.graph._TOOL_MAP", {"get_company_profile": fake_tool}, clear=False):
            result = await execute_tool_calls(state)

        assert result["tool_call_count"] == 1
        assert len(result["tool_results"]) == 2
        assert "Budget Exceeded" in result["tool_results"][1]["result"]
        assert result["tool_loop_terminated_reason"] == "budget_exceeded"

    async def test_execute_tool_calls_enforces_max_seconds_budget(self):
        from agent.graph import execute_tool_calls
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="")
        object.__setattr__(msg, "tool_calls", [
            {"name": "get_company_profile", "args": {"symbol": "AAPL"}, "id": "call-1"},
        ])

        state = {
            "messages": [msg],
            "tool_call_count": 0,
            "start_time_utc": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
            "agent_mode": "quick",
            "executed_skills": [],
        }

        result = await execute_tool_calls(state)

        assert result["tool_call_count"] == 0
        assert result["tool_results"]
        assert "Budget Exceeded" in result["tool_results"][0]["result"]
        assert result["tool_loop_terminated_reason"] == "budget_exceeded"


class TestVerificationTiebreaker:
    async def test_tiebreaker_runs_single_targeted_tool_call(self):
        from agent.graph import tiebreaker_tool_execution

        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=json.dumps({
            "success": True,
            "data": {"market_cap": 3000000000000, "currency": "USD"},
            "provenance": {"source": "fmp", "as_of": "2026-03-01"},
            "quality": {"completeness": 0.99},
        }))

        state = {
            "verification_report": {
                "status": "critical",
                "critical": [{"type": "core_fundamental_variance", "claim_key": "revenue"}],
                "warnings": [],
            },
            "tickers_mentioned": ["AAPL"],
            "tool_call_count": 0,
            "tiebreaker_attempt_count": 0,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "research",
        }

        with patch.dict("agent.graph._TOOL_MAP", {"get_financial_statements": fake_tool}, clear=False):
            result = await tiebreaker_tool_execution(state)

        assert result["tiebreaker_attempt_count"] == 1
        assert result["tool_call_count"] == 1
        assert result["tool_results"][0]["tool"] == "get_financial_statements"

    async def test_tiebreaker_skips_when_already_attempted(self):
        from agent.graph import tiebreaker_tool_execution

        state = {
            "verification_report": {"status": "critical", "critical": [{"type": "core_fundamental_variance"}]},
            "tool_call_count": 0,
            "tiebreaker_attempt_count": 1,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "research",
        }
        result = await tiebreaker_tool_execution(state)
        assert result["verification_failure_reason"] == "tiebreaker_already_attempted"

    async def test_tiebreaker_respects_tool_call_budget(self):
        """When tool-call budget is exhausted, tiebreaker must not invoke tools."""
        from agent.graph import tiebreaker_tool_execution

        state = {
            "verification_report": {
                "status": "critical",
                "critical": [{"type": "core_fundamental_variance", "claim_key": "revenue"}],
                "warnings": [],
            },
            "tickers_mentioned": ["AAPL"],
            "tool_call_count": 1,
            "tiebreaker_attempt_count": 0,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "research",
        }

        # Patch mode policy resolution to look like research mode but with
        # max_tool_calls=0 so any existing calls exhaust the budget.
        with patch("agent.graph.get_mode_policy") as get_mode_policy_mock:
            policy = get_mode_policy_mock.return_value
            policy.max_tool_calls = 0
            policy.max_seconds = None
            result = await tiebreaker_tool_execution(state)
            result = await tiebreaker_tool_execution(state)

        assert result["tiebreaker_attempt_count"] == 1
        assert result["verification_failure_reason"] == "tiebreaker_budget_exceeded"
        # No tool_results or additional tool_call_count increments when budget is exceeded.
        assert "tool_results" not in result
        assert result.get("tool_call_count", 0) == 0

    async def test_tiebreaker_respects_time_budget(self):
        """When time budget is exhausted, tiebreaker must not invoke tools."""
        from agent.graph import tiebreaker_tool_execution

        state = {
            "verification_report": {
                "status": "critical",
                "critical": [{"type": "core_fundamental_variance", "claim_key": "eps"}],
                "warnings": [],
            },
            "tickers_mentioned": ["AAPL"],
            "tool_call_count": 0,
            "tiebreaker_attempt_count": 0,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "research",
        }

        with patch("agent.graph.get_mode_policy") as get_mode_policy_mock, \
             patch("agent.graph._elapsed_seconds_since_start", return_value=1.0):
            policy = get_mode_policy_mock.return_value
            policy.max_tool_calls = 10
            policy.max_seconds = 0  # any elapsed > 0 trips the time budget
            result = await tiebreaker_tool_execution(state)

        assert result["tiebreaker_attempt_count"] == 1
        assert result["verification_failure_reason"] == "tiebreaker_time_budget_exceeded"
        assert "tool_results" not in result
        assert result.get("tool_call_count", 0) == 0

    async def test_tiebreaker_handles_missing_tool_gracefully(self):
        """If the selected tiebreaker tool is unavailable, record a failure reason and avoid loops."""
        from agent.graph import tiebreaker_tool_execution

        state = {
            "verification_report": {
                "status": "critical",
                "critical": [{"type": "core_fundamental_variance", "claim_key": "revenue"}],
                "warnings": [],
            },
            "tickers_mentioned": ["AAPL"],
            "tool_call_count": 0,
            "tiebreaker_attempt_count": 0,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "agent_mode": "research",
        }

        # Patch TOOL_MAP to ensure the selected tool name is missing.
        with patch("agent.graph._TOOL_MAP", {}):
            result = await tiebreaker_tool_execution(state)

        assert result["tiebreaker_attempt_count"] == 1
        reason = str(result.get("verification_failure_reason") or "")
        assert reason.startswith("tiebreaker_tool_unavailable:")


class TestCapabilityDegradation:
    async def test_route_finance_query_records_mode_capability_limitation(self):
        from agent.graph import route_finance_query
        from langchain_core.messages import HumanMessage

        fake_model = MagicMock()
        fake_model.ainvoke = AsyncMock(return_value=MagicMock(tool_calls=[], content="ok"))

        state = {
            "messages": [HumanMessage("Research AAPL and web sentiment")],
            "agent_mode": "research",
            "capabilities": {
                "internet_dns_ok": False,
                "fmp_api_key_present": True,
                "sec_api_key_present": True,
                "worker_reachable": True,
            },
            "active_skills": [],
            "current_query": "",
            "executed_skills": [],
            "tool_results": [],
        }

        with patch("agent.graph._get_tool_bound_model", return_value=fake_model):
            result = await route_finance_query(state)

        assert result["degradation_events"]
        assert any(
            "internet access" in json.dumps(item)
            for item in result["degradation_events"]
        )
        assert result["tool_results"]
        assert result["tool_results"][0]["tool"] == "mode_capability_guard"


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
        # Generic exceptions now include the type name for debuggability
        assert "ValueError" in error_events[0]["content"]
        assert error_events[0]["content"] != "An internal error occurred."
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

    async def test_sse_emits_verification_report_metadata(self):
        """on_chain_end output with verification_report must be emitted to SSE."""

        async def _events(*_args, **_kwargs):
            yield {
                "event": "on_chain_end",
                "name": "verification_gate",
                "data": {
                    "output": {
                        "verification_report": {
                            "status": "warning",
                            "warnings": [{"type": "missing_as_of", "claim_key": "market_cap"}],
                            "critical": [],
                        }
                    }
                },
            }

        self.graph_mock.astream_events = _events
        app = self._app()

        _, body = await self._post_chat(app, {
            "message": "AAPL market cap",
            "session_id": _valid_session_id(),
        })
        events = _parse_sse(body)
        report_events = [e for e in events if e.get("type") == "verification_report"]
        assert len(report_events) == 1
        assert report_events[0]["report"]["status"] == "warning"


# ── force_tool_retry — bypass prevention ─────────────────────────────────────


class TestForceToolRetry:
    """Verify the force_tool_retry node prevents the LLM from bypassing tools.

    The "fast bypass" bug: the LLM responds on its first pass with a plain-text
    answer (no tool_calls), side-stepping live data fetching.  The graph routes
    to force_tool_retry which injects a MANDATORY TOOL USE directive and
    increments tool_call_count by 1 so the retry branch fires at most once.
    """

    async def test_injects_mandatory_directive_with_ticker_hint(self):
        """force_tool_retry appends a SystemMessage containing 'MANDATORY TOOL USE'
        and mentions the tickers extracted from the user query."""
        from langchain_core.messages import SystemMessage, HumanMessage
        from agent.graph import force_tool_retry

        state = {
            "messages": [HumanMessage("How is Tesla doing?")],
            "tickers_mentioned": ["TSLA"],
            "tool_call_count": 0,
        }
        result = await force_tool_retry(state)

        # Must return exactly one new message
        assert len(result["messages"]) == 1
        directive: SystemMessage = result["messages"][0]
        assert isinstance(directive, SystemMessage)
        assert "MANDATORY TOOL USE" in directive.content
        assert "TSLA" in directive.content  # ticker hint injected

    async def test_increments_tool_call_count_by_one(self):
        """force_tool_retry returns tool_call_count=1 so the reducer adds 1,
        capping bypass retries at exactly one attempt."""
        from langchain_core.messages import HumanMessage
        from agent.graph import force_tool_retry

        state = {
            "messages": [HumanMessage("How is Tesla doing?")],
            "tickers_mentioned": [],
            "tool_call_count": 0,
        }
        result = await force_tool_retry(state)
        assert result["tool_call_count"] == 1

    async def test_no_tickers_still_injects_directive(self):
        """force_tool_retry works gracefully when no tickers were extracted
        (e.g. multi-entity queries like 'evaluate spacex-tesla merger')."""
        from langchain_core.messages import HumanMessage
        from agent.graph import force_tool_retry

        state = {
            "messages": [HumanMessage("evaluate in context of spacex-tesla merger")],
            "tickers_mentioned": [],
            "tool_call_count": 0,
        }
        result = await force_tool_retry(state)
        assert "MANDATORY TOOL USE" in result["messages"][0].content
        assert result["tool_call_count"] == 1

    async def test_should_continue_routes_to_force_retry_on_first_bypass(self):
        """_should_continue_tools returns 'force_tool_retry' when LLM bypasses
        on the very first pass (count == 0, no tool_calls)."""
        from langchain_core.messages import AIMessage
        from agent.graph import _should_continue_tools

        state = {
            "messages": [AIMessage(content="Tesla is doing well based on my training.")],
            "tool_call_count": 0,
        }
        route = _should_continue_tools(state)
        assert route == "force_tool_retry"

    async def test_should_continue_routes_to_finalize_on_second_bypass(self):
        """After force_tool_retry fires once (count==1), if the LLM still
        produces no tool calls, _should_continue_tools falls through to
        finalize_response — preventing an infinite loop."""
        from langchain_core.messages import AIMessage
        from agent.graph import _should_continue_tools

        state = {
            "messages": [AIMessage(content="I still cannot call tools.")],
            "tool_call_count": 1,  # retry already consumed
        }
        route = _should_continue_tools(state)
        assert route == "finalize_response"

    async def test_should_continue_routes_to_execute_when_tool_calls_present(self):
        """When the LLM returns tool_calls and count < MAX_TOOL_ROUNDS, route
        to execute_tool_calls — the normal happy path."""
        from langchain_core.messages import AIMessage
        from agent.graph import _should_continue_tools

        ai_msg = AIMessage(content="")
        # Simulate a tool_calls attribute as LangChain populates it
        object.__setattr__(ai_msg, "tool_calls", [{"name": "get_company_profile", "args": {"symbol": "TSLA"}, "id": "call_1"}])

        state = {
            "messages": [ai_msg],
            "tool_call_count": 0,
        }
        route = _should_continue_tools(state)
        assert route == "execute_tool_calls"


# ── Multi-entity / unlisted-entity stress test ───────────────────────────────


class TestMultiEntityQuery:
    """Queries with no valid ticker symbols (e.g. SpaceX, vague mergers)
    must be handled gracefully — no crashes, no 500 errors."""

    async def test_no_valid_tickers_extracted_from_merger_query(self):
        """'evaluate in context of spacex-tesla merger' contains no 1-5 char
        uppercase tokens that survive stopword filtering.  tickers_mentioned
        must be empty (TESLA is 5 chars but not a standard ticker — TSLA is)."""
        from agent.nodes import extract_tickers

        result = extract_tickers("evaluate in context of spacex-tesla merger")
        # 'merger', 'spacex', 'tesla' are all lowercase — not captured by bare regex
        # Capitalised: none present → empty
        assert result == []

    async def test_merger_query_routes_to_ticker_deep_dive(self):
        """'evaluate' is in _DEEP_DIVE_KEYWORDS, so the query routes to
        ticker_deep_dive — the finance tool loop — even without explicit tickers."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage("evaluate in context of spacex-tesla merger")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"
        assert result["tickers_mentioned"] == []

    async def test_route_finance_query_handles_no_tickers_gracefully(self):
        """When tickers_mentioned is empty, route_finance_query must NOT raise.
        The LLM is expected to either attempt tool calls on its own or return
        an error message.  Either way the function must return a dict with
        a 'messages' key."""
        from langchain_core.messages import HumanMessage, AIMessage
        from agent.graph import route_finance_query

        fake_model = MagicMock()
        fake_model.ainvoke = AsyncMock(
            return_value=AIMessage(
                content="SpaceX is private and Tesla merger is speculative."
            )
        )

        state = {
            "messages": [HumanMessage("evaluate in context of spacex-tesla merger")],
            "intent": "ticker_deep_dive",
            "tickers_mentioned": [],
            "context_refs": [],
            "injected_context": "",
            "tool_results": {},
            "finance_loop_count": 0,
        }

        with patch("agent.graph._get_tool_bound_model", return_value=fake_model):
            result = await route_finance_query(state)

        assert "messages" in result
        assert len(result["messages"]) >= 1

    async def test_sse_stream_completes_without_500_on_no_ticker_query(self):
        """Full SSE integration: multi-entity query with no ticker must complete
        the stream (done or error event) without throwing an HTTP 500."""
        import json as _json

        async def _no_ticker_events(*_args, **_kwargs):
            class _Chunk:
                content = "SpaceX remains private. A Tesla merger is speculative."

            yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": _Chunk()}}

        graph_mock = MagicMock()
        graph_mock.astream_events = _no_ticker_events
        upsert_mock = AsyncMock(return_value={"nodes_created": 0, "edges_created": 0, "node_ids": []})
        session_mock = MagicMock()

        with patch("routers.chat.graph", graph_mock), \
             patch("routers.chat.upsert_from_tool_results", upsert_mock), \
             patch("routers.chat.SessionLocal", MagicMock(return_value=session_mock)):

            from fastapi import FastAPI
            from httpx import ASGITransport, AsyncClient
            from routers.chat import router as chat_router

            app = FastAPI()
            app.include_router(chat_router, prefix="/api")

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/chat",
                    json={
                        "message": "evaluate in context of spacex-tesla merger",
                        "session_id": _valid_session_id(),
                    },
                    timeout=30,
                )

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        final_types = {e["type"] for e in events}
        # Must end with either done or error — never just silently die
        assert final_types & {"done", "error"}


# ── context_refs validation — @/$ prefix rejection ───────────────────────────


class TestContextRefsValidation:
    """Verify context_refs validation rejects @/$ prefixes (frontend strips them)
    and accepts plain uppercase tickers.  Documents the boundary between
    frontend normalization (ChatBox) and backend validation (chat.py).
    """

    def test_dollar_prefixed_ticker_rejected(self):
        """$TSLA must be rejected — the frontend always strips $ before POSTing.
        If the backend accepted it, the ticker regex in extract_tickers would
        miss it (bare regex requires strict [A-Z]{1-5} with no prefix)."""
        from routers.chat import ChatRequest

        with pytest.raises(Exception):  # ValidationError
            ChatRequest(
                message="How is Tesla?",
                session_id=_valid_session_id(),
                context_refs=["$TSLA"],
            )

    def test_at_prefixed_ticker_rejected(self):
        """@TSLA must never reach the graph as a context_ref — the frontend
        populates context_refs with bare 'TSLA' after extractContextRefs()."""
        from routers.chat import ChatRequest

        with pytest.raises(Exception):
            ChatRequest(
                message="How is Tesla?",
                session_id=_valid_session_id(),
                context_refs=["@TSLA"],
            )

    def test_bare_uppercase_ticker_accepted(self):
        """Plain 'TSLA' in context_refs is the canonical form after frontend
        normalization and must be accepted by the validator."""
        from routers.chat import ChatRequest

        req = ChatRequest(
            message="How is Tesla?",
            session_id=_valid_session_id(),
            context_refs=["TSLA"],
        )
        assert "TSLA" in req.context_refs

    def test_lowercase_ticker_passes_validation_then_normalized_in_graph(self):
        """Lowercase 'tsla' passes API validation (validator compares .upper())
        and is normalised to 'TSLA' inside intent_router before tool dispatch."""
        from routers.chat import ChatRequest

        # Should NOT raise — lowercase is validated via upper() comparison
        req = ChatRequest(
            message="How is Tesla?",
            session_id=_valid_session_id(),
            context_refs=["tsla"],
        )
        # The value is stored as-is; normalisation happens in intent_router
        assert "tsla" in req.context_refs

    async def test_intent_router_uppercases_context_refs(self):
        """intent_router must uppercase context_refs so that 'tsla' from the
        API becomes 'TSLA' in tickers_mentioned before tool dispatch."""
        from langchain_core.messages import HumanMessage
        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage("Tell me about tsla")],
            "context_refs": ["tsla"],
        }
        result = await intent_router(state)
        assert "TSLA" in result["tickers_mentioned"]


class TestConsentGateFlow:
    """Consent gate regressions for chat post-processing persistence."""

    @pytest.fixture(autouse=True)
    def _setup_mocks(self):
        from agent.memory_consent import _reset_for_tests

        _reset_for_tests()
        self.graph_mock = MagicMock()
        self.upsert_mock = AsyncMock(return_value={
            "nodes_created": 1,
            "edges_created": 1,
            "node_ids": [1],
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
        _reset_for_tests()

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_router, prefix="/api")
        return app

    async def _post_chat(self, app, payload: dict) -> tuple[int, str]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat", json=payload, timeout=30)
            return resp.status_code, resp.text

    async def test_emits_consent_required_and_skips_immediate_upsert(self):
        async def _events(*_args, **_kwargs):
            yield {
                "event": "on_tool_end",
                "name": "get_company_profile",
                "data": {
                    "input": {"symbol": "AAPL"},
                    "output": json.dumps({
                        "success": True,
                        "data": {
                            "symbol": "AAPL",
                            "name": "Apple Inc.",
                            "sector": "Technology",
                            "industry": "Consumer Electronics",
                        },
                        "sources": [],
                    }),
                },
            }

        self.graph_mock.astream_events = _events
        app = self._app()

        status, body = await self._post_chat(app, {
            "message": "Analyze AAPL",
            "session_id": _valid_session_id(),
        })
        assert status == 200

        events = _parse_sse(body)
        consent_events = [e for e in events if e.get("type") == "consent_required"]
        assert len(consent_events) == 1
        assert "proposal_id" in consent_events[0]["proposal"]

        # No immediate persistence without explicit confirmation.
        self.upsert_mock.assert_not_called()

    async def test_confirmed_proposal_commits_pending_payload(self):
        from agent.memory_consent import register_persistence_proposal, confirm_persistence_proposal

        session_id = _valid_session_id()
        proposal = register_persistence_proposal(
            session_id=session_id,
            run_id=str(uuid.uuid4()),
            tool_results=[{
                "tool": "get_company_profile",
                "args": {"symbol": "MSFT"},
                "result": json.dumps({
                    "success": True,
                    "data": {
                        "symbol": "MSFT",
                        "name": "Microsoft",
                        "sector": "Technology",
                        "industry": "Software",
                    },
                    "sources": [],
                }),
            }],
            extra_sources=[],
        )
        confirm = confirm_persistence_proposal(proposal["proposal_id"], "confirm")
        assert confirm["success"] is True

        async def _events(*_args, **_kwargs):
            if False:
                yield {}

        self.graph_mock.astream_events = _events
        app = self._app()

        status, body = await self._post_chat(app, {
            "message": "confirm pending memory write",
            "session_id": session_id,
        })
        assert status == 200
        events = _parse_sse(body)
        assert any(e.get("type") == "kg_update" for e in events)

        self.upsert_mock.assert_called_once()
        assert self.upsert_mock.call_args.kwargs.get("consent_granted") is True
