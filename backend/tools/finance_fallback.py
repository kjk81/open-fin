from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

ProviderName = Literal[
    "fmp",
    "yfinance",
    "twelve_data",
    "finnhub",
    "alpha_vantage",
    "tiingo",
    "eodhd",
]

DataCategory = Literal["price", "fundamentals", "historical", "news"]

CATEGORY_PROVIDER_ORDER: dict[DataCategory, tuple[ProviderName, ...]] = {
    "price": (
        "fmp",
        "yfinance",
        "twelve_data",
        "finnhub",
        "alpha_vantage",
        "tiingo",
        "eodhd",
    ),
    "fundamentals": (
        "fmp",
        "yfinance",
        "eodhd",
        "alpha_vantage",
        "finnhub",
        "tiingo",
    ),
    "historical": (
        "yfinance",
        "eodhd",
        "fmp",
        "alpha_vantage",
        "tiingo",
    ),
    "news": (
        "fmp",
        "finnhub",
        "yfinance",
        "tiingo",
        "alpha_vantage",
    ),
}

FREE_ENDPOINT_ALLOWLIST: dict[ProviderName, dict[DataCategory, set[str]]] = {
    "fmp": {
        "price": {"ohlcv_bars"},
        "fundamentals": {
            "company_profile",
            "income_statement",
            "balance_sheet",
            "institutional_holders",
            "peers",
            "stock_screener",
        },
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"stock_news"},
    },
    "yfinance": {
        "price": {"ohlcv_bars", "technical_snapshot"},
        "fundamentals": {
            "company_profile",
            "income_statement",
            "balance_sheet",
            "institutional_holders",
        },
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"ticker_news"},
    },
    "twelve_data": {
        "price": {"ohlcv_bars"},
        "fundamentals": set(),
        "historical": set(),
        "news": set(),
    },
    "finnhub": {
        "price": {"ohlcv_bars"},
        "fundamentals": {
            "company_profile",
            "income_statement",
            "balance_sheet",
            "peers",
        },
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"company_news", "news_sentiment"},
    },
    "alpha_vantage": {
        "price": {"ohlcv_bars"},
        "fundamentals": {
            "company_profile",
            "income_statement",
            "balance_sheet",
        },
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"news_sentiment"},
    },
    "tiingo": {
        "price": {"ohlcv_bars"},
        "fundamentals": {"daily_meta"},
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"news"},
    },
    "eodhd": {
        "price": {"ohlcv_bars"},
        "fundamentals": {
            "company_profile",
            "income_statement",
            "balance_sheet",
        },
        "historical": {"ohlcv_bars", "technical_snapshot", "anomaly_scan"},
        "news": {"news"},
    },
}

PROVIDER_KEY_ENV: dict[ProviderName, str | None] = {
    "fmp": "FMP_API_KEY",
    "yfinance": None,
    "twelve_data": "TWELVE_DATA_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "tiingo": "TIINGO_API_KEY",
    "eodhd": "EODHD_API_TOKEN",
}


@dataclass(frozen=True)
class AttemptRecord:
    provider: ProviderName
    status: Literal["success", "failed", "skipped"]
    reason: str | None = None


@dataclass(frozen=True)
class ChainResult:
    provider: ProviderName
    payload: Any
    attempts: list[AttemptRecord]


class FallbackChainExhaustedError(RuntimeError):
    def __init__(self, category: DataCategory, endpoint_id: str, attempts: list[AttemptRecord]) -> None:
        self.category = category
        self.endpoint_id = endpoint_id
        self.attempts = attempts
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        details = "; ".join(
            f"{a.provider}:{a.status}{f'({a.reason})' if a.reason else ''}" for a in self.attempts
        )
        return (
            f"All free-tier providers failed for category={self.category} endpoint={self.endpoint_id}. "
            f"Attempts: {details}"
        )


def _is_endpoint_allowed(provider: ProviderName, category: DataCategory, endpoint_id: str) -> bool:
    return endpoint_id in FREE_ENDPOINT_ALLOWLIST.get(provider, {}).get(category, set())


async def run_fallback_chain(
    *,
    category: DataCategory,
    endpoint_id: str,
    handlers: dict[ProviderName, Callable[[], Awaitable[Any]]],
    per_provider_timeout: float = 15.0,
) -> ChainResult:
    attempts: list[AttemptRecord] = []

    for provider in CATEGORY_PROVIDER_ORDER[category]:
        handler = handlers.get(provider)
        if handler is None:
            attempts.append(AttemptRecord(provider=provider, status="skipped", reason="no handler"))
            continue

        if not _is_endpoint_allowed(provider, category, endpoint_id):
            attempts.append(AttemptRecord(provider=provider, status="skipped", reason="endpoint not free-tier allowlisted"))
            continue

        try:
            payload = await asyncio.wait_for(handler(), timeout=per_provider_timeout)
            attempts.append(AttemptRecord(provider=provider, status="success"))
            return ChainResult(provider=provider, payload=payload, attempts=attempts)
        except Exception as exc:
            attempts.append(AttemptRecord(provider=provider, status="failed", reason=str(exc)))

    raise FallbackChainExhaustedError(category=category, endpoint_id=endpoint_id, attempts=attempts)
