"""Financial data tools: dual-source strategy (yfinance + FMP).

Market Action tools  (yfinance)
--------------------------------
- ``get_ohlcv``              OHLCV bars for charting; any period/interval
- ``get_technical_snapshot`` SMA(20/50/200), RSI(14), ATR(14), volume avg
- ``validate_ticker``        Quick existence check
- ``detect_anomalies``       Price-drop / volume-spike / gap-down scanner

Fundamental Research tools  (FMP primary, yfinance fallback)
-------------------------------------------------------------
- ``get_company_profile``       Name, sector, market cap, description, CEO
- ``get_financial_statements``  Income statement (revenue, EPS, margins)
- ``get_balance_sheet``         Assets, debt, cash, book value
- ``get_institutional_holders`` Top institutional ownership
- ``get_peers``                 Peer ticker list
- ``screen_stocks``             Fundamental screener (FMP only; no yfinance fallback)

Data-source discipline
----------------------
yfinance and FMP are **never** mixed inside a single function without explicit
fallback logic.  When FMP is unavailable the ``ToolResult.error`` field signals
degraded mode even when ``success=True`` (partial yfinance data returned).
``success=False`` is reserved for cases where no data at all could be obtained.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

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
from schemas.tool_contracts import SourceRef, ToolResult, ToolTiming

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _timing(tool_name: str, started_at: datetime) -> ToolTiming:
    return ToolTiming(tool_name=tool_name, started_at=started_at, ended_at=_now_utc())


def _yf_source(symbol: str) -> SourceRef:
    return SourceRef(
        url=f"https://finance.yahoo.com/quote/{symbol}",  # type: ignore[arg-type]
        title=f"Yahoo Finance: {symbol}",
        fetched_at=_now_utc(),
    )


def _fmp_source(symbol: str) -> SourceRef:
    return SourceRef(
        url=f"https://financialmodelingprep.com/financial-statements/{symbol}",  # type: ignore[arg-type]
        title=f"FMP: {symbol}",
        fetched_at=_now_utc(),
    )


async def _run_sync(fn, *args):
    """Run a blocking function in the default thread-pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


async def _yf_info(symbol: str) -> dict:
    """Fetch yfinance Ticker.info dict; returns {} on any error."""
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).info

    try:
        return await _run_sync(_fetch) or {}
    except Exception as exc:
        logger.warning("yfinance info(%s) failed: %s", symbol, exc)
        return {}


def _compute_technicals(df) -> dict[str, float | None]:
    """Compute SMA/RSI/ATR/volume indicators from a yfinance history DataFrame."""
    import pandas as pd

    if df is None or df.empty:
        return {}

    closes = df["Close"]
    result: dict[str, float | None] = {}

    def _sma(n: int) -> float | None:
        if len(closes) >= n:
            val = closes.rolling(n).mean().iloc[-1]
            return round(float(val), 4) if not pd.isna(val) else None
        return None

    result["sma_20"] = _sma(20)
    result["sma_50"] = _sma(50)
    result["sma_200"] = _sma(200)

    # RSI(14) via Wilder's smoothing approximation
    if len(closes) >= 16:
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        avg_gain = float(gain.iloc[-1])
        avg_loss = float(loss.iloc[-1])
        if avg_loss == 0:
            result["rsi_14"] = 100.0
        else:
            rs = avg_gain / avg_loss
            result["rsi_14"] = round(100 - 100 / (1 + rs), 2)
    else:
        result["rsi_14"] = None

    # Volume avg(20d)
    if "Volume" in df.columns and len(df) >= 20:
        val = df["Volume"].rolling(20).mean().iloc[-1]
        result["volume_avg_20d"] = round(float(val), 0) if not pd.isna(val) else None
    else:
        result["volume_avg_20d"] = None

    # ATR(14)
    if len(df) >= 15 and {"High", "Low"}.issubset(df.columns):
        high_low = df["High"] - df["Low"]
        high_prev = (df["High"] - df["Close"].shift()).abs()
        low_prev = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
        atr_val = tr.rolling(14).mean().iloc[-1]
        result["atr_14"] = round(float(atr_val), 4) if not pd.isna(atr_val) else None
    else:
        result["atr_14"] = None

    # 1-day % change
    if len(closes) >= 2:
        result["pct_change_1d"] = round(float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100), 4)
    else:
        result["pct_change_1d"] = None

    # 5-day % change
    if len(closes) >= 6:
        result["pct_change_5d"] = round(float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100), 4)
    else:
        result["pct_change_5d"] = None

    return result


