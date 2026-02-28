"""Open-Fin LangGraph decision graph.

Replaces Dexter's CLI orchestrator with a ReAct-style tool-calling loop.
Financial queries are routed through an LLM with bound tools from
``tools.finance`` and ``tools.sec_filings``.  A hard ceiling of
``MAX_TOOL_ROUNDS`` prevents runaway loops; the ``finalize_response`` node
produces the streamed, user-facing synthesis.

Topology
--------
::

    START
      → intent_router
      → context_injector
      → (route_after_context)
           ├─ finance intent ─→ route_finance_query ─→ (should_continue)
           │                       ↑         ├─ execute ─→ execute_tool_calls ─┘
           │                       │         └─ finalize ─→ finalize_response → END
           └─ general_chat   ─→ generation_node → END
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool as langchain_tool
from langgraph.graph import StateGraph, START, END

from .llm import get_llm, load_llm_settings, _effective_order, _provider_model
from .nodes import intent_router, context_injector, generation_node
from .skills_loader import get_skill, list_skills
from .state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 5

_FINANCE_INTENTS: frozenset[str] = frozenset({
    "trade_recommendation",
    "ticker_deep_dive",
    "stock_screening",
    "sec_filings",
})

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = (
    "You are Open-Fin, an expert financial AI co-pilot with access to "
    "real-time market data and SEC filing research tools.\n\n"
    "SINGLE-CALL POLICY — CRITICAL:\n"
    "Gather ALL required financial data in ONE parallel tool-call block "
    "whenever possible.  For example, when analyzing a stock, call "
    "get_company_profile, get_financial_statements, get_technical_snapshot, "
    "and get_balance_sheet simultaneously in a single response — do NOT "
    "call them one at a time.\n\n"
    "Only request additional tool calls if earlier results reveal new "
    "questions that could not have been anticipated (e.g. a peer comparison "
    "after discovering the sector).  Keep the total number of tool-call "
    "rounds to an absolute minimum.\n\n"
    "SKILLS — Reusable Analytical Playbooks:\n"
    "You have access to a `load_skill` tool that loads structured, step-by-step "
    "analytical playbooks (e.g. 'dcf_analysis').  When a user request aligns "
    "with an available skill, call `load_skill` to retrieve its instructions "
    "and then follow them precisely.  Each skill may only be executed once "
    "per session.\n\n"
    "When you have gathered sufficient data, respond with your analysis "
    "WITHOUT making further tool calls.  The response should be a brief "
    "signal to indicate readiness — the final user-facing answer will be "
    "synthesised in a later step."
)

_FINALIZE_SYSTEM_PROMPT = (
    "You are Open-Fin, an expert financial AI co-pilot.  Synthesise the "
    "research data below into a clear, data-driven answer for the user.  "
    "Be concise, precise and professional.  Cite specific numbers from the "
    "tool results.  Always clarify that your responses are informational "
    "and not financial advice."
)

# ---------------------------------------------------------------------------
# LangChain tool wrappers
# ---------------------------------------------------------------------------
# Each wrapper delegates to the async implementation in tools/ and serialises
# the ToolResult / output to a JSON string the LLM can interpret.
# Imports are deferred inside function bodies to avoid circular-import issues
# (tools.sec_filings imports agent.llm).


@langchain_tool
async def get_ohlcv(
    symbol: str,
    period: str = "3mo",
    interval: str = "1d",
) -> str:
    """Fetch OHLCV candlestick bars for charting and technical analysis.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").
        period: History window — 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max.
        interval: Bar granularity — 1m, 5m, 15m, 30m, 1h, 1d, 5d, 1wk, 1mo.
    """
    from tools.finance import get_ohlcv as _impl

    result = await _impl(symbol, period=period, interval=interval)
    return result.model_dump_json()


@langchain_tool
async def get_technical_snapshot(symbol: str) -> str:
    """Compute SMA(20/50/200), RSI(14), ATR(14) and volume averages for a ticker.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_technical_snapshot as _impl

    result = await _impl(symbol)
    return result.model_dump_json()


