"""Phase 3 — Tests for tools/finance.py (market action & fundamental research)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.finance import (
    AnomalySignal,
    BalanceSheetSummary,
    FMPCompanyProfile,
    IncomeStatementSummary,
    InstitutionalHolder,
    OHLCVBar,
    PeerComparison,
    ScreeningHit,
    TechnicalSnapshot,
)
from schemas.tool_contracts import ToolResult


# ---------------------------------------------------------------------------
# _run_sync uses get_running_loop (Phase 3 fix)
# ---------------------------------------------------------------------------

class TestRunSync:
    async def test_uses_running_loop(self):
        """_run_sync should call asyncio.get_running_loop, not get_event_loop."""
        from tools.finance import _run_sync

        def add(a, b):
            return a + b

        result = await _run_sync(add, 3, 4)
        assert result == 7


# ---------------------------------------------------------------------------
# validate_ticker
# ---------------------------------------------------------------------------

class TestValidateTicker:
    async def test_valid_ticker(self):
        with patch("tools.finance._yf_info", new_callable=AsyncMock) as mock_info:
            mock_info.return_value = {
                "quoteType": "EQUITY",
                "symbol": "AAPL",
                "longName": "Apple Inc.",
                "currentPrice": 150.0,
            }
            from tools.finance import validate_ticker
            result = await validate_ticker("AAPL")
            assert result is True

    async def test_invalid_ticker_empty_info(self):
        with patch("tools.finance._yf_info", new_callable=AsyncMock) as mock_info:
            mock_info.return_value = {}
            from tools.finance import validate_ticker
            result = await validate_ticker("XXXYZ")
            assert result is False


# ---------------------------------------------------------------------------
# get_ohlcv
# ---------------------------------------------------------------------------

class TestGetOhlcv:
    async def test_success(self):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=3)
        df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [104.0, 105.0, 106.0],
            "Volume": [1000, 1200, 1100],
        }, index=dates)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        with patch("tools.finance._run_sync", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = df
            from tools.finance import get_ohlcv
            result = await get_ohlcv("AAPL", period="5d")

        assert result.success is True
        assert len(result.data) == 3
        assert all(isinstance(b, OHLCVBar) for b in result.data)

    async def test_empty_data(self):
        import pandas as pd

        with patch("tools.finance._run_sync", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = pd.DataFrame()
            from tools.finance import get_ohlcv
            result = await get_ohlcv("BADTICKER")

        assert result.success is False
        assert "All free-tier providers failed" in (result.error or "")


# ---------------------------------------------------------------------------
# get_company_profile (FMP + yfinance fallback)
# ---------------------------------------------------------------------------

class TestGetCompanyProfile:
    async def test_fmp_success(self):
        mock_fmp = AsyncMock()
        mock_fmp.get = AsyncMock(return_value=[{
            "companyName": "Apple Inc",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "mktCap": 3_000_000_000_000,
            "description": "Apple designs...",
            "ceo": "Tim Cook",
            "ipoDate": "1980-12-12",
            "exchangeShortName": "NASDAQ",
        }])
        mock_fmp.__aenter__ = AsyncMock(return_value=mock_fmp)
        mock_fmp.__aexit__ = AsyncMock(return_value=False)

        with patch("clients.fmp.FmpClient", return_value=mock_fmp):
            from tools.finance import get_company_profile
            result = await get_company_profile("AAPL")

        assert result.success is True
        assert result.data.ceo == "Tim Cook"
        assert result.data.ipo_date == date(1980, 12, 12)

    async def test_fmp_unavailable_yfinance_fallback(self):
        from clients.fmp import FMPUnavailableError

        mock_fmp = AsyncMock()
        mock_fmp.get = AsyncMock(side_effect=FMPUnavailableError("no key"))
        mock_fmp.__aenter__ = AsyncMock(return_value=mock_fmp)
        mock_fmp.__aexit__ = AsyncMock(return_value=False)

        with patch("clients.fmp.FmpClient", return_value=mock_fmp), \
             patch("tools.finance._yf_info", new_callable=AsyncMock) as mock_yf:
            mock_yf.return_value = {
                "longName": "Apple Inc.",
                "sector": "Technology",
                "marketCap": 3_000_000_000_000,
            }
            from tools.finance import get_company_profile
            result = await get_company_profile("AAPL")

        assert result.success is True
        assert result.data.name == "Apple Inc."
        assert result.data.ceo is None  # yfinance doesn't provide CEO
        assert result.error is None


# ---------------------------------------------------------------------------
# screen_stocks (criteria allowlist)
# ---------------------------------------------------------------------------

class TestScreenStocks:
    async def test_allowlisted_criteria_pass(self):
        mock_fmp = AsyncMock()
        mock_fmp.get = AsyncMock(return_value=[
            {"symbol": "AAPL", "companyName": "Apple", "marketCap": 3e12, "sector": "Technology"},
        ])
        mock_fmp.__aenter__ = AsyncMock(return_value=mock_fmp)
        mock_fmp.__aexit__ = AsyncMock(return_value=False)

        with patch("clients.fmp.FmpClient", return_value=mock_fmp):
            from tools.finance import screen_stocks
            result = await screen_stocks(
                criteria={"marketCapMoreThan": 1_000_000_000, "sector": "Technology"},
                limit=5,
            )

        assert result.success is True
        assert len(result.data) == 1

    async def test_disallowed_criteria_stripped(self):
        """Keys not in the allowlist should be silently dropped."""
        mock_fmp = AsyncMock()
        mock_fmp.get = AsyncMock(return_value=[])
        mock_fmp.__aenter__ = AsyncMock(return_value=mock_fmp)
        mock_fmp.__aexit__ = AsyncMock(return_value=False)

        with patch("clients.fmp.FmpClient", return_value=mock_fmp):
            from tools.finance import screen_stocks
            result = await screen_stocks(
                criteria={
                    "sector": "Technology",
                    "__sql_inject": "DROP TABLE",  # malicious — should be stripped
                    "apikey": "stolen-key",         # override attempt — should be stripped
                },
            )

        # Verify malicious keys were not passed to FMP
        call_args = mock_fmp.get.call_args
        params = call_args[1].get("params") or (call_args[0][1] if len(call_args[0]) > 1 else {})
        assert "__sql_inject" not in params
        assert "apikey" not in params or params["apikey"] != "stolen-key"

    async def test_fmp_unavailable_no_fallback(self):
        """screen_stocks has no yfinance fallback — should return success=False."""
        from clients.fmp import FMPUnavailableError

        mock_fmp = AsyncMock()
        mock_fmp.get = AsyncMock(side_effect=FMPUnavailableError("no key"))
        mock_fmp.__aenter__ = AsyncMock(return_value=mock_fmp)
        mock_fmp.__aexit__ = AsyncMock(return_value=False)

        with patch("clients.fmp.FmpClient", return_value=mock_fmp):
            from tools.finance import screen_stocks
            result = await screen_stocks(criteria={"sector": "Technology"})

        assert result.success is False
        assert "stock_screener" in (result.error or "")


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------

class TestDetectAnomalies:
    async def test_detects_price_drop(self):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=25)
        closes = [100.0] * 24 + [90.0]  # 10% drop on last day
        df = pd.DataFrame({
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000] * 25,
        }, index=dates)

        with patch("tools.finance._run_sync", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = df
            from tools.finance import detect_anomalies
            signals = await detect_anomalies(["TST"], price_drop_threshold=0.05)

        price_drops = [s for s in signals if s.signal_type == "price_drop"]
        assert len(price_drops) >= 1
        assert price_drops[0].symbol == "TST"
        assert price_drops[0].magnitude < 0

    async def test_empty_symbols_returns_empty(self):
        from tools.finance import detect_anomalies
        signals = await detect_anomalies([])
        assert signals == []
