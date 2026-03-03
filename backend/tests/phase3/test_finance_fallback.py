from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tools.finance_fallback import FallbackChainExhaustedError, run_fallback_chain


class TestRunFallbackChain:
    async def test_uses_order_and_falls_through_failures(self):
        handlers = {
            "fmp": AsyncMock(side_effect=RuntimeError("fmp down")),
            "yfinance": AsyncMock(return_value={"ok": "yf"}),
        }

        result = await run_fallback_chain(
            category="price",
            endpoint_id="ohlcv_bars",
            handlers=handlers,
            per_provider_timeout=2.0,
        )

        assert result.provider == "yfinance"
        assert result.payload == {"ok": "yf"}
        assert len(result.attempts) >= 2
        assert result.attempts[0].provider == "fmp"

    async def test_skips_non_free_endpoints(self):
        handlers = {
            "fmp": AsyncMock(return_value={"ok": "fmp"}),
        }

        with pytest.raises(FallbackChainExhaustedError) as exc:
            await run_fallback_chain(
                category="price",
                endpoint_id="company_profile",
                handlers=handlers,
                per_provider_timeout=2.0,
            )

        assert "endpoint=company_profile" in str(exc.value)
        assert "endpoint not free-tier allowlisted" in str(exc.value)

    async def test_exhausted_chain_raises_clean_error(self):
        handlers = {
            "fmp": AsyncMock(side_effect=RuntimeError("quota")),
            "yfinance": AsyncMock(side_effect=RuntimeError("schema")),
        }

        with pytest.raises(FallbackChainExhaustedError) as exc:
            await run_fallback_chain(
                category="fundamentals",
                endpoint_id="company_profile",
                handlers=handlers,
                per_provider_timeout=2.0,
            )

        msg = str(exc.value)
        assert "All free-tier providers failed" in msg
        assert "category=fundamentals" in msg
        assert "fmp:failed(quota)" in msg
