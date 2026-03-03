from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AgentMode = Literal["quick", "research", "portfolio", "strategy"]
LegacyAgentMode = Literal["genie", "fundamentals", "sentiment", "technical"]


class ModePolicy(BaseModel):
    """Execution policy constraints for a single agent mode."""

    mode: AgentMode
    description: str
    allowed_data_domains: list[str] = Field(default_factory=list)
    allow_broad_web_research: bool = False
    requires_worker_reachability: bool = False
    max_tool_calls: int | None = None
    max_seconds: int | None = None


MODE_POLICIES: dict[AgentMode, ModePolicy] = {
    "quick": ModePolicy(
        mode="quick",
        description="Fast local response focused on KG, portfolio, and cached prices.",
        allowed_data_domains=["knowledge_graph", "portfolio", "cached_prices"],
        allow_broad_web_research=False,
        requires_worker_reachability=False,
        max_tool_calls=3,
        max_seconds=10,
    ),
    "research": ModePolicy(
        mode="research",
        description="Deep research with broad tool access including web and filings extraction.",
        allowed_data_domains=["all"],
        allow_broad_web_research=True,
        requires_worker_reachability=False,
        max_tool_calls=8,
        max_seconds=60,
    ),
    "portfolio": ModePolicy(
        mode="portfolio",
        description="Portfolio-centric analysis constrained to portfolio DB, pricing, and balance sheets.",
        allowed_data_domains=["portfolio", "pricing", "balance_sheets"],
        allow_broad_web_research=False,
        requires_worker_reachability=False,
        max_tool_calls=5,
        max_seconds=20,
    ),
    "strategy": ModePolicy(
        mode="strategy",
        description="Strategy worker and backtest focused mode that requires worker reachability.",
        allowed_data_domains=["strategy_workers", "backtests"],
        allow_broad_web_research=False,
        requires_worker_reachability=True,
        max_tool_calls=None,
        max_seconds=None,
    ),
}


LEGACY_MODE_MAP: dict[str, AgentMode] = {
    "genie": "quick",
    "fundamentals": "research",
    "sentiment": "research",
    "technical": "strategy",
}


def get_mode_policy(mode: AgentMode) -> ModePolicy:
    return MODE_POLICIES[mode]


def normalize_mode(
    mode: str | None,
    *,
    fallback: AgentMode = "quick",
    allow_legacy: bool = True,
) -> AgentMode:
    """Normalize user-provided mode into a canonical AgentMode."""
    if mode is None:
        return fallback
    normalized = mode.strip().lower()
    if normalized in MODE_POLICIES:
        return normalized  # type: ignore[return-value]
    if allow_legacy and normalized in LEGACY_MODE_MAP:
        return LEGACY_MODE_MAP[normalized]
    raise ValueError(
        "mode must be one of "
        f"{sorted(MODE_POLICIES.keys())}"
        + (f" or legacy aliases {sorted(LEGACY_MODE_MAP.keys())}" if allow_legacy else "")
    )


def resolve_requested_mode(mode: str | None, agent_mode: str | None) -> AgentMode:
    """Resolve request precedence: `mode` first, then `agent_mode`, else default quick."""
    if mode and mode.strip():
        return normalize_mode(mode, fallback="quick", allow_legacy=True)
    if agent_mode and agent_mode.strip():
        return normalize_mode(agent_mode, fallback="quick", allow_legacy=True)
    return "quick"