@langchain_tool
async def get_company_profile(symbol: str) -> str:
    """Fetch company profile: name, sector, market cap, description, CEO, IPO date.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_company_profile as _impl

    result = await _impl(symbol)
    return result.model_dump_json()


@langchain_tool
async def get_financial_statements(
    symbol: str,
    period: str = "annual",
    limit: int = 4,
) -> str:
    """Fetch income statements (revenue, net income, EPS, margins).

    Args:
        symbol: Ticker symbol.
        period: "annual" or "quarter".
        limit: Number of periods to return.
    """
    from tools.finance import get_financial_statements as _impl

    result = await _impl(symbol, period=period, limit=limit)
    return result.model_dump_json()


@langchain_tool
async def get_balance_sheet(
    symbol: str,
    period: str = "annual",
    limit: int = 4,
) -> str:
    """Fetch balance sheets (assets, debt, cash, book value).

    Args:
        symbol: Ticker symbol.
        period: "annual" or "quarter".
        limit: Number of periods to return.
    """
    from tools.finance import get_balance_sheet as _impl

    result = await _impl(symbol, period=period, limit=limit)
    return result.model_dump_json()


@langchain_tool
async def get_institutional_holders(symbol: str) -> str:
    """Fetch top institutional ownership for a ticker.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_institutional_holders as _impl

    result = await _impl(symbol)
    return result.model_dump_json()


