"""Agent mode configurations for the Genie coordinator system.

Each mode shapes the agent's behaviour by restricting tool preference,
overriding intent routing, and specifying output format instructions for
the ``finalize_response`` node.  The graph topology is unchanged — modes
affect prompts and routing decisions only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentModeConfig:
    """Configuration for a single agent mode."""

    name: str
    preferred_tools: list[str] = field(default_factory=list)
    finalize_format: str = ""
    intent_override: str | None = None


MODE_CONFIGS: dict[str, AgentModeConfig] = {
    "fundamentals": AgentModeConfig(
        name="fundamentals",
        preferred_tools=[
            "get_company_profile",
            "get_financial_statements",
            "get_balance_sheet",
            "get_institutional_holders",
        ],
        finalize_format=(
            "Structure your response with these sections:\n"
            "## FUNDAMENTALS RATING\n"
            "Give a one-word rating: Strong / Fair / Weak.\n\n"
            "## KEY METRICS\n"
            "Highlight revenue, EPS, margins, and debt levels.\n\n"
            "## RISKS\n"
            "Identify 2-3 key risks from the data.\n\n"
            "## VERDICT\n"
            "A 1-2 sentence conclusion with price implication."
        ),
        intent_override="ticker_deep_dive",
    ),
    "sentiment": AgentModeConfig(
        name="sentiment",
        preferred_tools=[
            "get_institutional_holders",
            "get_peers",
        ],
        finalize_format=(
            "Structure your response with these sections:\n"
            "## SENTIMENT RATING\n"
            "Give a one-word rating: Bullish / Neutral / Bearish.\n\n"
            "## INSTITUTIONAL FLOW\n"
            "Summarise institutional holder changes and weight.\n\n"
            "## SECTOR CONTEXT\n"
            "Compare against peers and sector trends.\n\n"
            "## SIGNAL\n"
            "A 1-2 sentence conclusion with directional bias."
        ),
        intent_override="ticker_deep_dive",
    ),
    "technical": AgentModeConfig(
        name="technical",
        preferred_tools=[
            "get_ohlcv",
            "get_technical_snapshot",
        ],
        finalize_format=(
            "Structure your response with these sections:\n"
            "## TREND RATING\n"
            "Give a one-word rating: Uptrend / Sideways / Downtrend.\n\n"
            "## KEY LEVELS\n"
            "State support, resistance, and pivots from the data.\n\n"
            "## MOMENTUM\n"
            "Interpret RSI, SMA crossovers, and volume patterns.\n\n"
            "## SIGNAL\n"
            "A 1-2 sentence conclusion: Buy / Hold / Sell bias."
        ),
        intent_override="ticker_deep_dive",
    ),
    "genie": AgentModeConfig(
        name="genie",
        preferred_tools=[],  # all tools available
        finalize_format=(
            "Structure your response with these sections:\n"
            "## OVERALL RATING\n"
            "Give a one-word rating: Buy / Hold / Sell.\n\n"
            "## FUNDAMENTALS\n"
            "Summarise key financial metrics and health.\n\n"
            "## SENTIMENT\n"
            "Institutional flow and market perception.\n\n"
            "## STOCK TREND\n"
            "Technical price action and momentum.\n\n"
            "## VERDICT\n"
            "A 2-3 sentence thesis with conviction level."
        ),
        intent_override=None,
    ),
}


def get_mode_config(mode: str) -> AgentModeConfig:
    """Return the config for *mode*, falling back to ``genie``."""
    return MODE_CONFIGS.get(mode.lower().strip(), MODE_CONFIGS["genie"])
