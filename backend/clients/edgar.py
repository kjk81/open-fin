"""SEC EDGAR async API client.

Uses the free ``data.sec.gov`` REST API — no authentication or API key required.

SEC fair-use policy requires:
- A descriptive ``User-Agent`` header identifying your application and contact
- Rate limit of ≤ 10 requests/second (enforced via internal asyncio semaphore)

References
----------
- https://www.sec.gov/developer
- Submissions API: ``GET https://data.sec.gov/submissions/CIK{cik}.json``
- Company tickers: ``GET https://www.sec.gov/files/company_tickers.json``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from clients.http_base import HttpClient

logger = logging.getLogger(__name__)

# SEC requires identifying info in User-Agent per their robots.txt
_SEC_USER_AGENT = "OpenFin/1.0 (financial-ai-copilot; contact@openfin.local)"

_EDGAR_BASE_URL = "https://data.sec.gov"
_SEC_BASE_URL = "https://www.sec.gov"

# Full ticker→CIK map endpoint (≈2 MB JSON, cached in memory after first fetch)
_COMPANY_TICKERS_PATH = "/files/company_tickers.json"


class EdgarClient:
    """Async SEC EDGAR client with built-in rate limiting and ticker→CIK resolution.

    Usage::

        async with EdgarClient() as edgar:
            cik = await edgar.ticker_to_cik("AAPL")
            if cik:
                subs = await edgar.get(f"/submissions/CIK{cik}.json")
    """

    # Class-level cache so all instances share the ticker map after first load
    _ticker_map: dict[str, str] | None = None  # {symbol_upper: zero_padded_cik}
    _ticker_map_lock: asyncio.Lock = asyncio.Lock()  # serialise first-fetch

    def __init__(self) -> None:
        # Semaphore keeps concurrent requests below the SEC's 10 req/sec limit
        self._semaphore = asyncio.Semaphore(8)

        self._edgar_http = HttpClient(
            base_url=_EDGAR_BASE_URL,
            timeout=30.0,
            user_agent=_SEC_USER_AGENT,
            max_retries=2,
        )
        self._sec_http = HttpClient(
            base_url=_SEC_BASE_URL,
            timeout=30.0,
            user_agent=_SEC_USER_AGENT,
            max_retries=2,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a ``data.sec.gov`` endpoint and return the parsed JSON body."""
        async with self._semaphore:
            resp = await self._edgar_http.get(path, params=params)
        return resp.json()

    async def get_company_tickers(self) -> dict[str, str]:
        """Return a ``{SYMBOL: zero_padded_cik}`` dict, loading from SEC if needed.

        The result is cached at the class level for the lifetime of the process
        to avoid hammering the SEC endpoint on every lookup.
        """
        if EdgarClient._ticker_map is not None:
            return EdgarClient._ticker_map

        async with EdgarClient._ticker_map_lock:
            # Double-check after acquiring lock (another coroutine may have populated it)
            if EdgarClient._ticker_map is not None:
                return EdgarClient._ticker_map

            async with self._semaphore:
                resp = await self._sec_http.get(_COMPANY_TICKERS_PATH)
            raw: dict = resp.json()

        # SEC format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
        mapping: dict[str, str] = {}
        for entry in raw.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                mapping[ticker] = cik

        EdgarClient._ticker_map = mapping
        logger.debug("EdgarClient: loaded %d ticker→CIK mappings from SEC", len(mapping))
        return mapping

    async def ticker_to_cik(self, symbol: str) -> str | None:
        """Resolve a ticker symbol to a zero-padded 10-digit CIK string.

        Returns ``None`` if the symbol is not found in the SEC company registry.
        """
        mapping = await self.get_company_tickers()
        return mapping.get(symbol.upper())

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._edgar_http.close()
        await self._sec_http.close()

    async def __aenter__(self) -> "EdgarClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
