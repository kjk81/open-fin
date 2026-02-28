"""Agent tool implementations."""

from tools.finance import (
    detect_anomalies,
    get_balance_sheet,
    get_company_profile,
    get_financial_statements,
    get_institutional_holders,
    get_ohlcv,
    get_peers,
    get_technical_snapshot,
    screen_stocks,
    validate_ticker,
)
from tools.edgar import get_8k_detail, get_recent_8k_filings
from tools.web import web_fetch, web_search

__all__ = [
    # yfinance — market action
    "get_ohlcv",
    "get_technical_snapshot",
    "validate_ticker",
    "detect_anomalies",
    # FMP — fundamental research (yfinance fallback)
    "get_company_profile",
    "get_financial_statements",
    "get_balance_sheet",
    "get_institutional_holders",
    "get_peers",
    "screen_stocks",
    # SEC EDGAR — 8-K filings
    "get_recent_8k_filings",
    "get_8k_detail",
    # Web research
    "web_fetch",
    "web_search",
]
