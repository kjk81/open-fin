"""Unit tests for prompt generation and intent routing.

Validates:
- get_router_soul_prompt() includes current date, Finneas identity, and
  strict tool-enforcement language.
- intent_router correctly handles @TICKER / $TICKER prefixes.
- Performance-keyword queries with tickers route to ticker_deep_dive.
- Conditional edge functions route finance intents correctly.
"""

from __future__ import annotations

from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Prompt content tests
# ---------------------------------------------------------------------------


class TestRouterSoulPrompt:
    def test_includes_current_date(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        today = datetime.now().strftime("%B %d, %Y")
        assert today in prompt, f"Expected '{today}' in router prompt"

    def test_includes_day_of_week(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        today_full = datetime.now().strftime("%A, %B %d, %Y")
        assert today_full in prompt

    def test_includes_finneas_identity(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        assert "Finneas" in prompt

    def test_includes_tool_enforcement_never_answer(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        # The enforcement block must forbid answering from training data
        assert "MUST NEVER" in prompt or "NEVER answer" in prompt

    def test_includes_stale_training_data_warning(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        assert "STALE" in prompt

    def test_includes_single_call_policy(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        assert "SINGLE-CALL POLICY" in prompt

    def test_includes_skills_advertisement(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        assert "load_skill" in prompt

    def test_includes_entity_recognition_hint(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        # Must guide the model to treat @/$ prefixes as tickers
        assert "@TICKER" in prompt or "@ mention" in prompt.lower() or "@" in prompt

    def test_is_non_empty_string(self) -> None:
        from agent.prompts import get_router_soul_prompt

        prompt = get_router_soul_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 500


class TestFinalizePrompt:
    def test_includes_current_date(self) -> None:
        from agent.prompts import get_finalize_prompt

        prompt = get_finalize_prompt()
        today = datetime.now().strftime("%B %d, %Y")
        assert today in prompt

    def test_includes_finneas_identity(self) -> None:
        from agent.prompts import get_finalize_prompt

        prompt = get_finalize_prompt()
        assert "Finneas" in prompt

    def test_includes_synthesis_instruction(self) -> None:
        from agent.prompts import get_finalize_prompt

        prompt = get_finalize_prompt()
        assert "Synthesise" in prompt or "synthesise" in prompt.lower()

    def test_is_non_empty_string(self) -> None:
        from agent.prompts import get_finalize_prompt

        prompt = get_finalize_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 200


class TestGenerationPrompt:
    def test_includes_current_date(self) -> None:
        from agent.prompts import get_generation_prompt

        prompt = get_generation_prompt()
        today = datetime.now().strftime("%B %d, %Y")
        assert today in prompt

    def test_includes_finneas_identity(self) -> None:
        from agent.prompts import get_generation_prompt

        prompt = get_generation_prompt()
        assert "Finneas" in prompt

    def test_is_non_empty_string(self) -> None:
        from agent.prompts import get_generation_prompt

        prompt = get_generation_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 200


# ---------------------------------------------------------------------------
# IntentRouter with @ and $ prefixes
# ---------------------------------------------------------------------------


class TestIntentRouterPrefixes:
    """Verify that @TICKER and $TICKER mentions are correctly extracted."""

    async def test_at_prefix_extracted(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="What do you think about @RBLX?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert "RBLX" in result["tickers_mentioned"]

    async def test_dollar_prefix_extracted(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="Is $NVDA a good buy right now?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert "NVDA" in result["tickers_mentioned"]

    async def test_at_rblx_not_unknown(self) -> None:
        """Core regression test: @RBLX must not be dropped as an unknown entity."""
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="How did @RBLX perform recently?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        # Must be extracted exactly as RBLX (no @ prefix, no empty string)
        assert "RBLX" in result["tickers_mentioned"]
        assert "@RBLX" not in result["tickers_mentioned"]

    async def test_mixed_prefixes_all_extracted(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="Compare @RBLX $AAPL and TSLA")],
            "context_refs": [],
        }
        result = await intent_router(state)
        for ticker in ("RBLX", "AAPL", "TSLA"):
            assert ticker in result["tickers_mentioned"], (
                f"{ticker} not found in {result['tickers_mentioned']}"
            )

    async def test_no_duplicates_from_mixed_prefixes(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="@AAPL $AAPL AAPL three times")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["tickers_mentioned"].count("AAPL") == 1


# ---------------------------------------------------------------------------
# Performance keyword routing
# ---------------------------------------------------------------------------


class TestPerformanceKeywordRouting:
    """Queries about price / performance with tickers must route to tools."""

    async def test_how_is_doing_routes_to_deep_dive(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="How is @RBLX doing today?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"
        assert "RBLX" in result["tickers_mentioned"]

    async def test_price_query_routes_to_deep_dive(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="What is the current price of TSLA?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_performance_keyword_routes_to_deep_dive(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="What's the performance of $NVDA this week?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_trend_keyword_routes_to_deep_dive(self) -> None:
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="AAPL trend this month")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_no_ticker_performance_stays_general(self) -> None:
        """Performance keywords alone (no tickers) must not force deep_dive."""
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [HumanMessage(content="How is the market doing today?")],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "general_chat"

    async def test_explicit_deep_dive_keywords_take_priority(self) -> None:
        """Explicit deep-dive words must take priority over performance catch."""
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [
                HumanMessage(content="Give me a deep dive analysis on $AAPL price")
            ],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"

    async def test_trade_intent_takes_priority_over_performance(self) -> None:
        """Trade keywords must take priority over performance keywords."""
        from langchain_core.messages import HumanMessage

        from agent.nodes import intent_router

        state = {
            "messages": [
                HumanMessage(content="Should I buy @RBLX today given the price?")
            ],
            "context_refs": [],
        }
        result = await intent_router(state)
        assert result["intent"] == "trade_recommendation"

# ---------------------------------------------------------------------------
# _should_continue_tools conditional edge
# ---------------------------------------------------------------------------


class TestShouldContinueTools:
    """Unit tests for the _should_continue_tools conditional edge."""

    def _ai(self, with_tool_calls: bool = False):
        """Build a lightweight AIMessage with or without tool_calls."""
        from agent.graph import _should_continue_tools  # noqa: F401 (trigger import)
        import sys
        AIMessage = sys.modules["langchain_core.messages"].AIMessage
        msg = AIMessage(content="some response")
        if with_tool_calls:
            msg.tool_calls = [{"name": "get_company_profile", "args": {}, "id": "t1"}]
        else:
            msg.tool_calls = []
        return msg

    def test_routes_force_retry_on_first_no_tool_calls(self) -> None:
        """No tool calls on the first pass (count==0) routes to force_tool_retry."""
        from agent.graph import _should_continue_tools

        state = {
            "messages": [self._ai(with_tool_calls=False)],
            "tool_call_count": 0,
            "intent": "ticker_deep_dive",
            "tickers_mentioned": ["GOOG"],
        }
        assert _should_continue_tools(state) == "force_tool_retry"

    def test_finalizes_after_forced_retry_no_tools(self) -> None:
        """No tool calls after force_tool_retry (count==1) falls through to finalize."""
        from agent.graph import _should_continue_tools

        state = {
            "messages": [self._ai(with_tool_calls=False)],
            "tool_call_count": 1,  # already consumed by force_tool_retry
            "intent": "ticker_deep_dive",
        }
        assert _should_continue_tools(state) == "finalize_response"

    def test_routes_execute_when_tool_calls_present_and_under_limit(self) -> None:
        from agent.graph import _should_continue_tools

        state = {
            "messages": [self._ai(with_tool_calls=True)],
            "tool_call_count": 0,
        }
        assert _should_continue_tools(state) == "execute_tool_calls"

    def test_finalizes_at_max_rounds_even_with_tool_calls(self) -> None:
        from agent.graph import _should_continue_tools, MAX_TOOL_ROUNDS

        state = {
            "messages": [self._ai(with_tool_calls=True)],
            "tool_call_count": MAX_TOOL_ROUNDS,
        }
        assert _should_continue_tools(state) == "finalize_response"

    def test_finalizes_with_empty_messages_list(self) -> None:
        """Edge case: no messages at all → falls through to finalize."""
        from agent.graph import _should_continue_tools

        state: dict = {"messages": [], "tool_call_count": 2}
        assert _should_continue_tools(state) == "finalize_response"

    def test_force_retry_not_repeated_on_mid_count(self) -> None:
        """count==2 with no tool calls must finalize, not loop through force_tool_retry."""
        from agent.graph import _should_continue_tools

        state = {
            "messages": [self._ai(with_tool_calls=False)],
            "tool_call_count": 2,
        }
        assert _should_continue_tools(state) == "finalize_response"


class TestForceToolRetryNode:
    """Unit tests for the force_tool_retry node function."""

    async def test_appends_system_message(self) -> None:
        import sys
        from agent.graph import force_tool_retry

        SystemMessage = sys.modules["langchain_core.messages"].SystemMessage

        state = {
            "messages": [],
            "tool_call_count": 0,
            "tickers_mentioned": ["AAPL"],
        }
        result = await force_tool_retry(state)
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], SystemMessage)

    async def test_sets_tool_call_count_to_1(self) -> None:
        from agent.graph import force_tool_retry

        state = {"messages": [], "tool_call_count": 0, "tickers_mentioned": []}
        result = await force_tool_retry(state)
        assert result["tool_call_count"] == 1

    async def test_includes_ticker_hint_in_directive(self) -> None:
        from agent.graph import force_tool_retry

        state = {
            "messages": [],
            "tool_call_count": 0,
            "tickers_mentioned": ["TSLA", "NVDA"],
        }
        result = await force_tool_retry(state)
        content = result["messages"][0].content
        assert "TSLA" in content
        assert "NVDA" in content

    async def test_no_hint_when_no_tickers(self) -> None:
        from agent.graph import force_tool_retry

        state = {"messages": [], "tool_call_count": 0, "tickers_mentioned": []}
        result = await force_tool_retry(state)
        content = result["messages"][0].content
        # Generic directive — no ticker-specific mention
        assert "MANDATORY TOOL USE" in content

    async def test_directive_mentions_live_data(self) -> None:
        """The directive must reference live/tool use — not training data."""
        from agent.graph import force_tool_retry

        state = {"messages": [], "tool_call_count": 0, "tickers_mentioned": []}
        result = await force_tool_retry(state)
        content = result["messages"][0].content
        assert "tool" in content.lower() or "TOOL" in content

# ---------------------------------------------------------------------------
# Conditional edge routing
# ---------------------------------------------------------------------------


class TestRouteAfterContext:
    """_route_after_context should send finance intents to the tool loop."""

    def test_trade_recommendation_routes_to_finance(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {"intent": "trade_recommendation"}
        assert _route_after_context(state) == "route_finance_query"

    def test_ticker_deep_dive_routes_to_finance(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {"intent": "ticker_deep_dive"}
        assert _route_after_context(state) == "route_finance_query"

    def test_stock_screening_routes_to_finance(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {"intent": "stock_screening"}
        assert _route_after_context(state) == "route_finance_query"

    def test_sec_filings_routes_to_finance(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {"intent": "sec_filings"}
        assert _route_after_context(state) == "route_finance_query"

    def test_general_chat_routes_to_generation(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {"intent": "general_chat"}
        assert _route_after_context(state) == "generation_node"

    def test_missing_intent_defaults_to_generation(self) -> None:
        from agent.graph import _route_after_context

        state: dict = {}
        assert _route_after_context(state) == "generation_node"
