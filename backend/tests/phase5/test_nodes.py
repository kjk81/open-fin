"""Phase 5 — Tests for agent/nodes.py (intent_router, context_injector)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest


def _make_chat_state(text: str, context_refs: list[str] | None = None):
    """Build a minimal ChatState dict with a single HumanMessage."""
    msg = MagicMock()
    msg.content = text
    # isinstance check in intent_router uses HumanMessage
    msg.__class__ = MagicMock()
    msg.__class__.__name__ = "HumanMessage"
    return {
        "messages": [msg],
        "context_refs": context_refs or [],
    }


# Patch HumanMessage isinstance check
def _is_human(msg):
    return getattr(msg.__class__, "__name__", "") == "HumanMessage"


class TestIntentRouter:
    async def test_trade_intent(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("Should I buy AAPL?")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert result["intent"] == "trade_recommendation"
        assert "AAPL" in result["tickers_mentioned"]

    async def test_deep_dive_intent(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("Give me a deep dive analysis of NVDA")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert result["intent"] == "ticker_deep_dive"
        assert "NVDA" in result["tickers_mentioned"]

    async def test_screening_intent(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("Screen stocks with low PE ratio")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert result["intent"] == "stock_screening"

    async def test_sec_filings_intent(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("Show me the 10-K risk factors for TSLA")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert result["intent"] == "sec_filings"
        assert "TSLA" in result["tickers_mentioned"]

    async def test_general_chat_fallback(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("What is inflation?")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert result["intent"] == "general_chat"

    async def test_ticker_stopwords_filtered(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("I AM an AI that tracks MSFT")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        tickers = result["tickers_mentioned"]
        assert "MSFT" in tickers
        for stopword in ["I", "AM", "AI"]:
            assert stopword not in tickers

    async def test_portfolio_context_added(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("Show my portfolio")], "context_refs": []}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert "user_portfolio" in result["context_refs"]

    async def test_context_ref_tickers_extracted(self):
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage("analyze this")], "context_refs": ["GOOG"]}
        from agent.nodes import intent_router
        result = await intent_router(state)
        assert "GOOG" in result["tickers_mentioned"]


class TestContextInjector:
    async def test_no_portfolio_ref(self):
        """When user_portfolio is not in context_refs, returns empty."""
        state = {"context_refs": []}
        from agent.nodes import context_injector
        result = await context_injector(state)
        assert result["injected_context"] == ""

    async def test_with_portfolio_no_positions(self):
        """When portfolio is empty, returns informative message."""
        state = {"context_refs": ["user_portfolio"]}
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        with patch("database.SessionLocal", return_value=mock_db):
            from agent.nodes import context_injector
            result = await context_injector(state)

        assert "no open" in result["injected_context"].lower()


class TestFormatFundamentals:
    def test_basic_formatting(self):
        from agent.nodes import _format_fundamentals
        info = {
            "longName": "Apple Inc",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "currentPrice": 195.50,
            "marketCap": 3_000_000_000_000,
            "trailingPE": 28.5,
        }
        result = _format_fundamentals("AAPL", info)
        assert "AAPL" in result
        assert "Apple Inc" in result
        assert "Technology" in result
        assert "$195.50" in result

    def test_empty_info(self):
        from agent.nodes import _format_fundamentals
        result = _format_fundamentals("TEST", {})
        assert "TEST" in result
