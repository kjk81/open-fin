"""Action Classification Registry & Safety Schema.

Defines the boundary between read-only data fetching and state-altering
operations. Every tool in the system must be explicitly listed here.

Classification policy:
  - Unknown tools default to READ_ONLY (safe sentinel for Phase 4 additions).
  - confirm_memory_write is WRITES_KG because it triggers KG + FAISS persistence.
  - Phase 4 trade/portfolio tools will be WRITES_PORTFOLIO.
  - Phase 4 strategy workers will be STRATEGY_TRIGGER.
  - Destructive admin operations will be ADMIN.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ActionCategory enum
# ---------------------------------------------------------------------------


class ActionCategory(str, Enum):
    """Side-effect classification for agent tools.

    Using str, Enum so values are directly JSON-serializable and print cleanly
    in logs without needing .value access everywhere.
    """

    READ_ONLY = "READ_ONLY"
    """Pure data fetch — no persistent state is mutated."""

    WRITES_KG = "WRITES_KG"
    """Mutates the knowledge graph or its vector index (FAISS)."""

    WRITES_PORTFOLIO = "WRITES_PORTFOLIO"
    """Places or modifies orders / positions in the user's portfolio."""

    STRATEGY_TRIGGER = "STRATEGY_TRIGGER"
    """Launches or schedules a strategy backtest or live execution worker."""

    ADMIN = "ADMIN"
    """Destructive administrative operations (session reset, memory purge, etc.)."""


# ---------------------------------------------------------------------------
# TOOL_ACTION_REGISTRY
# ---------------------------------------------------------------------------

TOOL_ACTION_REGISTRY: dict[str, ActionCategory] = {
    # ── FINANCE_TOOLS (graph.py FINANCE_TOOLS list) ─────────────────────────
    "get_ohlcv": ActionCategory.READ_ONLY,
    "get_technical_snapshot": ActionCategory.READ_ONLY,
    "get_company_profile": ActionCategory.READ_ONLY,
    "get_financial_statements": ActionCategory.READ_ONLY,
    "get_balance_sheet": ActionCategory.READ_ONLY,
    "get_institutional_holders": ActionCategory.READ_ONLY,
    "get_peers": ActionCategory.READ_ONLY,
    "screen_stocks": ActionCategory.READ_ONLY,
    "get_filings_metadata": ActionCategory.READ_ONLY,
    "extract_filing_sections": ActionCategory.READ_ONLY,
    "read_filings": ActionCategory.READ_ONLY,
    "search_web": ActionCategory.READ_ONLY,
    "fetch_webpage": ActionCategory.READ_ONLY,
    # get_social_sentiment caches internally, but the cache is an implementation
    # detail — no user-visible state is mutated.
    "get_social_sentiment": ActionCategory.READ_ONLY,
    # confirm_memory_write triggers KG + FAISS persistence pipeline.
    "confirm_memory_write": ActionCategory.WRITES_KG,
    "load_skill": ActionCategory.READ_ONLY,

    # ── Tools in tools/ not yet bound to FINANCE_TOOLS ──────────────────────
    "validate_ticker": ActionCategory.READ_ONLY,
    "detect_anomalies": ActionCategory.READ_ONLY,
    "get_recent_8k_filings": ActionCategory.READ_ONLY,
    "get_8k_detail": ActionCategory.READ_ONLY,

    # ── Phase 4 WRITES_PORTFOLIO placeholders (registered now for safety) ────
    "execute_trade": ActionCategory.WRITES_PORTFOLIO,
    "place_order": ActionCategory.WRITES_PORTFOLIO,
    "submit_order": ActionCategory.WRITES_PORTFOLIO,
    "cancel_order": ActionCategory.WRITES_PORTFOLIO,
    "add_to_portfolio": ActionCategory.WRITES_PORTFOLIO,
    "remove_from_portfolio": ActionCategory.WRITES_PORTFOLIO,
    "add_to_watchlist": ActionCategory.WRITES_PORTFOLIO,
    "remove_from_watchlist": ActionCategory.WRITES_PORTFOLIO,

    # ── Phase 4 WRITES_KG placeholders ──────────────────────────────────────
    "add_kg_node": ActionCategory.WRITES_KG,
    "upsert_kg_entity": ActionCategory.WRITES_KG,
    "delete_kg_node": ActionCategory.WRITES_KG,
    "remove_kg_entity": ActionCategory.WRITES_KG,
    "link_kg_entities": ActionCategory.WRITES_KG,

    # ── Phase 4 STRATEGY_TRIGGER placeholders ───────────────────────────────
    "run_backtest": ActionCategory.STRATEGY_TRIGGER,
    "execute_strategy": ActionCategory.STRATEGY_TRIGGER,
    "trigger_strategy": ActionCategory.STRATEGY_TRIGGER,
    "schedule_strategy": ActionCategory.STRATEGY_TRIGGER,

    # ── Phase 4 ADMIN placeholders ───────────────────────────────────────────
    "reset_session": ActionCategory.ADMIN,
    "clear_memory": ActionCategory.ADMIN,
    "purge_kg": ActionCategory.ADMIN,
    "rebuild_faiss_index": ActionCategory.ADMIN,
}


