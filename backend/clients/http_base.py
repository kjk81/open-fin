"""Async httpx wrapper with exponential-backoff retries, standardized timeouts,
and custom user-agent injection.

Usage (context manager)::

    async with HttpClient(base_url="https://api.example.com") as client:
        resp = await client.get("/endpoint", params={"q": "AAPL"})
        data = resp.json()

Usage (manual lifecycle)::

    client = HttpClient(base_url="https://api.example.com")
    try:
        resp = await client.get("/endpoint")
    finally:
        await client.close()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from clients.url_guard import SSRFBlockedError, validate_url  # noqa: F401

logger = logging.getLogger(__name__)

# Status codes that warrant a retry with backoff.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Base delay (seconds) for exponential backoff: attempt 0→1 s, 1→2 s, 2→4 s …
_BASE_DELAY: float = 1.0


class HttpClientError(Exception):
    """Raised when the server returns a non-retryable 4xx error."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}: {body[:200]}")
        self.status_code = status_code
        self.url = url


class HttpClient:
    """Async HTTP client with retries, timeouts, and a custom user-agent.

    Parameters
    ----------
    base_url:
        Optional base URL prepended to every request path.
    timeout:
        Total request timeout in seconds (default 30).
    connect_timeout:
        TCP connect timeout in seconds (default 10).
    max_retries:
        Maximum number of retry attempts on retryable errors (default 3).
    user_agent:
        Value for the ``User-Agent`` request header.
    headers:
        Additional default headers merged into every request.
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        user_agent: str = "OpenFin/1.0",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._max_retries = max_retries
        default_headers: dict[str, str] = {"User-Agent": user_agent}
        if headers:
            default_headers.update(headers)

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            headers=default_headers,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send a GET request with automatic retry on retryable errors."""
        return await self._request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send a POST request with automatic retry on retryable errors."""
        return await self._request(
            "POST", path, json=json, data=data, params=params, headers=headers
        )

    async def close(self) -> None:
        """Close the underlying httpx session and release connections."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        # SSRF guard: validate absolute URLs (paths relative to base_url are trusted)
        if path.startswith("http://") or path.startswith("https://"):
            validate_url(path)

        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self._max_retries:
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    data=data,
                    headers=headers,
                )

                if response.status_code in _RETRYABLE_STATUS:
                    delay = self._backoff(attempt)
                    logger.warning(
                        "Retryable HTTP %s from %s (attempt %d/%d), "
                        "retrying in %.1fs",
                        response.status_code,
                        response.url,
                        attempt + 1,
                        self._max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue

                # Non-retryable 4xx → raise immediately
                if 400 <= response.status_code < 500:
                    raise HttpClientError(
                        response.status_code,
                        str(response.url),
                        response.text,
                    )

                response.raise_for_status()
                return response

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                delay = self._backoff(attempt)
                logger.warning(
                    "Network error on %s %s (attempt %d/%d): %s, "
                    "retrying in %.1fs",
                    method,
                    path,
                    attempt + 1,
                    self._max_retries,
                    exc,
                    delay,
                )
                last_exc = exc
                await asyncio.sleep(delay)
                attempt += 1

        # Exhausted all retries
        if last_exc is not None:
            raise last_exc
        raise httpx.HTTPStatusError(  # should not reach here
            f"Exhausted retries for {method} {path}",
            request=httpx.Request(method, path),
            response=httpx.Response(503),
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Return exponential backoff delay for the given attempt index."""
        return _BASE_DELAY * (2 ** attempt)
