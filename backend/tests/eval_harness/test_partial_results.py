from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from unittest.mock import AsyncMock

from agent.graph import execute_tool_calls
from clients.fmp import FMPUnavailableError


@pytest.mark.asyncio
async def test_execute_tool_calls_continues_with_partial_results_on_timeout(monkeypatch):
    success_tool = SimpleNamespace(
        ainvoke=AsyncMock(return_value=json.dumps({"success": True, "data": [{"ok": True}], "sources": []}))
    )
    timeout_tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=TimeoutError("tool timeout")))

    graph_mod = __import__("agent.graph", fromlist=["_TOOL_MAP"])
    monkeypatch.setitem(graph_mod._TOOL_MAP, "get_company_profile", success_tool)
    monkeypatch.setitem(graph_mod._TOOL_MAP, "search_web", timeout_tool)

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_company_profile", "args": {"ticker": "AAPL"}, "id": "call-ok"},
                    {"name": "search_web", "args": {"query": "AAPL news"}, "id": "call-timeout"},
                ],
            )
        ],
        "tool_call_count": 0,
        "executed_skills": [],
        "tool_results": [],
        "citations": [],
        "confirmed_tokens": [],
        "pending_actions": [],
        "agent_mode": "research",
        "start_time_utc": None,
        "run_id": "",
    }

    result = await execute_tool_calls(state)

    assert len(result["tool_results"]) == 2
    by_tool = {item["tool"]: json.loads(item["result"]) for item in result["tool_results"]}
    assert by_tool["get_company_profile"]["success"] is True
    assert "error" in by_tool["search_web"]
    assert "timeout" in by_tool["search_web"]["error"].lower()


@pytest.mark.asyncio
async def test_execute_tool_calls_emits_standard_error_on_http_500(monkeypatch):
    failing_tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("HTTP 500")))

    graph_mod = __import__("agent.graph", fromlist=["_TOOL_MAP"])
    monkeypatch.setitem(graph_mod._TOOL_MAP, "search_web", failing_tool)

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_web", "args": {"query": "AAPL filing"}, "id": "call-500"},
                ],
            )
        ],
        "tool_call_count": 0,
        "executed_skills": [],
        "tool_results": [],
        "citations": [],
        "confirmed_tokens": [],
        "pending_actions": [],
        "agent_mode": "research",
        "start_time_utc": None,
        "run_id": "",
    }

    result = await execute_tool_calls(state)
    parsed = json.loads(result["tool_results"][0]["result"])

    assert result["tool_results"][0]["tool"] == "search_web"
    assert "error" in parsed
    assert "HTTP 500" in parsed["error"]


@pytest.mark.asyncio
async def test_execute_tool_calls_surfaces_fmp_unavailable_errors(monkeypatch):
    """Tools that raise FMPUnavailableError (401/403/429 at FMP layer) should
    produce a clear error envelope in tool_results, not crash the loop."""

    async def _failing_tool(*_args, **_kwargs):
        raise FMPUnavailableError("FMP unavailable: HTTP 401")

    failing_tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=_failing_tool))

    graph_mod = __import__("agent.graph", fromlist=["_TOOL_MAP"])
    monkeypatch.setitem(graph_mod._TOOL_MAP, "get_company_profile", failing_tool)

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_company_profile", "args": {"ticker": "AAPL"}, "id": "call-fmp"},
                ],
            )
        ],
        "tool_call_count": 0,
        "executed_skills": [],
        "tool_results": [],
        "citations": [],
        "confirmed_tokens": [],
        "pending_actions": [],
        "agent_mode": "research",
        "start_time_utc": None,
        "run_id": "",
    }

    result = await execute_tool_calls(state)
    assert len(result["tool_results"]) == 1

    tr = result["tool_results"][0]
    assert tr["tool"] == "get_company_profile"

    parsed = json.loads(tr["result"])
    assert "error" in parsed
    # Message should reference the upstream FMP failure clearly enough for evals
    assert "FMP unavailable" in parsed["error"] or "FMP" in parsed["error"]
