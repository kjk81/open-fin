"""Phase 2 — Tests for agent modes and budget enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class TestQuickModeToolPolicy:
    def test_quick_mode_never_allows_web_tools(self) -> None:
        """Quick mode must not surface broad web tools even when capabilities are healthy."""
        from agent.modes import get_mode_policy
        from agent.graph import _apply_mode_tool_policy

        mode_policy = get_mode_policy("quick")

        tools = [
            _FakeTool("get_company_profile"),
            _FakeTool("get_ohlcv"),
            _FakeTool("search_web"),
            _FakeTool("fetch_webpage"),
            _FakeTool("get_social_sentiment"),
        ]

        capabilities = {
            "internet_dns_ok": True,
            "fmp_api_key_present": True,
            "sec_api_key_present": True,
        }

        allowed_tools, disabled = _apply_mode_tool_policy(
            mode_policy=mode_policy,
            tools=tools,
            capabilities=capabilities,
        )

        allowed_names = {t.name for t in allowed_tools}
        assert "search_web" not in allowed_names
        assert "fetch_webpage" not in allowed_names
        assert "get_social_sentiment" not in allowed_names

        # Sanity check: core finance tools from the quick allow-list remain usable.
        assert "get_company_profile" in allowed_names
        assert "get_ohlcv" in allowed_names

        # Disabled set may record capability-related reasons for some tools,
        # but quick mode's allow-list alone must be sufficient to keep
        # web-search-style tools out of the allowed set.


class TestMaxSecondsBudget:
    def test_time_budget_enforced_for_quick_mode(self) -> None:
        """When elapsed time exceeds max_seconds, _should_continue_tools must finalize."""
        from agent.graph import _should_continue_tools
        import sys

        # Simulate a run that started well before the quick mode 10s budget.
        start = datetime.now(timezone.utc) - timedelta(seconds=20)

        # Build an AIMessage instance compatible with the MagicMock-based
        # langchain_core.messages stub used in Phase 2 tests.
        AIMessage = sys.modules["langchain_core.messages"].AIMessage
        msg = AIMessage(content="tool response")
        msg.tool_calls = [{"name": "get_company_profile", "args": {}, "id": "t1"}]

        state = {
            "messages": [msg],
            "tool_call_count": 0,
            "agent_mode": "quick",
            "start_time_utc": start.isoformat(),
        }

        # Even though we are under MAX_TOOL_ROUNDS and have tool_calls present,
        # the elapsed time must cause an immediate finalize.
        assert _should_continue_tools(state) == "finalize_response"

