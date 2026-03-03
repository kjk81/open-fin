from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from unittest.mock import AsyncMock

from agent.graph import _route_after_context, execute_tool_calls
from agent.nodes import intent_router


@pytest.mark.asyncio
async def test_research_apple_routes_to_search_web(monkeypatch):
    state = {
        "messages": [HumanMessage(content="research Apple")],
        "context_refs": [],
    }
    routed = await intent_router(state)

    assert routed["intent"] == "ticker_deep_dive"
    assert _route_after_context({**routed, "agent_mode": "research", "messages": []}) == "route_finance_query"

    search_tool = AsyncMock(return_value=json.dumps({"success": True, "data": [{"title": "Apple"}], "sources": []}))
    monkeypatch.setitem(__import__("agent.graph", fromlist=["_TOOL_MAP"])._TOOL_MAP, "search_web", search_tool)

    exec_state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "search_web", "args": {"query": "Apple"}, "id": "call-r1"}])],
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
    result = await execute_tool_calls(exec_state)

    assert result["tool_results"]
    assert result["tool_results"][0]["tool"] == "search_web"


@pytest.mark.asyncio
async def test_portfolio_performance_routes_to_local_tool(monkeypatch):
    state = {
        "messages": [HumanMessage(content="portfolio performance")],
        "context_refs": [],
    }
    routed = await intent_router(state)

    assert routed["intent"] == "ticker_deep_dive"
    assert "user_portfolio" in routed["context_refs"]
    assert _route_after_context({**routed, "agent_mode": "quick", "messages": []}) == "route_finance_query"

    local_tool = AsyncMock(return_value=json.dumps({"success": True, "data": {"symbol": "AAPL"}, "sources": []}))
    monkeypatch.setitem(__import__("agent.graph", fromlist=["_TOOL_MAP"])._TOOL_MAP, "get_company_profile", local_tool)

    exec_state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "get_company_profile", "args": {"ticker": "AAPL"}, "id": "call-p1"}])],
        "tool_call_count": 0,
        "executed_skills": [],
        "tool_results": [],
        "citations": [],
        "confirmed_tokens": [],
        "pending_actions": [],
        "agent_mode": "quick",
        "start_time_utc": None,
        "run_id": "",
    }
    result = await execute_tool_calls(exec_state)

    assert result["tool_results"]
    assert result["tool_results"][0]["tool"] == "get_company_profile"
    assert all(entry["tool"] != "search_web" for entry in result["tool_results"])
