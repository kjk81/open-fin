"""Phase 4 — Unified Action Boundary, Confirmation Gate, and Transaction Logging."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from agent import graph as graph_mod


def _make_base_state(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a minimal AgentState-like dict for execute_tool_calls."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "messages": [graph_mod.AIMessage(content="", tool_calls=tool_calls)],
        "current_query": "test query",
        "active_skills": [],
        "tool_call_count": 0,
        "tool_results": [],
        "citations": [],
        "executed_skills": [],
        "intent": "trade_recommendation",
        "tickers_mentioned": ["AAPL"],
        "context_refs": [],
        "injected_context": "",
        "ticker_reports": {},
        "session_id": "test-session",
        "anomaly_context": "",
        "screening_results": {},
        "filings_context": "",
        "agent_mode": "portfolio",
        "start_time_utc": now,
        "external_call_count": 0,
        "capabilities": {},
        "degradation_events": [],
        "tool_loop_terminated_reason": "",
        "run_id": "run-123",
        "verification_report": {},
        "verification_status": "",
        "verification_failure_reason": "",
        "tiebreaker_attempt_count": 0,
        "verification_disclaimer_used": False,
        "pending_memory_write": False,
        "memory_consent_status": "none",
        "memory_write_proposal": {},
        "pending_actions": [],
        "confirmed_tokens": [],
        "confirmation_pending": False,
    }


class _DummyTool:
    def __init__(self, result: dict[str, Any]):
        self._result = result
        self.called_with: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> str:
        self.called_with.append(args)
        return json.dumps(self._result)


@pytest.mark.asyncio
async def test_write_block_unconfirmed_action(monkeypatch):
    """Non-READ_ONLY tools must not execute until confirmed; graph should pause."""
    # Patch AIMessage / ToolMessage with lightweight test doubles so execute_tool_calls
    # isinstance checks and message construction behave deterministically in tests.
    class _TestAIMessage:
        def __init__(
            self,
            content: str,
            tool_calls: list[dict[str, Any]] | None = None,
            **_: Any,
        ):
            self.content = content
            self.tool_calls = tool_calls or []

    class _TestToolMessage:
        def __init__(self, content: str, tool_call_id: str):
            self.content = content
            self.tool_call_id = tool_call_id

    monkeypatch.setattr(graph_mod, "AIMessage", _TestAIMessage)
    monkeypatch.setattr(graph_mod, "ToolMessage", _TestToolMessage)
    # Replace handler with a dummy that would fail the test if called.
    dummy = _DummyTool({"success": True, "data": {}, "error": None})
    monkeypatch.setitem(graph_mod._TOOL_MAP, "execute_trade", dummy)

    tool_calls = [
        {
            "name": "execute_trade",
            "args": {"ticker": "AAPL", "action": "BUY", "qty": 10},
            "id": "call-1",
        }
    ]
    state = _make_base_state(tool_calls)

    result = await graph_mod.execute_tool_calls(state)

    # Tool should not have been invoked yet.
    assert dummy.called_with == []

    # Should emit an awaiting_confirmation ToolMessage.
    assert result["messages"], "expected at least one ToolMessage"
    msg = result["messages"][0]
    payload = json.loads(msg.content)
    assert payload["status"] == "awaiting_confirmation"
    assert "action_id" in payload

    # pending_actions should contain the preview for the non-READ_ONLY tool.
    pending = result["pending_actions"]
    assert len(pending) == 1
    preview = pending[0]
    assert preview["tool"] == "execute_trade"

    # confirmation_gate should detect the unconfirmed action and pause the graph.
    next_state = {**state, **result}
    gate_delta = await graph_mod.confirmation_gate(next_state)
    assert gate_delta["confirmation_pending"] is True

    routed = graph_mod._route_after_confirmation({**next_state, **gate_delta})
    assert routed == "END"


@pytest.mark.asyncio
async def test_transaction_logging_only_on_success(monkeypatch):
    """_log_state_write should be called only for successful non-READ_ONLY tools."""
    success_result = {"success": True, "data": {"ok": True}, "error": None}
    fail_result = {"success": False, "data": None, "error": "boom"}

    # Deterministic action_id must match execute_tool_calls hashing scheme.
    args = {"ticker": "AAPL", "action": "BUY", "qty": 5}
    deterministic_id = hashlib.sha256(
        json.dumps({"tool": "execute_trade", "args": args}, sort_keys=True).encode()
    ).hexdigest()[:16]

    # Patch AIMessage / ToolMessage with lightweight test doubles.
    class _TestAIMessage:
        def __init__(
            self,
            content: str,
            tool_calls: list[dict[str, Any]] | None = None,
            **_: Any,
        ):
            self.content = content
            self.tool_calls = tool_calls or []

    class _TestToolMessage:
        def __init__(self, content: str, tool_call_id: str):
            self.content = content
            self.tool_call_id = tool_call_id

    monkeypatch.setattr(graph_mod, "AIMessage", _TestAIMessage)
    monkeypatch.setattr(graph_mod, "ToolMessage", _TestToolMessage)

    # Successful path
    success_tool = _DummyTool(success_result)
    monkeypatch.setitem(graph_mod._TOOL_MAP, "execute_trade", success_tool)

    logged_calls: list[dict[str, Any]] = []

    def _fake_log_state_write(**kwargs: Any) -> None:
        logged_calls.append(kwargs)

    monkeypatch.setattr(graph_mod, "_log_state_write", _fake_log_state_write)

    tool_calls = [
        {
            "name": "execute_trade",
            "args": args,
            "id": "call-success",
        }
    ]
    state = _make_base_state(tool_calls)
    # Simulate user confirmation by injecting the deterministic action_id.
    state["confirmed_tokens"] = [deterministic_id]

    await graph_mod.execute_tool_calls(state)

    assert logged_calls, "expected state_write to be logged for successful write"
    last = logged_calls[-1]
    assert last["tool_name"] == "execute_trade"
    assert last["args"] == args
    assert last["tool_call_id"] == "call-success"

    # Failure path should not log a state_write event.
    logged_calls.clear()
    fail_tool = _DummyTool(fail_result)
    monkeypatch.setitem(graph_mod._TOOL_MAP, "execute_trade", fail_tool)

    tool_calls_fail = [
        {
            "name": "execute_trade",
            "args": args,
            "id": "call-fail",
        }
    ]
    state_fail = _make_base_state(tool_calls_fail)
    state_fail["confirmed_tokens"] = [deterministic_id]

    await graph_mod.execute_tool_calls(state_fail)

    assert logged_calls == [], "state_write must not be logged for failed tool calls"

