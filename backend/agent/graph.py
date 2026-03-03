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
from datetime import datetime, timezone
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

from .llm import get_llm, load_llm_settings, _effective_order_for_role, _provider_model
from .nodes import intent_router, context_injector, generation_node
from .prompts import get_finalize_prompt, get_router_soul_prompt
from .skills_loader import get_skill, list_skills
from .state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 5

GRAPH_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "route_finance_query": ("Planning required data fetches", "Planned data fetches"),
    "execute_tool_calls": ("Executing finance data tools", "Executed finance data tools"),
    "force_tool_retry": ("Forcing tool usage", "Forced tool usage"),
    "fallback_tool_execution": ("Auto-fetching market data", "Market data fetched"),
    "finalize_response": ("Synthesizing final response", "Synthesized final response"),
    "generation_node": ("Generating direct response", "Generated direct response"),
}

_FINANCE_INTENTS: frozenset[str] = frozenset({
    "trade_recommendation",
    "ticker_deep_dive",
    "stock_screening",
    "sec_filings",
})

# System prompts are now generated dynamically by agent/prompts.py.
# get_router_soul_prompt() and get_finalize_prompt() inject the current date
# and embed the Finneas SOUL personality on every call.

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
async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for current news, events, or information about a topic.

    Use this to find breaking news, recent developments, or any information
    not captured by financial data APIs.  Prefer get_social_sentiment for
    Reddit/Twitter sentiment; use search_web for general news searches.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (default 5, max 10).
    """
    from tools.web import web_search as _impl

    result = await _impl(query, max_results=min(max_results, 10))
    return result.model_dump_json()


@langchain_tool
async def fetch_webpage(url: str) -> str:
    """Fetch and extract readable content from a webpage URL.

    Use this to read a specific article, press release, or filing page
    referenced in search results or provided by the user.

    Args:
        url: The full URL to fetch.
    """
    from tools.web import web_fetch as _impl

    result = await _impl(url)
    return result.model_dump_json()


@langchain_tool
async def get_social_sentiment(ticker: str) -> str:
    """Get social media sentiment for a stock ticker from Reddit and Twitter/X.

    Runs targeted searches, fetches top posts, and synthesises an LLM-powered
    Sentiment Snapshot with Overall Bias, Key Catalysts, and Majority Opinion.
    Results are cached for 24 hours.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL", "TSLA").
    """
    from tools.sentiment import get_social_sentiment as _impl

    result = await _impl(ticker)
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
    search_web,
    fetch_webpage,
    get_social_sentiment,
    load_skill,
]

_TOOL_MAP: dict[str, Any] = {t.name: t for t in FINANCE_TOOLS}

# ---------------------------------------------------------------------------
# Helper: obtain a tool-bound LLM model from the provider fallback chain
# ---------------------------------------------------------------------------


def _get_model(role: str = "subagent"):
    """Return the first available provider model for *role*.

    Respects ``{ROLE}_PROVIDER`` env overrides and role-specific model names
    (e.g. ``SUBAGENT_OPENROUTER_MODEL``).  Raises ``RuntimeError`` if no
    configured provider is reachable.
    """
    mode, fallback_order, subagent_order = load_llm_settings()
    order = _effective_order_for_role(mode, fallback_order, role=role, subagent_order=subagent_order)
    logger.info("Model selection: trying providers in order (role=%s): %s", role, list(order))

    for provider in order:
        logger.info("  - Attempting provider: %s", provider)
        model = _provider_model(provider, role=role)
        if model is not None:
            logger.info("Model selected: provider=%s role=%s", provider, role)
            return model

    raise RuntimeError(
        f"No LLM provider available for role='{role}'. "
        "Configure at least one provider in backend/.env."
    )


def describe_graph_stage(node_name: str, phase: str) -> str | None:
    """Return a human-readable stage label for graph node lifecycle events.

    Args:
        node_name: LangGraph node name from stream events.
        phase: Lifecycle phase ("start" or "end").
    """
    labels = GRAPH_STAGE_LABELS.get(node_name)
    if labels is None:
        return None
    return labels[0] if phase == "start" else labels[1]


def _get_tool_bound_model(tools: list, role: str = "subagent"):
    """Return the first available provider model with *tools* bound.

    Always uses ``role="subagent"`` by default so that the tool-calling node
    gets the high-reasoning model while the finalizer uses the cheaper agent
    model.  The full ``FINANCE_TOOLS`` list including ``load_skill`` is always
    passed through intact.

    Falls back to ``role="agent"`` if the subagent provider is unavailable,
    and raises a descriptive ``RuntimeError`` if the resolved model does not
    support ``.bind_tools()``.
    """
    try:
        model = _get_model(role=role)
    except RuntimeError:
        if role != "agent":
            logger.warning(
                "Subagent provider unavailable, falling back to agent role"
            )
            model = _get_model(role="agent")
        else:
            raise

    logger.info("Tool-bound LLM: role=%s tools=%d", role, len(tools))

    try:
        return model.bind_tools(tools)
    except (AttributeError, NotImplementedError) as exc:
        raise RuntimeError(
            f"LLM provider for role='{role}' does not support tool binding: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Helper: assemble the message list for the tool-calling LLM
# ---------------------------------------------------------------------------


def _build_tool_messages(state: AgentState) -> list[BaseMessage]:
    """System prompt + full state-message replay for ``route_finance_query``."""

    agent_mode = state.get("agent_mode", "genie")
    parts: list[str] = [get_router_soul_prompt(agent_mode=agent_mode)]

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
    try:
        model = _get_tool_bound_model(FINANCE_TOOLS, role="subagent")
        messages = _build_tool_messages(state)
        response: AIMessage = await model.ainvoke(messages)
    except Exception as exc:
        logger.error(
            "route_finance_query LLM invocation failed: %s", exc, exc_info=True
        )
        response = AIMessage(
            content=(
                "I encountered an error while analyzing your request: "
                f"{type(exc).__name__}: {exc}"
            )
        )

    logger.info(
        "route_finance_query: tool_calls=%d content_len=%d",
        len(getattr(response, "tool_calls", None) or []),
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
    agent_mode = state.get("agent_mode", "genie")
    ctx_parts: list[str] = [get_finalize_prompt(agent_mode=agent_mode)]

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
    llm = get_llm(role="agent")
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
            created_at=datetime.now(timezone.utc),
        ))
        db.add(ChatHistory(
            session_id=session_id,
            role="assistant",
            content=full_response,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning("finalize_response: persistence failed: %s", exc)
        db.rollback()
    finally:
        db.close()

    return {"messages": [AIMessage(content=full_response)]}


# ---------------------------------------------------------------------------
# Node: force_tool_retry
# ---------------------------------------------------------------------------


async def force_tool_retry(state: AgentState) -> dict:
    """Inject a mandatory-tool-use directive and signal one "round" consumed.

    Called when the LLM returns a plain-text response on the very first pass
    of the finance loop — the "fast bypass" bug.  A SystemMessage is appended
    to the message list telling the LLM it MUST call at least one finance tool
    before synthesising an answer.  ``tool_call_count`` is incremented by 1
    so that this branch fires at most once; on the next pass, if the LLM still
    produces no tool calls, ``_should_continue_tools`` will fall through to
    ``finalize_response`` rather than looping forever.
    """
    tickers = state.get("tickers_mentioned", [])
    ticker_hint = f" for {', '.join(tickers)}" if tickers else ""

    directive = SystemMessage(
        content=(
            "MANDATORY TOOL USE — You responded without calling any tools. "
            f"You MUST call at least one finance tool{ticker_hint} before "
            "answering.  Your training data is stale.  Use get_company_profile, "
            "get_technical_snapshot, get_financial_statements, or another "
            "appropriate tool NOW.  Do NOT produce a final answer until you "
            "have retrieved live data."
        )
    )
    logger.info("force_tool_retry: injecting mandatory-tool directive%s", ticker_hint)
    return {
        "messages": [directive],
        "tool_call_count": 1,   # counts as one round to prevent infinite retry
    }


# ---------------------------------------------------------------------------
# Node: fallback_tool_execution
# ---------------------------------------------------------------------------


async def fallback_tool_execution(state: AgentState) -> dict:
    """Programmatically invoke core tools when the LLM ignores force_tool_retry.

    Called when ``_should_continue_tools`` detects the LLM produced no tool
    calls on the second pass (after force_tool_retry already fired).  Directly
    invokes ``get_company_profile`` and ``get_technical_snapshot`` for every
    ticker in ``tickers_mentioned`` (up to 3), then injects the raw JSON as a
    ``SystemMessage`` so ``finalize_response`` has real market data to work with.

    Uses ``SystemMessage`` instead of ``ToolMessage`` intentionally: ToolMessages
    require matching ``tool_call_id`` values from a prior AIMessage, which we do
    not have here.  The collected results are also appended to ``tool_results``
    via the ``operator.add`` reducer so the KG post-processor receives them.
    """
    tickers = state.get("tickers_mentioned", [])
    if not tickers:
        logger.warning(
            "fallback_tool_execution: no tickers_mentioned — cannot auto-fetch data. "
            "KG will remain empty for this request."
        )
        return {"tool_call_count": 1}

    tool_results: list[dict] = []
    data_parts: list[str] = []

    for ticker in tickers[:3]:
        for tool_fn in (get_company_profile, get_technical_snapshot):
            try:
                output: str = await tool_fn.ainvoke({"symbol": ticker})
                tool_results.append({
                    "tool": tool_fn.name,
                    "args": {"symbol": ticker, "ticker": ticker},
                    "result": output,
                })
                data_parts.append(f"[{tool_fn.name}({ticker})]\n{output[:4_000]}")
                logger.info(
                    "fallback_tool_execution: fetched %s(%s) — %d chars",
                    tool_fn.name, ticker, len(output),
                )
            except Exception as exc:
                logger.warning(
                    "fallback_tool_execution: %s(%s) failed: %s",
                    tool_fn.name, ticker, exc,
                )

    messages = []
    if data_parts:
        messages.append(SystemMessage(
            content="AUTO-FETCHED MARKET DATA (tools were not called by the model):\n\n"
                    + "\n\n".join(data_parts)
        ))

    logger.info(
        "fallback_tool_execution: %d tool result(s) collected for %s",
        len(tool_results), tickers[:3],
    )
    return {
        "messages": messages,
        "tool_results": tool_results,
        "tool_call_count": 1,
    }


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------


def _route_after_context(state: AgentState) -> str:
    """After context injection, choose between the tool loop and generation."""
    from .mode_config import get_mode_config

    agent_mode = state.get("agent_mode", "genie")
    cfg = get_mode_config(agent_mode)

    intent = state.get("intent", "general_chat")

    # Mode-specific intent override (e.g. fundamentals forces ticker_deep_dive)
    if cfg.intent_override and intent not in _FINANCE_INTENTS:
        logger.info(
            "Mode '%s' overriding intent '%s' → '%s'",
            agent_mode, intent, cfg.intent_override,
        )
        intent = cfg.intent_override
        # Mutate state for downstream nodes
        state["intent"] = intent  # type: ignore[index]

    if intent in _FINANCE_INTENTS:
        logger.debug("Routing to finance tool loop (intent=%s, mode=%s)", intent, agent_mode)
        return "route_finance_query"
    logger.debug("Routing to generation_node (intent=%s, mode=%s)", intent, agent_mode)
    return "generation_node"


def _should_continue_tools(state: AgentState) -> str:
    """Decide whether to execute more tools, force a retry, or finalise.

    Decision matrix (evaluated in order):
    1. tool_calls present AND count < MAX_TOOL_ROUNDS  → execute_tool_calls
    2. tool_calls present AND count >= MAX_TOOL_ROUNDS → finalize_response (ceiling)
    3. NO tool_calls AND count == 0 (first pass)       → force_tool_retry
    4. NO tool_calls AND count == 1 AND tickers known  → fallback_tool_execution
    5. otherwise                                        → finalize_response
    """
    last_msg = state["messages"][-1] if state.get("messages") else None

    has_tool_calls = (
        isinstance(last_msg, AIMessage)
        and bool(getattr(last_msg, "tool_calls", None))
    )

    count = state.get("tool_call_count", 0)
    tickers = state.get("tickers_mentioned", [])

    logger.info(
        "_should_continue_tools: has_tool_calls=%s count=%d tickers=%s",
        has_tool_calls, count, tickers,
    )

    if has_tool_calls and count < MAX_TOOL_ROUNDS:
        logger.info("Routing → execute_tool_calls (round %d)", count + 1)
        return "execute_tool_calls"

    if has_tool_calls and count >= MAX_TOOL_ROUNDS:
        logger.warning(
            "Tool-loop ceiling reached (%d/%d rounds). Forcing finalize.",
            count, MAX_TOOL_ROUNDS,
        )
        return "finalize_response"

    # First pass with no tool calls — inject a mandatory-tool directive.
    if not has_tool_calls and count == 0:
        logger.warning(
            "LLM skipped tools on first pass (intent=%s, tickers=%s). "
            "Injecting mandatory-tool directive and retrying.",
            state.get("intent", "?"),
            tickers,
        )
        return "force_tool_retry"

    # Second pass still no tool calls — fall back to programmatic execution
    # if we know which tickers to fetch.  This is the fix for the failure mode
    # where the LLM ignores the force_tool_retry directive.
    if not has_tool_calls and count == 1 and tickers:
        logger.warning(
            "LLM still skipped tools after force_tool_retry (tickers=%s). "
            "Routing → fallback_tool_execution.",
            tickers,
        )
        return "fallback_tool_execution"

    logger.info("Routing → finalize_response (count=%d, has_tool_calls=%s)", count, has_tool_calls)
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
                              ↓ (conditional)              ┌───┘      │
                           tool_calls AND count < 5?       │          │
                              Yes → execute_tool_calls ────┘          │
                              No, count==0 → force_tool_retry ────────┘ (loops back)
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
    builder.add_node("force_tool_retry", force_tool_retry)
    builder.add_node("fallback_tool_execution", fallback_tool_execution)
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
            "force_tool_retry": "force_tool_retry",
            "fallback_tool_execution": "fallback_tool_execution",
            "finalize_response": "finalize_response",
        },
    )

    builder.add_edge("execute_tool_calls", "route_finance_query")
    builder.add_edge("force_tool_retry", "route_finance_query")
    builder.add_edge("fallback_tool_execution", "finalize_response")
    builder.add_edge("finalize_response", END)
    builder.add_edge("generation_node", END)

    return builder.compile()


# Compiled once at import time — imported directly by routers/chat.py
graph = build_graph()
