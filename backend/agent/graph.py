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
import hashlib
import logging
import re
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

from schemas.tool_contracts import (
    Provenance,
    Quality,
    RawRef,
    ToolResult,
    ToolResultEnvelope,
    ToolTiming,
    compute_completeness,
    to_envelope,
)

from .llm import get_llm, load_llm_settings, _effective_order_for_role, _provider_model
from .modes import AgentMode, ModePolicy, get_mode_policy, normalize_mode
from .nodes import capabilities_snapshot, intent_router, context_injector, generation_node
from .prompts import get_finalize_prompt, get_router_soul_prompt
from .skills_loader import get_skill, list_skills
from .state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 5
_MAX_ARTIFACT_CHARS = 4_500
_NUMERIC_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?%?")
_REF_RE = re.compile(r"\[REF-(\d+)\]")

_TOOL_CAPABILITY_REQUIREMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "search_web": (("internet_dns_ok", "internet access"),),
    "fetch_webpage": (("internet_dns_ok", "internet access"),),
    "get_social_sentiment": (("internet_dns_ok", "internet access"),),
    "get_filings_metadata": (
        ("internet_dns_ok", "internet access"),
        ("sec_api_key_present", "SEC configuration"),
    ),
    "extract_filing_sections": (
        ("internet_dns_ok", "internet access"),
        ("sec_api_key_present", "SEC configuration"),
    ),
    "read_filings": (
        ("internet_dns_ok", "internet access"),
        ("sec_api_key_present", "SEC configuration"),
    ),
    "get_ohlcv": (("fmp_api_key_present", "FMP API access"),),
    "get_technical_snapshot": (("fmp_api_key_present", "FMP API access"),),
    "get_company_profile": (("fmp_api_key_present", "FMP API access"),),
    "get_financial_statements": (("fmp_api_key_present", "FMP API access"),),
    "get_balance_sheet": (("fmp_api_key_present", "FMP API access"),),
    "get_institutional_holders": (("fmp_api_key_present", "FMP API access"),),
    "get_peers": (("fmp_api_key_present", "FMP API access"),),
    "screen_stocks": (("fmp_api_key_present", "FMP API access"),),
}

_MODE_REQUIRED_TOOLS: dict[AgentMode, set[str]] = {
    "research": {"search_web"},
}

GRAPH_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "capabilities_snapshot": ("Checking system capabilities", "Checked system capabilities"),
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
# Envelope helper
# ---------------------------------------------------------------------------


def _truncate_text(text: str, max_len: int = _MAX_ARTIFACT_CHARS) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...[truncated]"


def _extract_sources_from_result(result_text: str) -> list[dict[str, str]]:
    """Extract normalized source refs from a tool output JSON string."""
    if not result_text:
        return []
    try:
        parsed = json.loads(result_text)
    except Exception:
        return []

    if not isinstance(parsed, dict):
        return []

    out: list[dict[str, str]] = []
    for src in parsed.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        title = str(src.get("title") or "").strip()
        if not url and not title:
            continue
        out.append({"url": url, "title": title or url})
    return out


def _resolve_mode_policy(state: AgentState) -> ModePolicy:
    mode_raw = state.get("agent_mode") or "quick"
    mode = normalize_mode(str(mode_raw), fallback="quick", allow_legacy=True)
    return get_mode_policy(mode)


def _parse_state_start_time(state: AgentState) -> datetime | None:
    raw = state.get("start_time_utc")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("Invalid start_time_utc in state: %r", raw)
        return None


def _elapsed_seconds_since_start(state: AgentState) -> float | None:
    start = _parse_state_start_time(state)
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - start).total_seconds()


