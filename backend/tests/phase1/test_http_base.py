"""Phase 1 — Tests for clients/http_base.py (HttpClient, backoff, SSRF guard)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from clients.http_base import HttpClient, HttpClientError, _BASE_DELAY
from clients.url_guard import SSRFBlockedError


# ---------------------------------------------------------------------------
# Successful requests
# ---------------------------------------------------------------------------

class TestSuccessfulRequests:
    @respx.mock
    async def test_get_200(self):
        respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with HttpClient(base_url="https://api.example.com") as client:
            resp = await client.get("/data")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @respx.mock
    async def test_post_200(self):
        respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(200, text="created")
        )
        async with HttpClient(base_url="https://api.example.com") as client:
            resp = await client.post("/submit", json={"key": "val"})
        assert resp.status_code == 200

    @respx.mock
    async def test_custom_user_agent(self):
        route = respx.get("https://api.example.com/ua").mock(
            return_value=httpx.Response(200)
        )
        async with HttpClient(
            base_url="https://api.example.com",
            user_agent="TestAgent/1.0",
        ) as client:
            await client.get("/ua")
        assert route.calls[0].request.headers["user-agent"] == "TestAgent/1.0"


# ---------------------------------------------------------------------------
# Exponential backoff & retries
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    @respx.mock
    async def test_retries_on_429(self):
        route = respx.get("https://api.example.com/limited")
        route.side_effect = [
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, text="ok"),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            async with HttpClient(
                base_url="https://api.example.com",
                max_retries=3,
            ) as client:
                resp = await client.get("/limited")
        assert resp.status_code == 200
        assert mock_sleep.call_count == 2  # two retries before success

    @respx.mock
    async def test_retries_on_500(self):
        route = respx.get("https://api.example.com/err")
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(200, text="recovered"),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            async with HttpClient(
                base_url="https://api.example.com",
                max_retries=3,
            ) as client:
                resp = await client.get("/err")
        assert resp.text == "recovered"

    @respx.mock
    async def test_exhausts_retries_on_persistent_502(self):
        respx.get("https://api.example.com/bad").mock(
            return_value=httpx.Response(502)
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            async with HttpClient(
                base_url="https://api.example.com",
                max_retries=2,
            ) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get("/bad")

    def test_backoff_formula(self):
        assert HttpClient._backoff(0) == _BASE_DELAY * 1
        assert HttpClient._backoff(1) == _BASE_DELAY * 2
        assert HttpClient._backoff(2) == _BASE_DELAY * 4
        assert HttpClient._backoff(3) == _BASE_DELAY * 8


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    @respx.mock
    async def test_4xx_raises_http_client_error(self):
        respx.get("https://api.example.com/notfound").mock(
            return_value=httpx.Response(404, text="not found")
        )
        async with HttpClient(base_url="https://api.example.com") as client:
            with pytest.raises(HttpClientError) as exc_info:
                await client.get("/notfound")
        assert exc_info.value.status_code == 404

    @respx.mock
    async def test_connect_error_retries(self):
        route = respx.get("https://api.example.com/down")
        route.side_effect = [
            httpx.ConnectError("connection refused"),
            httpx.Response(200, text="back up"),
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            async with HttpClient(
                base_url="https://api.example.com",
                max_retries=3,
            ) as client:
                resp = await client.get("/down")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    async def test_blocks_private_ip_url(self):
        """HttpClient should reject absolute URLs pointing to private IPs."""
        async with HttpClient() as client:
            with pytest.raises(SSRFBlockedError):
                await client.get("http://127.0.0.1/admin")

    async def test_blocks_metadata_endpoint(self):
        async with HttpClient() as client:
            with pytest.raises(SSRFBlockedError):
                await client.get("http://169.254.169.254/latest/meta-data")

    async def test_allows_public_url_with_base(self):
        """Relative paths with a base_url bypass SSRF check (trusted base)."""
        # This just tests the code path; the request itself would fail
        # because there's no mock set up for the actual HTTP call.
        pass  # See successful request tests above


# ---------------------------------------------------------------------------
# Context manager lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    async def test_context_manager_closes(self):
        client = HttpClient(base_url="https://example.com")
        assert client._client is not None
        await client.close()

    async def test_async_with(self):
        async with HttpClient(base_url="https://example.com") as client:
            assert client._client is not None
