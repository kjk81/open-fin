"""FMP (Financial Modeling Prep) async API client.

Wraps ``HttpClient`` with:
- Base URL pre-set to ``https://financialmodelingprep.com/api/v3``
- Automatic ``apikey`` query-parameter injection from ``FMP_API_KEY`` env var
- Raises ``FMPUnavailableError`` on: missing key, HTTP 401/403 (quota / bad key),
  HTTP 429 (rate limit), or any other transport-level failure

Callers should catch ``FMPUnavailableError`` and fall back to yfinance data.

Usage::

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get("/profile/AAPL")
    except FMPUnavailableError as exc:
        # Fall back to yfinance ...
        pass
"""

from __future__ import annotations

import logging
import os
from typing import Any

from clients.http_base import HttpClient, HttpClientError

logger = logging.getLogger(__name__)

_FMP_BASE_URL = "https://financialmodelingprep.com/stable/"


class FMPUnavailableError(Exception):
    """Raised when the FMP API cannot be reached or returns an error response.

    Signals to tool functions that they should fall back to yfinance.
    The exception message always explains the root cause.
    """


class FmpClient:
    """Async FMP REST client with automatic API-key injection.

    Parameters
    ----------
    api_key:
        Explicit API key; falls back to the ``FMP_API_KEY`` environment variable.

    Raises
    ------
    FMPUnavailableError
        Immediately on construction if no API key is available.

    Usage::

        async with FmpClient() as fmp:
            profile = await fmp.get("/profile/AAPL")   # list[dict]
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("FMP_API_KEY", "")
        if not self._api_key:
            raise FMPUnavailableError(
                "FMP_API_KEY is not configured. "
                "Add it to backend/.env to enable deep research tools. "
                "Sign up (free tier available) at https://financialmodelingprep.com/."
            )
        self._http = HttpClient(base_url=_FMP_BASE_URL, timeout=20.0, max_retries=2)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a FMP endpoint and return the parsed JSON body.

        The ``apikey`` parameter is automatically appended to every request.

        Parameters
        ----------
        path:
            Endpoint path relative to the stable base URL (e.g. ``"/profile/AAPL"``).
        params:
            Additional query parameters dict.

        Returns
        -------
        list | dict
            Parsed JSON response body.

        Raises
        ------
        FMPUnavailableError
            On 401/403 (bad key / quota exhausted), 429 (rate limit), or
            any network / transport failure.
        """
        # Normalize to a relative path so httpx joins it against the base URL
        # correctly (RFC 3986: a leading "/" would replace the /stable segment).
        path = path.lstrip("/")

        # Inject the API key explicitly into the path so the separator is
        # always correct: "?" when no query string exists yet, "&" when one
        # is already embedded in the path (e.g. "profile?symbol=AAPL").
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}apikey={self._api_key}"

        try:
            response = await self._http.get(path, params=params)
        except HttpClientError as exc:
            if exc.status_code in (401, 403):
                raise FMPUnavailableError(
                    f"FMP rejected the request (HTTP {exc.status_code}). "
                    "Check your FMP_API_KEY or upgrade your plan."
                ) from exc
            if exc.status_code == 429:
                raise FMPUnavailableError(
                    "FMP daily/minute rate limit reached (HTTP 429). "
                    "Request will be retried later."
                ) from exc
            raise FMPUnavailableError(f"FMP HTTP error: {exc}") from exc
        except Exception as exc:
            raise FMPUnavailableError(f"FMP network error: {exc}") from exc

        data = response.json()

        # FMP signals errors as {"Error Message": "..."} even on HTTP 200
        if isinstance(data, dict) and "Error Message" in data:
            raise FMPUnavailableError(f"FMP error: {data['Error Message']}")

        # Empty list is valid (no results), but a bare string is suspicious
        if isinstance(data, str):
            raise FMPUnavailableError(f"Unexpected FMP response (string): {data[:200]}")

        return data

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._http.close()

    async def __aenter__(self) -> "FmpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