def _budget_exceeded_payload(*, reason: str, detail: str, policy: ModePolicy, state: AgentState) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"Budget Exceeded: {detail}",
        "reason": reason,
        "mode": policy.mode,
        "budget": {
            "max_tool_calls": policy.max_tool_calls,
            "max_seconds": policy.max_seconds,
            "tool_call_count": state.get("tool_call_count", 0),
            "elapsed_seconds": _elapsed_seconds_since_start(state),
        },
        "suggested_alternative": (
            "Ask for a narrower scope, fewer tickers, or switch to quick mode for a concise answer."
        ),
    }


def _mode_capability_degradation_events(
    *,
    mode_policy: ModePolicy,
    capabilities: dict[str, Any],
    disabled_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    if disabled_tools:
        disabled_tool_names = [d["tool"] for d in disabled_tools]
        reasons = sorted({reason for d in disabled_tools for reason in d["reasons"]})
        suggestions: list[str] = []
        if any("internet access" in reason for reason in reasons):
            suggestions.append("Use quick or portfolio mode for local/portfolio-driven analysis without web access.")
        if any("FMP API access" in reason for reason in reasons):
            suggestions.append("Set FMP_API_KEY, then retry for live market and fundamentals data.")
        if any("SEC configuration" in reason for reason in reasons):
            suggestions.append("Set SEC_API_KEY, or provide filing excerpts directly for local analysis.")

        events.append({
            "type": "capability_degradation",
            "mode": mode_policy.mode,
            "disabled_tools": disabled_tool_names,
            "reasons": reasons,
            "message": (
                f"Mode '{mode_policy.mode}' has limited tool access because "
                + ", ".join(reasons)
                + "."
            ),
            "suggestions": suggestions,
        })

    if mode_policy.requires_worker_reachability and not bool(capabilities.get("worker_reachable", False)):
        events.append({
            "type": "capability_degradation",
            "mode": mode_policy.mode,
            "disabled_tools": ["strategy_worker"],
            "reasons": ["worker reachability"],
            "message": "Strategy mode requires a reachable worker, but worker health check is failing.",
            "suggestions": [
                "Start the worker service and retry strategy mode.",
                "Use research mode for analysis that does not require strategy worker execution.",
            ],
        })

    return events


def _apply_mode_tool_policy(
    *,
    mode_policy: ModePolicy,
    tools: list,
    capabilities: dict[str, Any],
) -> tuple[list, list[dict[str, Any]]]:
    allow_set = set(mode_policy.tool_allow_list)
    policy_filtered = [t for t in tools if not allow_set or t.name in allow_set]

    capability_filtered: list = []
    disabled: list[dict[str, Any]] = []
    for tool_obj in policy_filtered:
        requirements = _TOOL_CAPABILITY_REQUIREMENTS.get(tool_obj.name, ())
        missing = [label for key, label in requirements if not bool(capabilities.get(key, False))]
        if missing:
            disabled.append({"tool": tool_obj.name, "reasons": missing})
            continue
        capability_filtered.append(tool_obj)

    required_tools = _MODE_REQUIRED_TOOLS.get(mode_policy.mode, set())
    for required in sorted(required_tools):
        if required not in {t.name for t in capability_filtered}:
            required_reasons = [
                label
                for key, label in _TOOL_CAPABILITY_REQUIREMENTS.get(required, ())
                if not bool(capabilities.get(key, False))
            ]
            if required_reasons:
                already_recorded = any(d["tool"] == required for d in disabled)
                if not already_recorded:
                    disabled.append({"tool": required, "reasons": required_reasons})

    return capability_filtered, disabled


def _extract_numeric_tokens(text: str) -> set[str]:
    """Return normalized numeric tokens (e.g. 1,234 -> 1234)."""
    normalized: set[str] = set()
    sanitized = _REF_RE.sub("", text or "")
    for match in _NUMERIC_RE.findall(sanitized):
        normalized.add(match.replace(",", "").strip())
    return normalized


def _build_artifact_registry(
    tool_results: list[dict],
    citations: list[dict],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Normalize tool artifacts and assign deterministic REF IDs per response."""
    normalized: list[dict[str, Any]] = []

    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        tool_name = str(tr.get("tool") or "unknown_tool")
        args = tr.get("args") or {}
        result_text = str(tr.get("result") or "")
        src = _extract_sources_from_result(result_text)
        args_json = json.dumps(args, sort_keys=True, default=str)
        key_basis = f"{tool_name}|{args_json}|{result_text[:300]}"
        stable_key = hashlib.sha1(key_basis.encode("utf-8")).hexdigest()
        normalized.append({
            "stable_key": stable_key,
            "tool": tool_name,
            "args": args,
            "result": _truncate_text(result_text),
            "sources": src,
        })

    # If run-level citations were passed independently, preserve them as
    # synthetic artifacts so they are also ref-addressable by the model.
    for idx, c in enumerate(citations):
        if not isinstance(c, dict):
            continue
        url = str(c.get("url") or "").strip()
        title = str(c.get("title") or "").strip()
        if not url and not title:
            continue
        key_basis = f"citation|{title}|{url}|{idx}"
        stable_key = hashlib.sha1(key_basis.encode("utf-8")).hexdigest()
        normalized.append({
            "stable_key": stable_key,
            "tool": "citation",
            "args": {},
            "result": json.dumps({"title": title, "url": url}),
            "sources": [{"url": url, "title": title or url}],
        })

    normalized.sort(key=lambda item: item["stable_key"])

    allowed_numbers: set[str] = set()
    for i, item in enumerate(normalized, start=1):
        item["ref_id"] = f"REF-{i}"
        allowed_numbers |= _extract_numeric_tokens(item.get("result", ""))

    return normalized, allowed_numbers


def _format_artifacts_for_prompt(artifacts: list[dict[str, Any]]) -> str:
    """Render canonical artifact blocks consumed by the synthesis node."""
    if not artifacts:
        return "[REF-0]\nNO_VERIFIABLE_ARTIFACTS"

    blocks: list[str] = []
    for item in artifacts:
        lines = [
            f"[{item['ref_id']}]",
            f"TOOL: {item['tool']}",
            f"ARGS: {json.dumps(item['args'], sort_keys=True, default=str)}",
            "ARTIFACT:",
            item["result"],
        ]
        if item.get("sources"):
            src_bits = [
                f"- {s.get('title') or s.get('url') or 'source'} ({s.get('url') or 'n/a'})"
                for s in item["sources"]
            ]
            lines.extend(["SOURCES:", *src_bits])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _enforce_numeric_verification(
    response_text: str,
    *,
    allowed_ref_ids: set[str],
    allowed_numeric_tokens: set[str],
) -> str:
    """Strip lines with numeric claims lacking valid REF IDs or artifacts backing."""
    if not response_text:
        return response_text

    warning = (
        "Cannot Verify: A numeric claim was removed because it could not be "
        "verified against current-turn artifacts."
    )
    out_lines: list[str] = []

    for raw_line in response_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            out_lines.append(raw_line)
            continue

        line_numbers = _extract_numeric_tokens(line)
        if not line_numbers:
            out_lines.append(raw_line)
            continue

        refs_in_line = {f"REF-{m}" for m in _REF_RE.findall(line)}
        refs_valid = bool(refs_in_line) and refs_in_line.issubset(allowed_ref_ids)
        numbers_valid = line_numbers.issubset(allowed_numeric_tokens)

        if refs_valid and numbers_valid:
            out_lines.append(raw_line)
        else:
            out_lines.append(warning)

    return "\n".join(out_lines)

def _wrap_envelope(
    tool_name: str,
    result: "ToolResult[Any]",
    identifier: str,
    **kwargs: Any,
) -> str:
    """Convert a ToolResult → ToolResultEnvelope and serialize to JSON.

    Computes tool-specific completeness heuristics and forwards any extra
    kwargs (e.g. ``raw_ref``) to :func:`to_envelope`.
    """
    raw_data = result.data
    if hasattr(raw_data, "model_dump"):
        raw_data = raw_data.model_dump()
    elif isinstance(raw_data, list) and raw_data and hasattr(raw_data[0], "model_dump"):
        raw_data = [item.model_dump() for item in raw_data]

    completeness, warnings = compute_completeness(tool_name, raw_data)
    envelope = to_envelope(
        result,
        identifier=identifier,
        completeness=completeness,
        warnings=warnings,
        **kwargs,
    )
    return envelope.model_dump_json()


# ---------------------------------------------------------------------------
# LangChain tool wrappers
# ---------------------------------------------------------------------------
# Each wrapper delegates to the async implementation in tools/ and serialises
# the ToolResultEnvelope to a JSON string the LLM can interpret.
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
    return _wrap_envelope("get_ohlcv", result, identifier=symbol.upper())


@langchain_tool
async def get_technical_snapshot(symbol: str) -> str:
    """Compute SMA(20/50/200), RSI(14), ATR(14) and volume averages for a ticker.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_technical_snapshot as _impl

    result = await _impl(symbol)
    return _wrap_envelope("get_technical_snapshot", result, identifier=symbol.upper())


@langchain_tool
async def get_company_profile(symbol: str) -> str:
    """Fetch company profile: name, sector, market cap, description, CEO, IPO date.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_company_profile as _impl

    result = await _impl(symbol)
    return _wrap_envelope("get_company_profile", result, identifier=symbol.upper())


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
    return _wrap_envelope("get_financial_statements", result, identifier=symbol.upper())


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
    return _wrap_envelope("get_balance_sheet", result, identifier=symbol.upper())


@langchain_tool
async def get_institutional_holders(symbol: str) -> str:
    """Fetch top institutional ownership for a ticker.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_institutional_holders as _impl

    result = await _impl(symbol)
    return _wrap_envelope("get_institutional_holders", result, identifier=symbol.upper())


@langchain_tool
async def get_peers(symbol: str) -> str:
    """Fetch peer / competitor tickers and sector information.

    Args:
        symbol: Ticker symbol.
    """
    from tools.finance import get_peers as _impl

    result = await _impl(symbol)
    return _wrap_envelope("get_peers", result, identifier=symbol.upper())


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
    return _wrap_envelope("screen_stocks", result, identifier=criteria_json)


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
    return _wrap_envelope("get_filings_metadata", result, identifier=ticker.upper())


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
    data = [s.model_dump() for s in results]
    completeness, warnings = compute_completeness("extract_filing_sections", data)
    now = datetime.now(timezone.utc)
    envelope = ToolResultEnvelope(
        data=data,
        provenance=Provenance(
            source="sec.gov",
            retrieved_at=now.isoformat(),
            as_of=now.strftime("%Y-%m-%d"),
            identifier=filing_url,
        ),
        quality=Quality(warnings=warnings, completeness=completeness),
        timing=ToolTiming(
            tool_name="extract_filing_sections",
            started_at=now,
            ended_at=now,
        ),
        success=bool(data),
        raw_ref=RawRef(storage_type="cache_key", ref=filing_url),
    )
    return envelope.model_dump_json()


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
    return _wrap_envelope("read_filings", result, identifier=query)


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
    return _wrap_envelope("search_web", result, identifier=query)


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
    return _wrap_envelope("fetch_webpage", result, identifier=url)


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
    return _wrap_envelope("get_social_sentiment", result, identifier=ticker.upper())


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


def _get_tool_bound_model(tools: list, role: str = "subagent", mode_policy: ModePolicy | None = None):
    """Return the first available provider model with *tools* bound.

    Always uses ``role="subagent"`` by default so that the tool-calling node
    gets the high-reasoning model while the finalizer uses the cheaper agent
    model. When ``mode_policy`` is provided, tools are filtered against
    ``ModePolicy.tool_allow_list`` before binding.

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

    filtered_tools = tools
    if mode_policy is not None:
        allow_set = set(mode_policy.tool_allow_list)
        filtered_tools = [t for t in tools if not allow_set or t.name in allow_set]

    logger.info(
        "Tool-bound LLM: role=%s tools=%d mode=%s",
        role,
        len(filtered_tools),
        mode_policy.mode if mode_policy else "none",
    )

    try:
        return model.bind_tools(filtered_tools)
    except (AttributeError, NotImplementedError) as exc:
        raise RuntimeError(
            f"LLM provider for role='{role}' does not support tool binding: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Helper: assemble the message list for the tool-calling LLM
# ---------------------------------------------------------------------------


def _build_tool_messages(state: AgentState) -> list[BaseMessage]:
    """System prompt + full state-message replay for ``route_finance_query``."""

    agent_mode = state.get("agent_mode", "quick")
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
    mode_policy = _resolve_mode_policy(state)
    capabilities = state.get("capabilities") or {}
    allowed_tools, disabled_tools = _apply_mode_tool_policy(
        mode_policy=mode_policy,
        tools=FINANCE_TOOLS,
        capabilities=capabilities,
    )
    degradation_events = _mode_capability_degradation_events(
        mode_policy=mode_policy,
        capabilities=capabilities,
        disabled_tools=disabled_tools,
    )

    degradation_results: list[dict[str, Any]] = []
    for event in degradation_events:
        degradation_results.append({
            "tool": "mode_capability_guard",
            "args": {"mode": mode_policy.mode},
            "result": json.dumps({
                "success": False,
                "error": event["message"],
                "mode": event["mode"],
                "disabled_tools": event.get("disabled_tools", []),
                "reasons": event.get("reasons", []),
                "suggestions": event.get("suggestions", []),
            }),
        })

    if not allowed_tools:
        blocked = AIMessage(
            content=(
                "I cannot execute tools for this request because required capabilities "
                "are unavailable. I will provide the best local-only answer and suggest alternatives."
            )
        )
        return {
            "messages": [blocked],
            "tool_results": degradation_results,
            "degradation_events": degradation_events,
            "tool_loop_terminated_reason": "capability_degradation",
        }

    try:
        model = _get_tool_bound_model(allowed_tools, role="subagent", mode_policy=mode_policy)
        messages = _build_tool_messages(state)
        if degradation_events:
            degraded_notice = [
                "CAPABILITY LIMITATION NOTICE:",
                *[f"- {event['message']}" for event in degradation_events],
                "Proceed with available tools only and clearly explain limitations to the user.",
            ]
            messages.append(SystemMessage(content="\n".join(degraded_notice)))
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

    active: list[str] = state.get("active_skills") or [t.name for t in allowed_tools]

    return {
        "messages": [response],
        "active_skills": active,
        "current_query": current_query,
        "tool_results": degradation_results,
        "degradation_events": degradation_events,
    }


# ---------------------------------------------------------------------------
# Node: execute_tool_calls
# ---------------------------------------------------------------------------


async def execute_tool_calls(state: AgentState) -> dict:
    """Run every tool call from the last ``AIMessage`` and return ``ToolMessage``s.

    Increments ``tool_call_count`` per individual tool invocation and enforces
    mode-level budgets (``max_tool_calls`` / ``max_seconds``). Individual
    tool errors are caught and
    returned as JSON so the LLM can decide how to proceed.
    """
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"tool_call_count": 0, "tool_results": [], "messages": []}

    mode_policy = _resolve_mode_policy(state)
    tool_messages: list[ToolMessage] = []
    tool_results: list[dict] = []
    citations: list[dict] = []

    already_executed: set[str] = set(state.get("executed_skills", []))
    newly_executed: list[str] = []
    executed_count = 0

    def _append_budget_exceeded_messages(detail: str, reason: str, remaining_calls: list[dict[str, Any]]) -> None:
        payload = _budget_exceeded_payload(
            reason=reason,
            detail=detail,
            policy=mode_policy,
            state=state,
        )
        payload_str = json.dumps(payload)

        if not remaining_calls:
            tool_results.append({
                "tool": "budget_guard",
                "args": {"mode": mode_policy.mode},
                "result": payload_str,
            })
            return

        for pending in remaining_calls:
            pending_name = pending.get("name", "unknown_tool")
            pending_args = pending.get("args", {})
            pending_call_id = pending.get("id", "budget-guard")
            per_tool_payload = {**payload, "tool": pending_name}
            per_tool_output = json.dumps(per_tool_payload)
            tool_messages.append(ToolMessage(content=per_tool_output, tool_call_id=pending_call_id))
            tool_results.append({
                "tool": pending_name,
                "args": pending_args,
                "result": per_tool_output,
            })

    elapsed_before = _elapsed_seconds_since_start(state)
    if (
        mode_policy.max_seconds is not None
        and elapsed_before is not None
        and elapsed_before >= mode_policy.max_seconds
    ):
        _append_budget_exceeded_messages(
            detail=(
                f"time budget reached ({elapsed_before:.1f}s >= {mode_policy.max_seconds}s)"
            ),
            reason="max_seconds",
            remaining_calls=list(last_message.tool_calls),
        )
        return {
            "messages": tool_messages,
            "tool_call_count": 0,
            "tool_results": tool_results,
            "citations": citations,
            "executed_skills": newly_executed,
            "tool_loop_terminated_reason": "budget_exceeded",
        }

    for idx, call in enumerate(last_message.tool_calls):
        total_executed = state.get("tool_call_count", 0) + executed_count
        if mode_policy.max_tool_calls is not None and total_executed >= mode_policy.max_tool_calls:
            _append_budget_exceeded_messages(
                detail=(
                    "tool call budget reached "
                    f"({total_executed}/{mode_policy.max_tool_calls})"
                ),
                reason="max_tool_calls",
                remaining_calls=list(last_message.tool_calls[idx:]),
            )
            break

        elapsed_now = _elapsed_seconds_since_start(state)
        if (
            mode_policy.max_seconds is not None
            and elapsed_now is not None
            and elapsed_now >= mode_policy.max_seconds
        ):
            _append_budget_exceeded_messages(
                detail=(
                    f"time budget reached ({elapsed_now:.1f}s >= {mode_policy.max_seconds}s)"
                ),
                reason="max_seconds",
                remaining_calls=list(last_message.tool_calls[idx:]),
            )
            break

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
        citations.extend(_extract_sources_from_result(output))
        executed_count += 1

    logger.info(
        "execute_tool_calls: executed %d tool(s), total call count → %d",
        executed_count,
        state.get("tool_call_count", 0) + executed_count,
    )

    terminated_reason = ""
    for entry in tool_results:
        try:
            parsed = json.loads(str(entry.get("result") or "{}"))
        except Exception:
            continue
        if isinstance(parsed, dict) and str(parsed.get("error", "")).startswith("Budget Exceeded"):
            terminated_reason = "budget_exceeded"
            break

    return {
        "messages": tool_messages,
        "tool_call_count": executed_count,  # reducer *adds* to current count
        "tool_results": tool_results,       # reducer *appends* to list
        "citations": citations,             # reducer *appends* to list
        "executed_skills": newly_executed,  # reducer *appends* to list
        "tool_loop_terminated_reason": terminated_reason,
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

        Verify gate policy:
        - Input evidence is restricted to current ``tool_results`` + ``citations``.
        - Each artifact is normalized and assigned deterministic ``[REF-n]`` IDs.
        - Numeric lines in model output are stripped unless they include valid
            ``[REF-n]`` and all numbers are present in evidence artifacts.
    """
    from database import SessionLocal
    from models import ChatHistory

    session_id = state.get("session_id", "")
    tool_results = state.get("tool_results", [])
    citations = state.get("citations", [])

    # --- Recover the original user message ---
    current_user_text = state.get("current_query", "")
    if not current_user_text:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                current_user_text = msg.content
                break

    artifacts, allowed_numbers = _build_artifact_registry(tool_results, citations)
    allowed_ref_ids = {item["ref_id"] for item in artifacts}
    artifact_block = _format_artifacts_for_prompt(artifacts)

    # --- Build synthesis prompt with only query + artifacts/citations ---
    agent_mode = state.get("agent_mode", "quick")
    ctx_parts: list[str] = [get_finalize_prompt(agent_mode=agent_mode)]
    ctx_parts.append("\n\nSTANDARDIZED_ARTIFACTS (cite as [REF-n]):\n" + artifact_block)

    # --- Construct minimal synthesis input (no history) ---
    synthesis_messages: list[BaseMessage] = [
        SystemMessage(content="\n".join(ctx_parts)),
    ]
    synthesis_messages.append(HumanMessage(content=current_user_text))

    # --- Stream the final response (captured by SSE endpoint), tools-disabled ---
    llm = get_llm(role="agent")
    if hasattr(llm, "bind_tools"):
        try:
            llm = llm.bind_tools([])
        except Exception as exc:
            logger.debug("finalize_response: bind_tools([]) unsupported: %s", exc)

    full_response = ""
    async for chunk in llm.astream(synthesis_messages):
        if chunk.content:
            full_response += chunk.content

    full_response = _enforce_numeric_verification(
        full_response,
        allowed_ref_ids=allowed_ref_ids,
        allowed_numeric_tokens=allowed_numbers,
    )

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
    citations: list[dict] = []
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
                citations.extend(_extract_sources_from_result(output))
                data_parts.append(f"[{tool_fn.name}({ticker})]\n{output[:4_500]}")
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
        "citations": citations,
        "tool_call_count": 1,
    }


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------


def _route_after_context(state: AgentState) -> str:
    """After context injection, choose between the tool loop and generation."""
    from .mode_config import get_mode_config

    agent_mode = normalize_mode(
        str(state.get("agent_mode", "quick")),
        fallback="quick",
        allow_legacy=True,
    )
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
    termination_reason = (state.get("tool_loop_terminated_reason") or "").strip().lower()
    if termination_reason:
        logger.info("Routing → finalize_response (tool loop terminated: %s)", termination_reason)
        return "finalize_response"

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

    mode_policy = _resolve_mode_policy(state)
    max_tool_calls = mode_policy.max_tool_calls
    if max_tool_calls is not None and count >= max_tool_calls:
        logger.warning(
            "Tool-call budget reached (%d/%d). Forcing finalize.",
            count,
            max_tool_calls,
        )
        return "finalize_response"

    elapsed = _elapsed_seconds_since_start(state)
    if (
        mode_policy.max_seconds is not None
        and elapsed is not None
        and elapsed >= mode_policy.max_seconds
    ):
        logger.warning(
            "Time budget reached (%.2fs/%ss). Forcing finalize.",
            elapsed,
            mode_policy.max_seconds,
        )
        return "finalize_response"

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
    builder.add_node("capabilities_snapshot", capabilities_snapshot)
    builder.add_node("intent_router", intent_router)
    builder.add_node("context_injector", context_injector)
    builder.add_node("generation_node", generation_node)
    builder.add_node("route_finance_query", route_finance_query)
    builder.add_node("execute_tool_calls", execute_tool_calls)
    builder.add_node("force_tool_retry", force_tool_retry)
    builder.add_node("fallback_tool_execution", fallback_tool_execution)
    builder.add_node("finalize_response", finalize_response)

    # -- Edges --
    builder.add_edge(START, "capabilities_snapshot")
    builder.add_edge("capabilities_snapshot", "intent_router")
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