# ---------------------------------------------------------------------------
# yfinance Tools — Market Action
# ---------------------------------------------------------------------------

async def get_ohlcv(
    symbol: str,
    period: str = "3mo",
    interval: str = "1d",
) -> ToolResult[list[OHLCVBar]]:
    """Fetch OHLCV candlestick bars for charting.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"AAPL"``).
    period:
        History window — any yfinance period string (``1d``, ``5d``, ``1mo``,
        ``3mo``, ``6mo``, ``1y``, ``2y``, ``5y``, ``10y``, ``ytd``, ``max``).
    interval:
        Bar granularity — any yfinance interval string (``1m``, ``2m``, ``5m``,
        ``15m``, ``30m``, ``60m``, ``90m``, ``1h``, ``1d``, ``5d``, ``1wk``,
        ``1mo``, ``3mo``).
    """
    started_at = _now_utc()
    tool_name = "get_ohlcv"

    try:
        import yfinance as yf

        def _fetch():
            return yf.Ticker(symbol.upper()).history(period=period, interval=interval)

        df = await _run_sync(_fetch)

        if df is None or df.empty:
            return ToolResult(
                data=[],
                timing=_timing(tool_name, started_at),
                success=False,
                error=f"No OHLCV data returned for {symbol!r} (period={period}, interval={interval})",
            )

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            bars.append(OHLCVBar(
                date=ts.date() if hasattr(ts, "date") else ts,
                open=round(float(row["Open"]), 4),
                high=round(float(row["High"]), 4),
                low=round(float(row["Low"]), 4),
                close=round(float(row["Close"]), 4),
                volume=int(row.get("Volume", 0) or 0),
            ))

        return ToolResult(
            data=bars,
            sources=[_yf_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("get_ohlcv(%s): %s", symbol, exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def get_technical_snapshot(symbol: str) -> ToolResult[TechnicalSnapshot]:
    """Compute SMA(20/50/200), RSI(14), ATR(14), and volume average from 1-year history.

    Returns a ``TechnicalSnapshot`` for cross-referencing screening results or
    providing entry-setup context after fundamental analysis.
    """
    started_at = _now_utc()
    tool_name = "get_technical_snapshot"

    try:
        import yfinance as yf

        def _fetch():
            return yf.Ticker(symbol.upper()).history(period="1y")

        df = await _run_sync(_fetch)
        technicals = _compute_technicals(df)

        price = 0.0
        if df is not None and not df.empty:
            price = round(float(df["Close"].iloc[-1]), 4)

        snapshot = TechnicalSnapshot(
            symbol=symbol.upper(),
            price=price,
            **technicals,
        )

        return ToolResult(
            data=snapshot,
            sources=[_yf_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("get_technical_snapshot(%s): %s", symbol, exc)
        return ToolResult(
            data=TechnicalSnapshot(symbol=symbol.upper(), price=0.0),
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def validate_ticker(symbol: str) -> bool:
    """Return ``True`` if the symbol resolves to a real security via yfinance.

    Uses the same heuristic as ``routers/ticker.py``:
    ``quoteType`` present OR (has identity fields AND has a price).
    """
    info = await _yf_info(symbol)
    if not info:
        return False
    quote_type = info.get("quoteType")
    has_identity = bool(
        info.get("symbol") or info.get("longName") or info.get("shortName")
    )
    has_price = (
        info.get("currentPrice") is not None
        or info.get("regularMarketPrice") is not None
    )
    return bool(quote_type) or (has_identity and has_price)


async def detect_anomalies(
    symbols: list[str],
    price_drop_threshold: float = 0.05,
    volume_spike_multiplier: float = 2.0,
    gap_down_threshold: float = 0.03,
) -> list[AnomalySignal]:
    """Scan a list of symbols for intraday technical anomalies.

    Parameters
    ----------
    symbols:
        Ticker symbols to monitor.
    price_drop_threshold:
        Fraction single-day decline to trigger a ``price_drop`` signal
        (default ``0.05`` = 5 %).
    volume_spike_multiplier:
        Ratio of today's volume to the 20-day average that triggers a
        ``volume_spike`` signal (default ``2.0`` = 2×).
    gap_down_threshold:
        Fraction overnight gap-down (open vs. prior close) to trigger a
        ``gap_down`` signal (default ``0.03`` = 3 %).
    """
    import yfinance as yf

    signals: list[AnomalySignal] = []
    now = _now_utc()

    for symbol in symbols:
        try:
            def _fetch(sym: str = symbol):
                return yf.Ticker(sym.upper()).history(period="25d")

            df = await _run_sync(_fetch)
            if df is None or len(df) < 2:
                continue

            closes = df["Close"]
            volumes = df["Volume"]
            opens = df["Open"]

            latest_close = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2])
            latest_open = float(opens.iloc[-1])
            latest_volume = float(volumes.iloc[-1])

            window = min(20, len(volumes) - 1)
            vol_avg_20 = float(volumes.iloc[:-1].rolling(window).mean().iloc[-1])

            # --- Price drop ---
            day_return = (latest_close - prev_close) / prev_close
            if day_return <= -price_drop_threshold:
                signals.append(AnomalySignal(
                    symbol=symbol.upper(),
                    signal_type="price_drop",
                    magnitude=round(day_return, 4),
                    detected_at=now,
                    context_summary=(
                        f"{symbol} dropped {abs(day_return) * 100:.1f}% today, "
                        f"from ${prev_close:.2f} to ${latest_close:.2f}."
                    ),
                ))

            # --- Gap down ---
            gap = (latest_open - prev_close) / prev_close
            if gap <= -gap_down_threshold:
                signals.append(AnomalySignal(
                    symbol=symbol.upper(),
                    signal_type="gap_down",
                    magnitude=round(gap, 4),
                    detected_at=now,
                    context_summary=(
                        f"{symbol} gapped down {abs(gap) * 100:.1f}% at open "
                        f"(${latest_open:.2f} vs. prior close ${prev_close:.2f})."
                    ),
                ))

            # --- Volume spike ---
            if vol_avg_20 > 0:
                vol_ratio = latest_volume / vol_avg_20
                if vol_ratio >= volume_spike_multiplier:
                    signals.append(AnomalySignal(
                        symbol=symbol.upper(),
                        signal_type="volume_spike",
                        magnitude=round(vol_ratio, 2),
                        detected_at=now,
                        context_summary=(
                            f"{symbol} volume is {vol_ratio:.1f}× its 20-day average "
                            f"({int(latest_volume):,} vs. {int(vol_avg_20):,})."
                        ),
                    ))

        except Exception as exc:
            logger.warning("detect_anomalies: error scanning %s: %s", symbol, exc)

    return signals


# ---------------------------------------------------------------------------
# FMP Tools — Fundamental Research  (yfinance fallback on FMPUnavailableError)
# ---------------------------------------------------------------------------

async def get_company_profile(symbol: str) -> ToolResult[FMPCompanyProfile]:
    """Fetch company profile from FMP.

    Fallback: yfinance ``Ticker.info`` — CEO and IPO date will be ``None``.
    """
    started_at = _now_utc()
    tool_name = "get_company_profile"

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get(f"/profile?symbol={symbol.upper()}")

        items = data if isinstance(data, list) else [data]
        if not items:
            raise ValueError("Empty FMP profile response")

        item = items[0]
        ipo_date = None
        if item.get("ipoDate"):
            try:
                ipo_date = date.fromisoformat(item["ipoDate"])
            except ValueError:
                pass

        profile = FMPCompanyProfile(
            symbol=symbol.upper(),
            name=item.get("companyName"),
            sector=item.get("sector"),
            industry=item.get("industry"),
            market_cap=item.get("mktCap"),
            description=item.get("description"),
            ceo=item.get("ceo"),
            ipo_date=ipo_date,
            exchange=item.get("exchangeShortName"),
        )

        return ToolResult(
            data=profile,
            sources=[_fmp_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        logger.warning("get_company_profile: FMP unavailable for %s — yfinance fallback. %s", symbol, exc)
        info = await _yf_info(symbol)
        profile = FMPCompanyProfile(
            symbol=symbol.upper(),
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=info.get("marketCap"),
            description=info.get("longBusinessSummary"),
            exchange=info.get("exchange"),
        )
        return ToolResult(
            data=profile,
            sources=[_yf_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
            error=f"Degraded: FMP unavailable ({exc}). CEO and IPO date omitted (yfinance fallback).",
        )

    except Exception as exc:
        logger.error("get_company_profile: unexpected error for %s: %s", symbol, exc)
        return ToolResult(
            data=FMPCompanyProfile(symbol=symbol.upper()),
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def get_financial_statements(
    symbol: str,
    period: str = "annual",
    limit: int = 4,
) -> ToolResult[list[IncomeStatementSummary]]:
    """Fetch income statements from FMP.

    Fallback: yfinance ``Ticker.financials`` — EPS and operating margin omitted.

    Parameters
    ----------
    period: ``"annual"`` or ``"quarter"``.
    limit:  Number of periods to return.
    """
    started_at = _now_utc()
    tool_name = "get_financial_statements"

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get(
                f"/income-statement?symbol={symbol.upper()}",
                params={"period": period, "limit": limit},
            )

        rows = data if isinstance(data, list) else []
        statements: list[IncomeStatementSummary] = []
        for item in rows:
            rev = item.get("revenue")
            gross = item.get("grossProfit")
            op_income = item.get("operatingIncome")
            statements.append(IncomeStatementSummary(
                symbol=symbol.upper(),
                period=item.get("date", ""),
                revenue=rev,
                net_income=item.get("netIncome"),
                eps=item.get("eps"),
                gross_margin=(gross / rev) if rev and gross else None,
                operating_margin=(op_income / rev) if rev and op_income else None,
            ))

        return ToolResult(
            data=statements,
            sources=[_fmp_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        logger.warning("get_financial_statements: FMP unavailable for %s — yfinance fallback. %s", symbol, exc)
        try:
            import pandas as pd
            import yfinance as yf

            def _fetch():
                return yf.Ticker(symbol.upper()).financials

            fin = await _run_sync(_fetch)
            statements = []
            if fin is not None and not fin.empty:
                for col in list(fin.columns)[:limit]:
                    def _safe(row: str):
                        if row in fin.index:
                            v = fin.loc[row, col]
                            return float(v) if v is not None and not pd.isna(v) else None
                        return None

                    rev = _safe("Total Revenue")
                    gross = _safe("Gross Profit")
                    statements.append(IncomeStatementSummary(
                        symbol=symbol.upper(),
                        period=str(col.date()) if hasattr(col, "date") else str(col),
                        revenue=rev,
                        net_income=_safe("Net Income"),
                        gross_margin=(gross / rev) if rev and gross and rev != 0 else None,
                    ))

            return ToolResult(
                data=statements,
                sources=[_yf_source(symbol)],
                timing=_timing(tool_name, started_at),
                success=True,
                error=f"Degraded: FMP unavailable ({exc}). EPS and operating margin omitted (yfinance fallback).",
            )
        except Exception as yf_exc:
            return ToolResult(
                data=[],
                timing=_timing(tool_name, started_at),
                success=False,
                error=f"FMP unavailable ({exc}); yfinance fallback also failed: {yf_exc}",
            )

    except Exception as exc:
        logger.error("get_financial_statements: error for %s: %s", symbol, exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def get_balance_sheet(
    symbol: str,
    period: str = "annual",
    limit: int = 4,
) -> ToolResult[list[BalanceSheetSummary]]:
    """Fetch balance sheets from FMP.

    Fallback: yfinance ``Ticker.balance_sheet`` — ``book_value_per_share`` omitted.
    """
    started_at = _now_utc()
    tool_name = "get_balance_sheet"

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get(
                f"/balance-sheet-statement?symbol={symbol.upper()}",
                params={"period": period, "limit": limit},
            )

        rows = data if isinstance(data, list) else []
        sheets: list[BalanceSheetSummary] = []
        for item in rows:
            sheets.append(BalanceSheetSummary(
                symbol=symbol.upper(),
                period=item.get("date", ""),
                total_assets=item.get("totalAssets"),
                total_debt=item.get("totalDebt"),
                cash=item.get("cashAndCashEquivalents"),
                book_value_per_share=item.get("bookValuePerShare"),
            ))

        return ToolResult(
            data=sheets,
            sources=[_fmp_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        logger.warning("get_balance_sheet: FMP unavailable for %s — yfinance fallback. %s", symbol, exc)
        try:
            import pandas as pd
            import yfinance as yf

            def _fetch():
                return yf.Ticker(symbol.upper()).balance_sheet

            bs = await _run_sync(_fetch)
            sheets = []
            if bs is not None and not bs.empty:
                for col in list(bs.columns)[:limit]:
                    def _safe(row: str, col=col):
                        if row in bs.index:
                            v = bs.loc[row, col]
                            return float(v) if v is not None and not pd.isna(v) else None
                        return None

                    sheets.append(BalanceSheetSummary(
                        symbol=symbol.upper(),
                        period=str(col.date()) if hasattr(col, "date") else str(col),
                        total_assets=_safe("Total Assets"),
                        total_debt=_safe("Total Debt") or _safe("Long Term Debt"),
                        cash=_safe("Cash And Cash Equivalents") or _safe("Cash"),
                    ))

            return ToolResult(
                data=sheets,
                sources=[_yf_source(symbol)],
                timing=_timing(tool_name, started_at),
                success=True,
                error=f"Degraded: FMP unavailable ({exc}). Book value per share omitted (yfinance fallback).",
            )
        except Exception as yf_exc:
            return ToolResult(
                data=[],
                timing=_timing(tool_name, started_at),
                success=False,
                error=f"FMP unavailable ({exc}); yfinance fallback also failed: {yf_exc}",
            )

    except Exception as exc:
        logger.error("get_balance_sheet: error for %s: %s", symbol, exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def get_institutional_holders(symbol: str) -> ToolResult[list[InstitutionalHolder]]:
    """Fetch institutional ownership from FMP.

    Fallback: yfinance ``Ticker.institutional_holders`` — ``change_pct`` omitted.
    """
    started_at = _now_utc()
    tool_name = "get_institutional_holders"

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get(f"/institutional-holder/{symbol.upper()}")

        rows = data if isinstance(data, list) else []
        holders: list[InstitutionalHolder] = []
        for item in rows:
            holders.append(InstitutionalHolder(
                holder_name=item.get("holder", ""),
                shares=item.get("shares"),
                pct_ownership=item.get("weightedPercent") or item.get("percentage"),
                change_pct=item.get("change"),
            ))

        return ToolResult(
            data=holders,
            sources=[_fmp_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        logger.warning("get_institutional_holders: FMP unavailable for %s — yfinance fallback. %s", symbol, exc)
        try:
            import yfinance as yf

            def _fetch():
                return yf.Ticker(symbol.upper()).institutional_holders

            df = await _run_sync(_fetch)
            holders = []
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    shares_val = row.get("Shares")
                    pct_val = row.get("% Out")
                    holders.append(InstitutionalHolder(
                        holder_name=str(row.get("Holder", "")),
                        shares=int(shares_val) if shares_val is not None else None,
                        pct_ownership=float(pct_val) if pct_val is not None else None,
                    ))

            return ToolResult(
                data=holders,
                sources=[_yf_source(symbol)],
                timing=_timing(tool_name, started_at),
                success=True,
                error=f"Degraded: FMP unavailable ({exc}). Ownership change data omitted (yfinance fallback).",
            )
        except Exception as yf_exc:
            return ToolResult(
                data=[],
                timing=_timing(tool_name, started_at),
                success=False,
                error=f"FMP unavailable ({exc}); yfinance fallback also failed: {yf_exc}",
            )

    except Exception as exc:
        logger.error("get_institutional_holders: error for %s: %s", symbol, exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def get_peers(symbol: str) -> ToolResult[PeerComparison]:
    """Fetch peer tickers from FMP.

    Fallback: yfinance sector/industry info — peer list will be empty.
    """
    started_at = _now_utc()
    tool_name = "get_peers"

    from clients.fmp import FmpClient, FMPUnavailableError

    try:
        async with FmpClient() as fmp:
            data = await fmp.get("/stock-peers", params={"symbol": symbol.upper()})

        items = data if isinstance(data, list) else [data]
        peers_list: list[str] = []
        if items:
            peers_list = [
                p for p in (items[0].get("peersList") or [])
                if p and p.upper() != symbol.upper()
            ]

        # FMP /stock_peers doesn't include sector/industry — fetch from profile
        sector = industry = None
        try:
            profile_result = await get_company_profile(symbol)
            if profile_result.success and profile_result.data:
                sector = profile_result.data.sector
                industry = profile_result.data.industry
        except Exception:
            pass

        return ToolResult(
            data=PeerComparison(
                symbol=symbol.upper(),
                peers=peers_list,
                sector=sector,
                industry=industry,
            ),
            sources=[_fmp_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        logger.warning("get_peers: FMP unavailable for %s — yfinance fallback. %s", symbol, exc)
        info = await _yf_info(symbol)
        return ToolResult(
            data=PeerComparison(
                symbol=symbol.upper(),
                peers=[],
                sector=info.get("sector"),
                industry=info.get("industry"),
            ),
            sources=[_yf_source(symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
            error=f"Degraded: FMP unavailable ({exc}). Peer list unavailable; sector/industry from yfinance only.",
        )

    except Exception as exc:
        logger.error("get_peers: error for %s: %s", symbol, exc)
        return ToolResult(
            data=PeerComparison(symbol=symbol.upper(), peers=[]),
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def screen_stocks(
    criteria: dict[str, Any],
    limit: int = 20,
) -> ToolResult[list[ScreeningHit]]:
    """Screen stocks using FMP's stock screener endpoint.

    Parameters
    ----------
    criteria:
        FMP screener query parameters, e.g.::

            {
                "marketCapMoreThan": 1_000_000_000,
                "peRatioLowerThan": 15,
                "sector": "Technology",
                "country": "US",
            }

    limit:
        Maximum number of results to return.

    Note
    ----
    yfinance has no equivalent screener. If FMP is unavailable this function
    returns ``success=False`` rather than a degraded result.
    """
    started_at = _now_utc()
    tool_name = "screen_stocks"

    from clients.fmp import FmpClient, FMPUnavailableError

    # Allowlist of safe FMP screener parameters to prevent query injection
    _SCREEN_ALLOWLIST = {
        "marketCapMoreThan", "marketCapLowerThan",
        "priceMoreThan", "priceLowerThan",
        "betaMoreThan", "betaLowerThan",
        "volumeMoreThan", "volumeLowerThan",
        "dividendMoreThan", "dividendLowerThan",
        "isEtf", "isActivelyTrading",
        "sector", "industry", "country", "exchange",
        "peRatioMoreThan", "peRatioLowerThan",
    }

    try:
        params: dict[str, Any] = {
            k: v for k, v in criteria.items() if k in _SCREEN_ALLOWLIST
        }
        params["limit"] = limit

        async with FmpClient() as fmp:
            data = await fmp.get("/stock-screener", params=params)

        rows = data if isinstance(data, list) else []
        hits: list[ScreeningHit] = []
        for item in rows:
            hits.append(ScreeningHit(
                symbol=item.get("symbol", ""),
                name=item.get("companyName"),
                pe_ratio=item.get("pe"),
                price_to_book=item.get("priceBookValueRatio"),
                market_cap=item.get("marketCap"),
                sector=item.get("sector"),
            ))

        return ToolResult(
            data=hits,
            sources=[SourceRef(
                url="https://financialmodelingprep.com/api/v3/stock-screener",  # type: ignore[arg-type]
                title="FMP Stock Screener",
                fetched_at=_now_utc(),
            )],
            timing=_timing(tool_name, started_at),
            success=True,
        )

    except FMPUnavailableError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=(
                f"Stock screening requires the FMP API (currently unavailable: {exc}). "
                "yfinance has no equivalent screener endpoint. "
                "Add FMP_API_KEY to backend/.env to enable this feature."
            ),
        )

    except Exception as exc:
        logger.error("screen_stocks: %s", exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )
