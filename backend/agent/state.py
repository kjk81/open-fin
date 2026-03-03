from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from .modes import AgentMode


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

    # Aggregated citation refs collected during tool execution (operator.add concatenates)
    citations: Annotated[list[dict], operator.add]

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

    # Agent mode: "quick" | "research" | "portfolio" | "strategy"
    # Written once at start — no reducer needed.
    agent_mode: AgentMode

    # UTC timestamp captured when the agent run starts.
    # Used to derive elapsed wall-clock budget for mode policy checks.
    start_time_utc: str

    # Number of external/network-heavy tool invocations observed for this run.
    # Increment semantics are handled by graph/tool nodes.
    external_call_count: Annotated[int, operator.add]

    # Point-in-time capability snapshot (health flags + per-check timestamps).
    capabilities: dict[str, Any]

    # Capability/mode degradation details emitted by policy checks.
    degradation_events: Annotated[list[dict[str, Any]], operator.add]

    # Optional terminal reason used to short-circuit tool loop and finalize.
    tool_loop_terminated_reason: str

    # UUID of the persisted AgentRun record for this invocation.
    # Written once at stream start — no reducer needed.
    run_id: str

    # Structured reconciliation/verification artifact emitted by verification_gate.
    verification_report: dict[str, Any]

    # verification_gate terminal status: "pass" | "warning" | "critical".
    verification_status: str

    # Human-readable reason when verification reports a critical failure.
    verification_failure_reason: str

    # One-shot tiebreaker loop counter (add reducer prevents infinite retries).
    tiebreaker_attempt_count: Annotated[int, operator.add]

    # True when graph falls back to a disclaimed response after failed tiebreak.
    verification_disclaimer_used: bool

    # True when persistence should pause for explicit user confirmation.
    pending_memory_write: bool

    # Consent gate status: "none" | "pending" | "confirmed" | "discarded".
    memory_consent_status: str

    # Structured proposal metadata emitted by memory_consent_gate.
    memory_write_proposal: dict[str, Any]

    # -- Action Registry fields (Phase 4 safety schema) ----------------------

    # Non-READ_ONLY action previews accumulated across tool rounds.
    # operator.add appends lists; nodes that emit no actions return [].
    pending_actions: Annotated[list[dict[str, Any]], operator.add]

    # Confirmed action_id tokens from the UI or a future consent gate node.
    # operator.add appends across rounds; initialized empty.
    confirmed_tokens: Annotated[list[str], operator.add]

    # True when confirmation_gate is holding unconfirmed non-READ_ONLY actions.
    # Graph pauses at END and frontend must POST /api/chat/confirm to resume.
    confirmation_pending: bool
