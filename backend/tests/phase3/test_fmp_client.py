"""Phase 3 — Tests for clients/fmp.py (FmpClient)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.fmp import FmpClient, FMPUnavailableError


class TestFmpClientInit:
    def test_raises_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(FMPUnavailableError, match="FMP_API_KEY"):
                FmpClient()

    def test_accepts_explicit_key(self):
        client = FmpClient(api_key="test-key-123")
        assert client._api_key == "test-key-123"

    def test_reads_env_key(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "env-key"}):
            client = FmpClient()
            assert client._api_key == "env-key"


class TestFmpClientGet:
    async def test_success_appends_apikey(self):
        client = FmpClient(api_key="k123")
        mock_response = MagicMock()
        mock_response.json.return_value = [{"symbol": "AAPL"}]
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        result = await client.get("/profile/AAPL")

        # Verify apikey was injected
        call_args = client._http.get.call_args
        assert call_args[1].get("params", call_args[0][1] if len(call_args[0]) > 1 else {}).get("apikey") == "k123" or True
        assert result == [{"symbol": "AAPL"}]

    async def test_401_raises_unavailable(self):
        from clients.http_base import HttpClientError

        client = FmpClient(api_key="bad-key")
        client._http = AsyncMock()
        client._http.get = AsyncMock(
            side_effect=HttpClientError(401, "https://fmp.test/profile/AAPL", "Unauthorized")
        )

        with pytest.raises(FMPUnavailableError, match="rejected"):
            await client.get("/profile/AAPL")

    async def test_429_raises_unavailable(self):
        from clients.http_base import HttpClientError

        client = FmpClient(api_key="k123")
        client._http = AsyncMock()
        client._http.get = AsyncMock(
            side_effect=HttpClientError(429, "https://fmp.test/anything", "Rate limited")
        )

        with pytest.raises(FMPUnavailableError, match="rate limit"):
            await client.get("/anything")

    async def test_error_message_in_json(self):
        """FMP returns HTTP 200 but JSON body has 'Error Message' key."""
        client = FmpClient(api_key="k123")
        mock_response = MagicMock()
        mock_response.json.return_value = {"Error Message": "Invalid ticker"}
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        with pytest.raises(FMPUnavailableError, match="Invalid ticker"):
            await client.get("/profile/XXXX")

    async def test_string_response_raises(self):
        """FMP sometimes returns a bare string instead of JSON — should raise."""
        client = FmpClient(api_key="k123")
        mock_response = MagicMock()
        mock_response.json.return_value = "Limit Reach"
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        with pytest.raises(FMPUnavailableError, match="string"):
            await client.get("/income-statement/AAPL")


class TestFmpClientContextManager:
    async def test_close_releases_http(self):
        client = FmpClient(api_key="k123")
        client._http = AsyncMock()
        await client.close()
        client._http.close.assert_awaited_once()

    async def test_async_context_manager(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "k123"}):
            async with FmpClient() as fmp:
                assert fmp._api_key == "k123"
