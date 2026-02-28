"""Phase 3 — Tests for clients/edgar.py (EdgarClient)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.edgar import EdgarClient


@pytest.fixture(autouse=True)
def clear_ticker_map():
    """Reset the class-level cache before every test."""
    EdgarClient._ticker_map = None
    yield
    EdgarClient._ticker_map = None


class TestEdgarClientInit:
    def test_semaphore_created(self):
        client = EdgarClient()
        assert isinstance(client._semaphore, asyncio.Semaphore)

    def test_dual_http_clients(self):
        client = EdgarClient()
        # Should have separate clients for data.sec.gov and www.sec.gov
        assert client._edgar_http is not client._sec_http


class TestEdgarClientGet:
    async def test_get_returns_json(self):
        client = EdgarClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"cik": "0000320193"}
        client._edgar_http = AsyncMock()
        client._edgar_http.get = AsyncMock(return_value=mock_response)

        result = await client.get("/submissions/CIK0000320193.json")

        assert result == {"cik": "0000320193"}
        client._edgar_http.get.assert_awaited_once()


class TestEdgarClientTickerMap:
    async def test_loads_and_caches_ticker_map(self):
        client = EdgarClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc"},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        client._sec_http = AsyncMock()
        client._sec_http.get = AsyncMock(return_value=mock_response)

        mapping = await client.get_company_tickers()

        assert mapping["AAPL"] == "0000320193"
        assert mapping["MSFT"] == "0000789019"

        # Should be cached — second call should NOT hit the HTTP client again
        client._sec_http.get = AsyncMock(side_effect=RuntimeError("should not be called"))
        mapping2 = await client.get_company_tickers()
        assert mapping2 is mapping

    async def test_ticker_to_cik_found(self):
        client = EdgarClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc"},
        }
        client._sec_http = AsyncMock()
        client._sec_http.get = AsyncMock(return_value=mock_response)

        cik = await client.ticker_to_cik("aapl")
        assert cik == "0000320193"

    async def test_ticker_to_cik_not_found(self):
        EdgarClient._ticker_map = {"AAPL": "0000320193"}

        client = EdgarClient()
        cik = await client.ticker_to_cik("ZZZZ")
        assert cik is None

    async def test_lock_prevents_double_fetch(self):
        """The asyncio.Lock should prevent concurrent first-fetches."""
        client = EdgarClient()
        call_count = 0

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "0": {"cik_str": 1, "ticker": "TST", "title": "Test"},
        }

        original_get = AsyncMock(return_value=mock_response)

        async def counting_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original_get(*args, **kwargs)

        client._sec_http = AsyncMock()
        client._sec_http.get = counting_get

        # Fire two concurrent fetches
        results = await asyncio.gather(
            client.get_company_tickers(),
            client.get_company_tickers(),
        )

        # Both should return same data, but HTTP should only be called once
        assert results[0] == results[1]
        assert call_count == 1


class TestEdgarClientContextManager:
    async def test_close_releases_both_clients(self):
        client = EdgarClient()
        client._edgar_http = AsyncMock()
        client._sec_http = AsyncMock()
        await client.close()
        client._edgar_http.close.assert_awaited_once()
        client._sec_http.close.assert_awaited_once()