def get_action_category(tool_name: str) -> ActionCategory:
    """Return the ActionCategory for *tool_name*.

    Unknown tools default to READ_ONLY — the safest possible sentinel.
    Phase 4 authors must explicitly register new non-READ_ONLY tools here.
    """
    return TOOL_ACTION_REGISTRY.get(tool_name, ActionCategory.READ_ONLY)


# ---------------------------------------------------------------------------
# ActionPreview schema
# ---------------------------------------------------------------------------


class ActionPreview(BaseModel):
    """Structured preview of a proposed non-READ_ONLY tool invocation.

    Created by execute_tool_calls before any state-altering tool runs.
    Stored in AgentState.pending_actions (as model_dump() dicts) so the UI
    or a future consent gate node can inspect and gate the action.
    """

    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool: str
    category: ActionCategory
    args: dict[str, Any] = Field(default_factory=dict)
    delta_preview: str
    """Human-readable description of what this action will do."""
    justification_citations: list[str] = Field(default_factory=list)
    """REF-n strings from the artifact registry supporting this action."""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Delta preview helper
# ---------------------------------------------------------------------------


def build_delta_preview(tool_name: str, args: dict[str, Any]) -> str:
    """Generate a human-readable description of what a tool call will do.

    Never raises — falls back to a generic description on any error.
    """
    try:
        return _build_delta_preview_inner(tool_name, args)
    except Exception:
        return f"Execute {tool_name}"


def _build_delta_preview_inner(tool_name: str, args: dict[str, Any]) -> str:
    # ── WRITES_KG ────────────────────────────────────────────────────────────
    if tool_name == "confirm_memory_write":
        decision = str(args.get("decision", "confirm")).lower()
        if decision in {"confirm", "yes", "approve"}:
            return "Persist current research session to long-term memory (KG + FAISS)"
        return "Discard pending memory persistence proposal"

    if tool_name in {"add_kg_node", "upsert_kg_entity"}:
        entity = str(args.get("name", args.get("entity", ""))).strip()
        node_type = str(args.get("node_type", "entity")).strip()
        return f"Add {node_type} '{entity}' to Knowledge Graph" if entity else "Mutate Knowledge Graph"

    if tool_name in {"delete_kg_node", "remove_kg_entity"}:
        entity = str(args.get("name", args.get("entity", ""))).strip()
        return f"Remove '{entity}' from Knowledge Graph" if entity else "Delete Knowledge Graph node"

    if tool_name == "link_kg_entities":
        src = str(args.get("source", args.get("from", ""))).strip()
        dst = str(args.get("target", args.get("to", ""))).strip()
        rel = str(args.get("relation", args.get("relationship", "relates_to"))).strip()
        if src and dst:
            return f"Connect '{src}' → '{dst}' ({rel}) in Knowledge Graph"
        return "Link entities in Knowledge Graph"

    # ── WRITES_PORTFOLIO ─────────────────────────────────────────────────────
    if tool_name in {"execute_trade", "place_order", "submit_order"}:
        action = str(args.get("action", args.get("side", ""))).upper()
        ticker = str(args.get("ticker", args.get("symbol", ""))).upper()
        qty = args.get("qty", args.get("quantity", args.get("shares", "")))
        if action and ticker:
            return f"{action} {qty} share(s) of {ticker}" if qty else f"{action} {ticker}"
        return "Execute trade order"

    if tool_name == "cancel_order":
        order_id = str(args.get("order_id", args.get("id", ""))).strip()
        return f"Cancel order {order_id}" if order_id else "Cancel open order"

    if tool_name in {"add_to_portfolio", "add_to_watchlist"}:
        ticker = str(args.get("ticker", args.get("symbol", ""))).upper()
        list_name = "Portfolio" if "portfolio" in tool_name else "Watchlist"
        return f"Add '{ticker}' to {list_name}" if ticker else f"Add ticker to {list_name}"

    if tool_name in {"remove_from_portfolio", "remove_from_watchlist"}:
        ticker = str(args.get("ticker", args.get("symbol", ""))).upper()
        list_name = "Portfolio" if "portfolio" in tool_name else "Watchlist"
        return f"Remove '{ticker}' from {list_name}" if ticker else f"Remove ticker from {list_name}"

    # ── STRATEGY_TRIGGER ─────────────────────────────────────────────────────
    if tool_name in {"run_backtest", "execute_strategy", "trigger_strategy", "schedule_strategy"}:
        strategy = str(args.get("strategy_name", args.get("strategy", ""))).strip()
        ticker = str(args.get("ticker", args.get("symbol", ""))).upper()
        if strategy and ticker:
            return f"Run strategy '{strategy}' on {ticker}"
        if strategy:
            return f"Run strategy '{strategy}'"
        return "Trigger strategy execution"

    # ── ADMIN ─────────────────────────────────────────────────────────────────
    if tool_name == "reset_session":
        return "Reset current agent session state"
    if tool_name == "clear_memory":
        return "Clear long-term memory (KG + FAISS)"
    if tool_name == "purge_kg":
        return "Purge entire Knowledge Graph"
    if tool_name == "rebuild_faiss_index":
        return "Rebuild FAISS vector index from scratch"

    # ── READ_ONLY fallback (should not reach here for registered tools) ───────
    symbol = str(args.get("symbol", args.get("ticker", ""))).upper()
    label = tool_name.replace("_", " ")
    if symbol:
        return f"Fetch {label} for {symbol}"
    return f"Execute {label}"


