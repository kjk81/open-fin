from __future__ import annotations
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    # Full conversation messages — add_messages reducer appends rather than replaces
    messages: Annotated[list[BaseMessage], add_messages]

    # "general_chat" | "trade_recommendation" | "ticker_deep_dive" | "stock_screening"
    intent: str

    # Upper-case ticker symbols extracted from the user message, e.g. ["AAPL", "NVDA"]
    tickers_mentioned: list[str]

    # Named context sources to inject, e.g. ["user_portfolio"]
    context_refs: list[str]

    # Formatted string injected into the system prompt (portfolio rows, etc.)
    injected_context: str

    # {symbol: report_text} built by TickerLookupNode
    ticker_reports: dict[str, str]

    # Identifies the chat session for history lookup and persistence
    session_id: str

    # Alert context from the anomaly trigger pipeline (symbol + signal summary)
    anomaly_context: str

    # ScreeningResult data for generation node consumption
    screening_results: dict