@langchain_tool
async def get_peers(symbol: str) -> str:
    """Fetch peer / competitor tickers and sector information.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_peers as _impl

    result = await _impl(symbol)
    return result.model_dump_json()


@langchain_tool
async def screen_stocks(criteria_json: str, limit: int = 20) -> str:
    """Screen stocks using fundamental criteria via the FMP screener.

    Args:
        criteria_json: A JSON string of FMP screener parameters, e.g.
            '{"marketCapMoreThan": 1000000000, "peRatioLowerThan": 15,
              "sector": "Technology", "country": "US"}'.
            Common keys: marketCapMoreThan, marketCapLowerThan, peRatioMoreThan,
            peRatioLowerThan, priceMoreThan, priceLowerThan, sector, country,
            betaMoreThan, betaLowerThan, dividendMoreThan.
        limit: Maximum number of results (default 20).
    """
    from tools.finance import screen_stocks as _impl

    criteria: dict[str, Any] = json.loads(criteria_json)
    result = await _impl(criteria, limit=limit)
    return result.model_dump_json()


@langchain_tool
async def get_filings_metadata(
    ticker: str,
    form_types: str = "10-K,10-Q",
    limit: int = 3,
) -> str:
    """Fetch recent SEC filing metadata (URLs, accession numbers) for a ticker.

    Args:
        ticker: Ticker symbol.
        form_types: Comma-separated form types (e.g. "10-K,10-Q").
        limit: Maximum number of filings to return.
    """
    from tools.sec_filings import get_filings_metadata as _impl

    ft_list = [f.strip() for f in form_types.split(",") if f.strip()]
    result = await _impl(ticker, form_types=ft_list, limit=limit)
    return result.model_dump_json()


@langchain_tool
async def extract_filing_sections(
    filing_url: str,
    sections: str,
) -> str:
    """Extract specific sections from a SEC filing document by URL.

    Args:
        filing_url: URL of the SEC filing index page.
        sections: Comma-separated section names, e.g.
            "Risk Factors,Management Discussion".
    """
    from tools.sec_filings import extract_filing_sections as _impl

    section_list = [s.strip() for s in sections.split(",") if s.strip()]
    results = await _impl(filing_url, section_list)
    return json.dumps([s.model_dump() for s in results], default=str)


@langchain_tool
async def read_filings(query: str) -> str:
    """Full SEC filing research: plans extraction from a natural-language query,
    retrieves filing metadata, and extracts targeted sections automatically.

    Args:
        query: Natural-language question about SEC filings (e.g.
            "What are AAPL's main risk factors from their latest 10-K?").
    """
    from tools.sec_filings import read_filings as _impl

    result = await _impl(query)
    return result.model_dump_json()


@langchain_tool
def load_skill(skill_name: str) -> str:
    """Load a reusable analytical skill (playbook) by name.

    Returns the skill's step-by-step Markdown instructions so the LLM can
    follow its prescribed workflow.  Use ``list_skills`` output in the system
    prompt to discover available skill names.

    The orchestrator enforces a once-per-session policy: attempting to load
    the same skill a second time within a session will return an error.

    Args:
        skill_name: Unique slug of the skill, e.g. "dcf_analysis".
    """
    skill = get_skill(skill_name)
    if skill is None:
        available = ", ".join(list_skills()) or "(none)"
        return json.dumps({
            "error": f"Skill '{skill_name}' not found. Available: {available}"
        })
    return json.dumps({
        "skill": skill.name,
        "description": skill.description,
        "required_tools": skill.required_tools,
        "instructions": skill.instructions,
    })


# -- Collected tool list and lookup map --

FINANCE_TOOLS: list = [
    get_ohlcv,
    get_technical_snapshot,
    get_company_profile,
    get_financial_statements,
    get_balance_sheet,
    get_institutional_holders,
    get_peers,
    screen_stocks,
    get_filings_metadata,
    extract_filing_sections,
    read_filings,
    load_skill,
]

_TOOL_MAP: dict[str, Any] = {t.name: t for t in FINANCE_TOOLS}

# ---------------------------------------------------------------------------
# Helper: obtain a tool-bound LLM model from the provider fallback chain
# ---------------------------------------------------------------------------


def _get_tool_bound_model(tools: list):
    """Return the first available provider model with *tools* bound."""
    mode, fallback_order = load_llm_settings()
    order = _effective_order(mode, fallback_order)

    for provider in order:
        model = _provider_model(provider)
        if model is not None:
            logger.info(
                "Tool-bound LLM: provider=%s tools=%d", provider, len(tools),
            )
            return model.bind_tools(tools)

    raise RuntimeError(
        "No LLM provider available for tool-augmented queries. "
        "Configure at least one provider in backend/.env."
    )


# ---------------------------------------------------------------------------
# Helper: assemble the message list for the tool-calling LLM
# ---------------------------------------------------------------------------


def _build_tool_messages(state: AgentState) -> list[BaseMessage]:
    """System prompt + full state-message replay for ``route_finance_query``."""

    parts: list[str] = [_ROUTER_SYSTEM_PROMPT]

    injected = state.get("injected_context", "")
    if injected:
        parts.append(f"\n\nCURRENT USER PORTFOLIO:\n{injected}")

    anomaly = state.get("anomaly_context", "")
    if anomaly:
        parts.append(f"\n\nANOMALY ALERT CONTEXT:\n{anomaly}")

    intent = state.get("intent", "")
    if intent == "trade_recommendation":
        parts.append(
            '\n\nWhen recommending trades, format each recommendation as: '
            '[TRADE: {"action": "BUY", "ticker": "AAPL", "qty": 10}]'
        )

    # Advertise available skills
    available_skills = list_skills()
    if available_skills:
        executed = set(state.get("executed_skills", []))
        remaining = [s for s in available_skills if s not in executed]
        if remaining:
            parts.append(
                f"\n\nAVAILABLE SKILLS (call load_skill to activate): "
                f"{', '.join(remaining)}"
            )

    system = SystemMessage(content="".join(parts))

    # Replay the conversation (Human → AI+tool_calls → ToolMessages → …)
    msgs: list[BaseMessage] = [system]
    for m in state.get("messages", []):
        msgs.append(m)
    return msgs


# ---------------------------------------------------------------------------
# Node: route_finance_query
# ---------------------------------------------------------------------------


async def route_finance_query(state: AgentState) -> dict:
    """Invoke the LLM with bound finance / SEC tools.

    The model either makes tool calls (routed to ``execute_tool_calls``) or
    produces a final text answer (routed to ``finalize_response``).
    """
    model = _get_tool_bound_model(FINANCE_TOOLS)
    messages = _build_tool_messages(state)

    response: AIMessage = await model.ainvoke(messages)

    logger.info(
        "route_finance_query: tool_calls=%d content_len=%d",
        len(response.tool_calls) if response.tool_calls else 0,
        len(response.content) if response.content else 0,
    )

    # Extract the user query once (first pass only — non-reducer overwrites)
    current_query = state.get("current_query", "")
    if not current_query:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                current_query = msg.content
                break

    active: list[str] = state.get("active_skills") or [t.name for t in FINANCE_TOOLS]

    return {
        "messages": [response],
        "active_skills": active,
        "current_query": current_query,
    }


# ---------------------------------------------------------------------------
# Node: execute_tool_calls
# ---------------------------------------------------------------------------


async def execute_tool_calls(state: AgentState) -> dict:
    """Run every tool call from the last ``AIMessage`` and return ``ToolMessage``s.

    Increments ``tool_call_count`` by 1 per round (not per individual call) to
    track how many times we have looped.  Individual tool errors are caught and
    returned as JSON so the LLM can decide how to proceed.
    """
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"tool_call_count": 0, "tool_results": [], "messages": []}

    tool_messages: list[ToolMessage] = []
    tool_results: list[dict] = []

    already_executed: set[str] = set(state.get("executed_skills", []))
    newly_executed: list[str] = []

    for call in last_message.tool_calls:
        name: str = call["name"]
        args: dict = call["args"]
        call_id: str = call["id"]

        # --- Duplicate-skill guard ---
        if name == "load_skill":
            requested = args.get("skill_name", "")
            if requested in already_executed:
                output = json.dumps({
                    "error": (
                        f"Skill '{requested}' has already been executed in "
                        "this session. Each skill may only run once."
                    )
                })
                tool_messages.append(ToolMessage(content=output, tool_call_id=call_id))
                tool_results.append({"tool": name, "args": args, "result": output})
                continue
            newly_executed.append(requested)
            already_executed.add(requested)

        handler = _TOOL_MAP.get(name)
        if handler is None:
            output = json.dumps({"error": f"Unknown tool: {name}"})
        else:
            try:
                output = await handler.ainvoke(args)
            except Exception as exc:
                logger.warning("execute_tool_calls: %s failed: %s", name, exc)
                output = json.dumps({"error": str(exc)})

        tool_messages.append(ToolMessage(content=output, tool_call_id=call_id))
        tool_results.append({"tool": name, "args": args, "result": output})

    logger.info(
        "execute_tool_calls: executed %d tool(s), round total → %d",
        len(tool_messages),
        state.get("tool_call_count", 0) + 1,
    )

    return {
        "messages": tool_messages,
        "tool_call_count": 1,              # reducer *adds* to current count
        "tool_results": tool_results,       # reducer *appends* to list
        "executed_skills": newly_executed,  # reducer *appends* to list
    }


# ---------------------------------------------------------------------------
# Node: finalize_response
# ---------------------------------------------------------------------------


async def finalize_response(state: AgentState) -> dict:
    """Synthesise accumulated tool data into a streamed, user-facing answer
    and persist the exchange to ``ChatHistory``.

    Always performs a fresh streaming LLM call so that the chat SSE endpoint
    (which listens for ``on_chat_model_stream`` events) can push tokens to
    the frontend in real time.
    """
    from database import SessionLocal
    from models import ChatHistory

    session_id = state.get("session_id", "")
    tool_results = state.get("tool_results", [])

    # --- Recover the original user message ---
    current_user_text = state.get("current_query", "")
    if not current_user_text:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                current_user_text = msg.content
                break

    # --- Build the synthesis prompt with all accumulated context ---
    ctx_parts: list[str] = [_FINALIZE_SYSTEM_PROMPT]

    injected = state.get("injected_context", "")
    if injected:
        ctx_parts.append(f"\n\nUSER PORTFOLIO:\n{injected}")

    if tool_results:
        ctx_parts.append("\n\nRESEARCH DATA COLLECTED:")
        for tr in tool_results:
            result_text = tr.get("result", "")
            if len(result_text) > 4_000:
                result_text = result_text[:4_000] + "\n...[truncated]"
            ctx_parts.append(
                f"\n[{tr['tool']}({json.dumps(tr['args'], default=str)})]\n"
                f"{result_text}"
            )

    anomaly = state.get("anomaly_context", "")
    if anomaly:
        ctx_parts.append(f"\n\nANOMALY ALERT CONTEXT:\n{anomaly}")

    intent = state.get("intent", "")
    if intent == "trade_recommendation":
        ctx_parts.append(
            '\n\nWhen recommending trades, format each as: '
            '[TRADE: {"action": "BUY", "ticker": "AAPL", "qty": 10}]'
        )

    # --- Load recent chat history for multi-turn context ---
    db = SessionLocal()
    synthesis_messages: list[BaseMessage] = [
        SystemMessage(content="\n".join(ctx_parts)),
    ]
    try:
        rows = (
            db.query(ChatHistory)
            .filter(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.created_at.asc())
            .limit(10)
            .all()
        )
        for row in rows:
            if row.role == "user":
                synthesis_messages.append(HumanMessage(content=row.content))
            elif row.role == "assistant":
                synthesis_messages.append(AIMessage(content=row.content))
    except Exception as exc:
        logger.warning(
            "finalize_response: history load failed for session %s: %s",
            session_id, exc,
        )
    finally:
        db.close()

    synthesis_messages.append(HumanMessage(content=current_user_text))

    # --- Stream the final response (captured by SSE endpoint) ---
    llm = get_llm()
    full_response = ""
    async for chunk in llm.astream(synthesis_messages):
        if chunk.content:
            full_response += chunk.content

    # --- Persist the exchange to ChatHistory ---
    db = SessionLocal()
    try:
        db.add(ChatHistory(
            session_id=session_id,
            role="user",
            content=current_user_text,
            created_at=datetime.utcnow(),
        ))
        db.add(ChatHistory(
            session_id=session_id,
            role="assistant",
            content=full_response,
            created_at=datetime.utcnow(),
        ))
        db.commit()
    except Exception as exc:
        logger.warning("finalize_response: persistence failed: %s", exc)
        db.rollback()
    finally:
        db.close()

    return {"messages": [AIMessage(content=full_response)]}


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------


def _route_after_context(state: AgentState) -> str:
    """After context injection, choose between the tool loop and generation."""
    intent = state.get("intent", "general_chat")
    if intent in _FINANCE_INTENTS:
        logger.debug("Routing to finance tool loop (intent=%s)", intent)
        return "route_finance_query"
    logger.debug("Routing to generation_node (intent=%s)", intent)
    return "generation_node"


def _should_continue_tools(state: AgentState) -> str:
    """Decide whether to execute more tools or finalise the answer.

    Returns ``"execute_tool_calls"`` when the last AI message contains tool
    calls **and** we have not exceeded ``MAX_TOOL_ROUNDS``.  Otherwise
    returns ``"finalize_response"`` to break the loop.
    """
    last_msg = state["messages"][-1] if state.get("messages") else None

    has_tool_calls = (
        isinstance(last_msg, AIMessage)
        and getattr(last_msg, "tool_calls", None)
    )

    count = state.get("tool_call_count", 0)

    if has_tool_calls and count < MAX_TOOL_ROUNDS:
        return "execute_tool_calls"

    if has_tool_calls and count >= MAX_TOOL_ROUNDS:
        logger.warning(
            "Tool-loop ceiling reached (%d/%d rounds). Forcing finalize.",
            count, MAX_TOOL_ROUNDS,
        )

    return "finalize_response"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph():
    """Construct and compile the Open-Fin LangGraph state graph.

    Topology:
        START → intent_router → context_injector
                                      ↓ (conditional)
                   finance intent? ────────────────────────────────────┐
                     Yes → route_finance_query ←───────────────┐      │
                              ↓ (conditional)                  │      │
                           tool_calls AND count < 5?           │      │
                              Yes → execute_tool_calls ────────┘      │
                              No  → finalize_response → END           │
                                                                      │
                     No  → generation_node → END  ←───────────────────┘
    """
    builder = StateGraph(AgentState)

    # -- Nodes --
    builder.add_node("intent_router", intent_router)
    builder.add_node("context_injector", context_injector)
    builder.add_node("generation_node", generation_node)
    builder.add_node("route_finance_query", route_finance_query)
    builder.add_node("execute_tool_calls", execute_tool_calls)
    builder.add_node("finalize_response", finalize_response)

    # -- Edges --
    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "context_injector")

    builder.add_conditional_edges(
        "context_injector",
        _route_after_context,
        {
            "route_finance_query": "route_finance_query",
            "generation_node": "generation_node",
        },
    )

    builder.add_conditional_edges(
        "route_finance_query",
        _should_continue_tools,
        {
            "execute_tool_calls": "execute_tool_calls",
            "finalize_response": "finalize_response",
        },
    )

    builder.add_edge("execute_tool_calls", "route_finance_query")
    builder.add_edge("finalize_response", END)
    builder.add_edge("generation_node", END)

    return builder.compile()


# Compiled once at import time — imported directly by routers/chat.py
graph = build_graph()
