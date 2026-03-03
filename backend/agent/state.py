from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    """Legacy chat state — kept for backward-compatible nodes (generation_node, etc.)."""

    # Full conversation messages — add_messages reducer appends rather than replaces
    messages: Annotated[list[BaseMessage], add_messages]

    # "general_chat" | "trade_recommendation" | "ticker_deep_dive" | "stock_screening" | "sec_filings"
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

    # SEC filings extraction context for generation node consumption
    filings_context: str


class AgentState(TypedDict):
    """Core state for the LangGraph decision graph.

    Extends the original ChatState fields with tool-loop tracking for the
    ReAct-style finance agent that replaced Dexter's CLI orchestrator.
    """

    # -- LangGraph message channel (add_messages reducer appends) --
    messages: Annotated[list[BaseMessage], add_messages]

    # The raw user query text extracted once at the start of the finance path
    current_query: str

    # Tool names currently bound to the LLM for this session
    active_skills: list[str]

    # Incremented by 1 each tool-execution round; operator.add reducer sums
    tool_call_count: Annotated[int, operator.add]

    # Accumulated ToolResult payloads from execute_tool_calls (operator.add concatenates)
    tool_results: Annotated[list[dict], operator.add]

    # Skill names that have already been executed in this session
    executed_skills: Annotated[list[str], operator.add]

    # -- Fields carried over from ChatState for backward compatibility --
    intent: str
    tickers_mentioned: list[str]
    context_refs: list[str]
    injected_context: str
    ticker_reports: dict[str, str]
    session_id: str
    anomaly_context: str
    screening_results: dict
    filings_context: str

    # Agent mode: "genie" | "fundamentals" | "sentiment" | "technical"
    # Written once at start — no reducer needed.
    agent_mode: str

    # UUID of the persisted AgentRun record for this invocation.
    # Written once at stream start — no reducer needed.
    run_id: str