# ---------------------------------------------------------------------------
# Rollback hint builder
# ---------------------------------------------------------------------------


def build_rollback_hint(tool_name: str, args: dict[str, Any]) -> str:
    """Return a human-readable string describing how to manually reverse a write.

    Used exclusively by _log_state_write to populate rollback_hint in state_write
    events. Never raises — falls back to a generic message on any error.
    """
    try:
        return _build_rollback_hint_inner(tool_name, args)
    except Exception:
        return f"No automated rollback available for {tool_name}"


def _build_rollback_hint_inner(tool_name: str, args: dict[str, Any]) -> str:
    # ── WRITES_KG ────────────────────────────────────────────────────────────
    if tool_name == "confirm_memory_write":
        proposal_id = str(args.get("proposal_id", "<proposal_id>")).strip()
        decision = str(args.get("decision", "confirm")).lower()
        if decision in {"confirm", "yes", "approve"}:
            return f"confirm_memory_write(proposal_id='{proposal_id}', decision='discard')"
        return "Already discarded — no rollback needed"

    if tool_name in {"add_kg_node", "upsert_kg_entity"}:
        entity = str(args.get("name", args.get("entity", "<entity>"))).strip()
        return f"delete_kg_node(name='{entity}')"

    if tool_name in {"delete_kg_node", "remove_kg_entity"}:
        entity = str(args.get("name", args.get("entity", "<entity>"))).strip()
        return f"add_kg_node(name='{entity}') — must re-supply original properties manually"

    if tool_name == "link_kg_entities":
        src = str(args.get("source", args.get("from", "<source>"))).strip()
        dst = str(args.get("target", args.get("to", "<target>"))).strip()
        rel = str(args.get("relation", args.get("relationship", "relates_to"))).strip()
        return f"Unlink '{src}' → '{dst}' ({rel}) via KG admin API"

    # ── WRITES_PORTFOLIO ─────────────────────────────────────────────────────
    if tool_name in {"execute_trade", "place_order", "submit_order"}:
        action = str(args.get("action", args.get("side", ""))).upper()
        ticker = str(args.get("ticker", args.get("symbol", "<ticker>"))).upper()
        qty = args.get("qty", args.get("quantity", args.get("shares", "")))
        reverse = "SELL" if action == "BUY" else "BUY" if action == "SELL" else "reverse"
        if qty:
            return f"cancel_order(order_id=<returned_order_id>) OR execute_trade(action='{reverse}', ticker='{ticker}', qty={qty})"
        return f"cancel_order(order_id=<returned_order_id>)"

    if tool_name == "cancel_order":
        return "Cannot un-cancel an order — re-place manually if needed"

    if tool_name in {"add_to_portfolio", "add_to_watchlist"}:
        ticker = str(args.get("ticker", args.get("symbol", "<ticker>"))).upper()
        reverse_tool = "remove_from_portfolio" if "portfolio" in tool_name else "remove_from_watchlist"
        return f"{reverse_tool}(ticker='{ticker}')"

    if tool_name in {"remove_from_portfolio", "remove_from_watchlist"}:
        ticker = str(args.get("ticker", args.get("symbol", "<ticker>"))).upper()
        reverse_tool = "add_to_portfolio" if "portfolio" in tool_name else "add_to_watchlist"
        return f"{reverse_tool}(ticker='{ticker}')"

    # ── STRATEGY_TRIGGER ─────────────────────────────────────────────────────
    if tool_name in {"run_backtest", "execute_strategy", "trigger_strategy", "schedule_strategy"}:
        return "Strategy runs are read-only simulations — no persistent state mutated unless output is committed"

    # ── ADMIN ─────────────────────────────────────────────────────────────────
    if tool_name == "reset_session":
        return "No automated rollback — session state is gone; restore from prior episodic_summaries if needed"
    if tool_name in {"clear_memory", "purge_kg"}:
        return "No automated rollback — manual database restore from backup required"
    if tool_name == "rebuild_faiss_index":
        return "Re-run rebuild_faiss_index to regenerate — no data loss if KG is intact"

    return f"No automated rollback available for {tool_name}"


# ---------------------------------------------------------------------------
# Startup validation utility
# ---------------------------------------------------------------------------


def validate_registry_coverage(tool_names: list[str]) -> list[str]:
    """Return tool names present in *tool_names* but missing from TOOL_ACTION_REGISTRY.

    Intended for a one-time startup check. Empty return means full coverage.
    Unknown tools are not broken (get_action_category defaults to READ_ONLY),
    but gaps surface here so developers can register them explicitly.
    """
    return [name for name in tool_names if name not in TOOL_ACTION_REGISTRY]
