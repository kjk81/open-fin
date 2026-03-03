from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from clients.http_base import HttpClient
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
from tools.finance_fallback import FallbackChainExhaustedError, ProviderName, run_fallback_chain

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _timing(tool_name: str, started_at: datetime) -> ToolTiming:
    return ToolTiming(tool_name=tool_name, started_at=started_at, ended_at=_now_utc())


def _provider_source(provider: ProviderName, symbol: str) -> SourceRef:
    symbol = symbol.upper()
    if provider == "yfinance":
        url = f"https://finance.yahoo.com/quote/{symbol}"
        title = f"Yahoo Finance: {symbol}"
    elif provider == "fmp":
        url = f"https://financialmodelingprep.com/financial-statements/{symbol}"
        title = f"FMP: {symbol}"
    elif provider == "eodhd":
        url = f"https://eodhistoricaldata.com"
        title = f"EODHD: {symbol}"
    elif provider == "twelve_data":
        url = "https://twelvedata.com"
        title = f"Twelve Data: {symbol}"
    elif provider == "finnhub":
        url = "https://finnhub.io"
        title = f"Finnhub: {symbol}"
    elif provider == "alpha_vantage":
        url = "https://www.alphavantage.co"
        title = f"Alpha Vantage: {symbol}"
    else:
        url = "https://www.tiingo.com"
        title = f"Tiingo: {symbol}"

    return SourceRef(url=url, title=title, fetched_at=_now_utc())


async def _run_sync(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


async def _yf_info(symbol: str) -> dict:
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).info

    try:
        return await _run_sync(_fetch) or {}
    except Exception as exc:
        logger.warning("yfinance info(%s) failed: %s", symbol, exc)
        return {}


def _period_to_days(period: str) -> int:
    mapping = {
        "1d": 1,
        "5d": 5,
        "1mo": 30,
        "3mo": 90,
        "6mo": 180,
        "1y": 365,
        "2y": 730,
        "5y": 1825,
        "10y": 3650,
        "ytd": 365,
        "max": 3650,
    }
    return mapping.get(period, 90)


def _eodhd_symbol(symbol: str) -> str:
    s = symbol.upper()
    return s if "." in s else f"{s}.US"


async def _get_json(
    base_url: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_retries: int = 1,
) -> Any:
    async with HttpClient(base_url=base_url, timeout=timeout, max_retries=max_retries, headers=headers) as http:
        response = await http.get(path, params=params)
    return response.json()


def _compute_technicals(df) -> dict[str, float | None]:
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

    if "Volume" in df.columns and len(df) >= 20:
        val = df["Volume"].rolling(20).mean().iloc[-1]
        result["volume_avg_20d"] = round(float(val), 0) if not pd.isna(val) else None
    else:
        result["volume_avg_20d"] = None

    if len(df) >= 15 and {"High", "Low"}.issubset(df.columns):
        high_low = df["High"] - df["Low"]
        high_prev = (df["High"] - df["Close"].shift()).abs()
        low_prev = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
        atr_val = tr.rolling(14).mean().iloc[-1]
        result["atr_14"] = round(float(atr_val), 4) if not pd.isna(atr_val) else None
    else:
        result["atr_14"] = None

    if len(closes) >= 2:
        result["pct_change_1d"] = round(float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100), 4)
    else:
        result["pct_change_1d"] = None

    if len(closes) >= 6:
        result["pct_change_5d"] = round(float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100), 4)
    else:
        result["pct_change_5d"] = None

    return result


async def _ohlcv_from_yfinance(symbol: str, period: str, interval: str) -> list[OHLCVBar]:
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).history(period=period, interval=interval)

    df = await _run_sync(_fetch)
    if df is None or df.empty:
        raise ValueError("No yfinance OHLCV data")

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
    return bars


async def _ohlcv_from_eodhd(symbol: str, period: str) -> list[OHLCVBar]:
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("EODHD_API_TOKEN missing")

    end_date = date.today()
    start_date = end_date - timedelta(days=_period_to_days(period))
    data = await _get_json(
        "https://eodhistoricaldata.com",
        f"/api/eod/{_eodhd_symbol(symbol)}",
        params={
            "api_token": token,
            "fmt": "json",
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "period": "d",
        },
    )

    rows = data if isinstance(data, list) else []
    if not rows:
        raise ValueError("No EODHD OHLCV data")

    return [
        OHLCVBar(
            date=date.fromisoformat(row["date"]),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=int(row.get("volume", 0) or 0),
        )
        for row in rows
        if row.get("date")
    ]


async def _ohlcv_from_fmp(symbol: str, period: str) -> list[OHLCVBar]:
    from clients.fmp import FmpClient

    days = min(_period_to_days(period), 3650)
    async with FmpClient() as fmp:
        data = await fmp.get(f"/historical-price-full/{symbol.upper()}", params={"timeseries": days})

    hist = (data or {}).get("historical", []) if isinstance(data, dict) else []
    if not hist:
        raise ValueError("No FMP OHLCV data")

    bars: list[OHLCVBar] = []
    for row in hist:
        if not row.get("date"):
            continue
        bars.append(OHLCVBar(
            date=date.fromisoformat(row["date"]),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=int(row.get("volume", 0) or 0),
        ))

    if not bars:
        raise ValueError("No FMP OHLCV bars")
    return bars


async def _ohlcv_from_alpha_vantage(symbol: str, period: str) -> list[OHLCVBar]:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY missing")

    data = await _get_json(
        "https://www.alphavantage.co",
        "/query",
        params={
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol.upper(),
            "outputsize": "full",
            "apikey": api_key,
        },
    )

    series = data.get("Time Series (Daily)", {}) if isinstance(data, dict) else {}
    if not series:
        raise ValueError("No Alpha Vantage OHLCV data")

    limit = _period_to_days(period)
    bars: list[OHLCVBar] = []
    for dt, row in list(series.items())[:limit]:
        bars.append(OHLCVBar(
            date=date.fromisoformat(dt),
            open=float(row.get("1. open", 0.0)),
            high=float(row.get("2. high", 0.0)),
            low=float(row.get("3. low", 0.0)),
            close=float(row.get("4. close", 0.0)),
            volume=int(float(row.get("6. volume", 0.0))),
        ))

    if not bars:
        raise ValueError("No Alpha Vantage OHLCV bars")
    return bars


async def _ohlcv_from_tiingo(symbol: str, period: str) -> list[OHLCVBar]:
    token = os.environ.get("TIINGO_API_KEY", "").strip()
    if not token:
        raise ValueError("TIINGO_API_KEY missing")

    end_date = date.today()
    start_date = end_date - timedelta(days=_period_to_days(period))
    data = await _get_json(
        "https://api.tiingo.com",
        f"/tiingo/daily/{symbol.upper()}/prices",
        params={
            "token": token,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "resampleFreq": "daily",
        },
    )

    rows = data if isinstance(data, list) else []
    if not rows:
        raise ValueError("No Tiingo OHLCV data")

    bars: list[OHLCVBar] = []
    for row in rows:
        dt_raw = row.get("date", "")
        if not dt_raw:
            continue
        bars.append(OHLCVBar(
            date=date.fromisoformat(dt_raw[:10]),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=int(row.get("volume", 0) or 0),
        ))

    if not bars:
        raise ValueError("No Tiingo OHLCV bars")
    return bars


async def _ohlcv_from_twelve_data(symbol: str, interval: str) -> list[OHLCVBar]:
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY missing")

    data = await _get_json(
        "https://api.twelvedata.com",
        "/time_series",
        params={
            "symbol": symbol.upper(),
            "interval": interval,
            "apikey": api_key,
            "outputsize": 200,
            "format": "JSON",
        },
    )

    rows = data.get("values", []) if isinstance(data, dict) else []
    if not rows:
        raise ValueError("No Twelve Data OHLCV data")

    bars: list[OHLCVBar] = []
    for row in rows:
        dt_raw = row.get("datetime", "")
        if not dt_raw:
            continue
        bars.append(OHLCVBar(
            date=date.fromisoformat(dt_raw[:10]),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=int(float(row.get("volume", 0.0))),
        ))

    if not bars:
        raise ValueError("No Twelve Data OHLCV bars")
    return bars


async def _ohlcv_from_finnhub(symbol: str, period: str) -> list[OHLCVBar]:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        raise ValueError("FINNHUB_API_KEY missing")

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    start_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=_period_to_days(period))).timestamp())
    data = await _get_json(
        "https://finnhub.io/api/v1",
        "/stock/candle",
        params={
            "symbol": symbol.upper(),
            "resolution": "D",
            "from": start_ts,
            "to": now_ts,
            "token": api_key,
        },
    )

    status = data.get("s") if isinstance(data, dict) else None
    if status != "ok":
        raise ValueError("No Finnhub OHLCV data")

    opens = data.get("o", [])
    highs = data.get("h", [])
    lows = data.get("l", [])
    closes = data.get("c", [])
    volumes = data.get("v", [])
    times = data.get("t", [])

    bars: list[OHLCVBar] = []
    for idx, ts in enumerate(times):
        bars.append(OHLCVBar(
            date=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
            open=float(opens[idx]),
            high=float(highs[idx]),
            low=float(lows[idx]),
            close=float(closes[idx]),
            volume=int(volumes[idx]),
        ))

    if not bars:
        raise ValueError("No Finnhub OHLCV bars")
    return bars


def _profile_from_fmp_item(symbol: str, item: dict[str, Any]) -> FMPCompanyProfile:
    ipo_date = None
    if item.get("ipoDate"):
        try:
            ipo_date = date.fromisoformat(item["ipoDate"])
        except ValueError:
            ipo_date = None

    return FMPCompanyProfile(
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


async def _profile_fmp(symbol: str) -> FMPCompanyProfile:
    from clients.fmp import FmpClient

    async with FmpClient() as fmp:
        data = await fmp.get(f"/profile?symbol={symbol.upper()}")

    items = data if isinstance(data, list) else [data]
    if not items:
        raise ValueError("Empty FMP profile response")
    return _profile_from_fmp_item(symbol, items[0])


async def _profile_yfinance(symbol: str) -> FMPCompanyProfile:
    info = await _yf_info(symbol)
    if not info:
        raise ValueError("Empty yfinance profile")
    return FMPCompanyProfile(
        symbol=symbol.upper(),
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        market_cap=info.get("marketCap"),
        description=info.get("longBusinessSummary"),
        ceo=info.get("companyOfficers", [{}])[0].get("name") if info.get("companyOfficers") else None,
        exchange=info.get("exchange"),
    )


async def _profile_eodhd(symbol: str) -> FMPCompanyProfile:
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("EODHD_API_TOKEN missing")

    data = await _get_json(
        "https://eodhistoricaldata.com",
        f"/api/fundamentals/{_eodhd_symbol(symbol)}",
        params={"api_token": token, "fmt": "json"},
    )

    general = data.get("General", {}) if isinstance(data, dict) else {}
    if not general:
        raise ValueError("No EODHD profile data")

    ipo_date = None
    if general.get("IPODate"):
        try:
            ipo_date = date.fromisoformat(general["IPODate"])
        except ValueError:
            ipo_date = None

    return FMPCompanyProfile(
        symbol=symbol.upper(),
        name=general.get("Name"),
        sector=general.get("Sector"),
        industry=general.get("Industry"),
        market_cap=general.get("MarketCapitalization"),
        description=general.get("Description"),
        ceo=general.get("CEO"),
        ipo_date=ipo_date,
        exchange=general.get("Exchange"),
    )


async def _profile_alpha(symbol: str) -> FMPCompanyProfile:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY missing")

    data = await _get_json(
        "https://www.alphavantage.co",
        "/query",
        params={"function": "OVERVIEW", "symbol": symbol.upper(), "apikey": api_key},
    )

    if not isinstance(data, dict) or not data.get("Symbol"):
        raise ValueError("No Alpha Vantage profile")

    ipo_date = None
    if data.get("IPODate"):
        try:
            ipo_date = date.fromisoformat(data["IPODate"])
        except ValueError:
            ipo_date = None

    market_cap = None
    if data.get("MarketCapitalization"):
        try:
            market_cap = float(data["MarketCapitalization"])
        except ValueError:
            market_cap = None

    return FMPCompanyProfile(
        symbol=symbol.upper(),
        name=data.get("Name"),
        sector=data.get("Sector"),
        industry=data.get("Industry"),
        market_cap=market_cap,
        description=data.get("Description"),
        exchange=data.get("Exchange"),
        ipo_date=ipo_date,
    )


async def _profile_finnhub(symbol: str) -> FMPCompanyProfile:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        raise ValueError("FINNHUB_API_KEY missing")

    data = await _get_json(
        "https://finnhub.io/api/v1",
        "/stock/profile2",
        params={"symbol": symbol.upper(), "token": api_key},
    )
    if not isinstance(data, dict) or not data.get("ticker"):
        raise ValueError("No Finnhub profile")

    return FMPCompanyProfile(
        symbol=symbol.upper(),
        name=data.get("name"),
        industry=data.get("finnhubIndustry"),
        market_cap=data.get("marketCapitalization"),
        exchange=data.get("exchange"),
    )


async def _profile_tiingo(symbol: str) -> FMPCompanyProfile:
    token = os.environ.get("TIINGO_API_KEY", "").strip()
    if not token:
        raise ValueError("TIINGO_API_KEY missing")

    data = await _get_json(
        "https://api.tiingo.com",
        f"/tiingo/daily/{symbol.upper()}",
        params={"token": token},
    )
    if not isinstance(data, dict) or not data.get("ticker"):
        raise ValueError("No Tiingo profile")

    return FMPCompanyProfile(
        symbol=symbol.upper(),
        name=data.get("name"),
        exchange=data.get("exchangeCode"),
        description=data.get("description"),
    )


def _income_from_table(symbol: str, table: list[dict[str, Any]], limit: int) -> list[IncomeStatementSummary]:
    rows: list[IncomeStatementSummary] = []
    for item in table[:limit]:
        rev = item.get("revenue")
        gross = item.get("grossProfit")
        op_income = item.get("operatingIncome")
        rows.append(IncomeStatementSummary(
            symbol=symbol.upper(),
            period=str(item.get("date", "")),
            revenue=rev,
            net_income=item.get("netIncome"),
            eps=item.get("eps"),
            gross_margin=(gross / rev) if rev and gross else None,
            operating_margin=(op_income / rev) if rev and op_income else None,
        ))
    return rows


async def _income_fmp(symbol: str, period: str, limit: int) -> list[IncomeStatementSummary]:
    from clients.fmp import FmpClient

    async with FmpClient() as fmp:
        data = await fmp.get(
            f"/income-statement?symbol={symbol.upper()}",
            params={"period": period, "limit": limit},
        )

    rows = data if isinstance(data, list) else []
    return _income_from_table(symbol, rows, limit)


async def _income_yfinance(symbol: str, limit: int) -> list[IncomeStatementSummary]:
    import pandas as pd
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).financials

    fin = await _run_sync(_fetch)
    output: list[IncomeStatementSummary] = []
    if fin is None or fin.empty:
        return output

    for col in list(fin.columns)[:limit]:
        def _safe(row: str):
            if row in fin.index:
                val = fin.loc[row, col]
                return float(val) if val is not None and not pd.isna(val) else None
            return None

        rev = _safe("Total Revenue")
        gross = _safe("Gross Profit")
        output.append(IncomeStatementSummary(
            symbol=symbol.upper(),
            period=str(col.date()) if hasattr(col, "date") else str(col),
            revenue=rev,
            net_income=_safe("Net Income"),
            gross_margin=(gross / rev) if rev and gross and rev != 0 else None,
        ))

    if not output:
        raise ValueError("No yfinance income statements")
    return output


async def _income_eodhd(symbol: str, period: str, limit: int) -> list[IncomeStatementSummary]:
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("EODHD_API_TOKEN missing")

    data = await _get_json(
        "https://eodhistoricaldata.com",
        f"/api/fundamentals/{_eodhd_symbol(symbol)}",
        params={"api_token": token, "fmt": "json"},
    )

    section = "yearly" if period == "annual" else "quarterly"
    income = ((data or {}).get("Financials", {}).get("Income_Statement", {}).get(section, {}))
    if not isinstance(income, dict) or not income:
        raise ValueError("No EODHD income statements")

    rows: list[IncomeStatementSummary] = []
    for dt, row in list(income.items())[:limit]:
        rev = row.get("totalRevenue")
        gross = row.get("grossProfit")
        op_income = row.get("operatingIncome")
        rows.append(IncomeStatementSummary(
            symbol=symbol.upper(),
            period=dt,
            revenue=rev,
            net_income=row.get("netIncome"),
            eps=row.get("eps"),
            gross_margin=(gross / rev) if rev and gross else None,
            operating_margin=(op_income / rev) if rev and op_income else None,
        ))

    if not rows:
        raise ValueError("No EODHD income rows")
    return rows


async def _income_alpha(symbol: str, period: str, limit: int) -> list[IncomeStatementSummary]:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY missing")

    data = await _get_json(
        "https://www.alphavantage.co",
        "/query",
        params={"function": "INCOME_STATEMENT", "symbol": symbol.upper(), "apikey": api_key},
    )

    key = "annualReports" if period == "annual" else "quarterlyReports"
    reports = data.get(key, []) if isinstance(data, dict) else []
    if not reports:
        raise ValueError("No Alpha Vantage income statements")

    rows: list[IncomeStatementSummary] = []
    for row in reports[:limit]:
        rev = float(row["totalRevenue"]) if row.get("totalRevenue") not in (None, "None") else None
        gross = float(row["grossProfit"]) if row.get("grossProfit") not in (None, "None") else None
        op_income = float(row["operatingIncome"]) if row.get("operatingIncome") not in (None, "None") else None
        eps = float(row["reportedEPS"]) if row.get("reportedEPS") not in (None, "None") else None
        rows.append(IncomeStatementSummary(
            symbol=symbol.upper(),
            period=row.get("fiscalDateEnding", ""),
            revenue=rev,
            net_income=float(row["netIncome"]) if row.get("netIncome") not in (None, "None") else None,
            eps=eps,
            gross_margin=(gross / rev) if rev and gross else None,
            operating_margin=(op_income / rev) if rev and op_income else None,
        ))

    return rows


async def _balance_fmp(symbol: str, period: str, limit: int) -> list[BalanceSheetSummary]:
    from clients.fmp import FmpClient

    async with FmpClient() as fmp:
        data = await fmp.get(
            f"/balance-sheet-statement?symbol={symbol.upper()}",
            params={"period": period, "limit": limit},
        )

    rows = data if isinstance(data, list) else []
    out: list[BalanceSheetSummary] = []
    for item in rows[:limit]:
        out.append(BalanceSheetSummary(
            symbol=symbol.upper(),
            period=item.get("date", ""),
            total_assets=item.get("totalAssets"),
            total_debt=item.get("totalDebt"),
            cash=item.get("cashAndCashEquivalents"),
            book_value_per_share=item.get("bookValuePerShare"),
        ))
    return out


async def _balance_yfinance(symbol: str, limit: int) -> list[BalanceSheetSummary]:
    import pandas as pd
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).balance_sheet

    bs = await _run_sync(_fetch)
    if bs is None or bs.empty:
        raise ValueError("No yfinance balance sheet")

    out: list[BalanceSheetSummary] = []
    for col in list(bs.columns)[:limit]:
        def _safe(row: str):
            if row in bs.index:
                val = bs.loc[row, col]
                return float(val) if val is not None and not pd.isna(val) else None
            return None

        out.append(BalanceSheetSummary(
            symbol=symbol.upper(),
            period=str(col.date()) if hasattr(col, "date") else str(col),
            total_assets=_safe("Total Assets"),
            total_debt=_safe("Total Debt") or _safe("Long Term Debt"),
            cash=_safe("Cash And Cash Equivalents") or _safe("Cash"),
        ))
    return out


async def _balance_eodhd(symbol: str, period: str, limit: int) -> list[BalanceSheetSummary]:
    token = os.environ.get("EODHD_API_TOKEN", "").strip()
    if not token:
        raise ValueError("EODHD_API_TOKEN missing")

    data = await _get_json(
        "https://eodhistoricaldata.com",
        f"/api/fundamentals/{_eodhd_symbol(symbol)}",
        params={"api_token": token, "fmt": "json"},
    )

    section = "yearly" if period == "annual" else "quarterly"
    balance = ((data or {}).get("Financials", {}).get("Balance_Sheet", {}).get(section, {}))
    if not isinstance(balance, dict) or not balance:
        raise ValueError("No EODHD balance sheet")

    out: list[BalanceSheetSummary] = []
    for dt, row in list(balance.items())[:limit]:
        out.append(BalanceSheetSummary(
            symbol=symbol.upper(),
            period=dt,
            total_assets=row.get("totalAssets"),
            total_debt=row.get("totalDebt"),
            cash=row.get("cashAndShortTermInvestments") or row.get("cashAndCashEquivalents"),
            book_value_per_share=row.get("bookValue"),
        ))
    return out


async def _balance_alpha(symbol: str, period: str, limit: int) -> list[BalanceSheetSummary]:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY missing")

    data = await _get_json(
        "https://www.alphavantage.co",
        "/query",
        params={"function": "BALANCE_SHEET", "symbol": symbol.upper(), "apikey": api_key},
    )

    key = "annualReports" if period == "annual" else "quarterlyReports"
    reports = data.get(key, []) if isinstance(data, dict) else []
    if not reports:
        raise ValueError("No Alpha Vantage balance sheets")

    rows: list[BalanceSheetSummary] = []
    for row in reports[:limit]:
        rows.append(BalanceSheetSummary(
            symbol=symbol.upper(),
            period=row.get("fiscalDateEnding", ""),
            total_assets=float(row["totalAssets"]) if row.get("totalAssets") not in (None, "None") else None,
            total_debt=float(row["totalLiabilities"]) if row.get("totalLiabilities") not in (None, "None") else None,
            cash=float(row["cashAndCashEquivalentsAtCarryingValue"]) if row.get("cashAndCashEquivalentsAtCarryingValue") not in (None, "None") else None,
            book_value_per_share=float(row["commonStockSharesOutstanding"]) if row.get("commonStockSharesOutstanding") not in (None, "None") else None,
        ))

    return rows


async def _holders_fmp(symbol: str) -> list[InstitutionalHolder]:
    from clients.fmp import FmpClient

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
    if not holders:
        raise ValueError("No FMP institutional holders")
    return holders


async def _holders_yfinance(symbol: str) -> list[InstitutionalHolder]:
    import yfinance as yf

    def _fetch():
        return yf.Ticker(symbol.upper()).institutional_holders

    df = await _run_sync(_fetch)
    if df is None or df.empty:
        raise ValueError("No yfinance institutional holders")

    holders: list[InstitutionalHolder] = []
    for _, row in df.iterrows():
        shares_val = row.get("Shares")
        pct_val = row.get("% Out")
        holders.append(InstitutionalHolder(
            holder_name=str(row.get("Holder", "")),
            shares=int(shares_val) if shares_val is not None else None,
            pct_ownership=float(pct_val) if pct_val is not None else None,
        ))
    return holders


async def _peers_fmp(symbol: str) -> PeerComparison:
    from clients.fmp import FmpClient

    async with FmpClient() as fmp:
        data = await fmp.get("/stock-peers", params={"symbol": symbol.upper()})

    items = data if isinstance(data, list) else [data]
    peers_list: list[str] = []
    if items:
        peers_list = [p for p in (items[0].get("peersList") or []) if p and p.upper() != symbol.upper()]

    if not peers_list:
        raise ValueError("No FMP peers list")

    profile = await _profile_fmp(symbol)
    return PeerComparison(symbol=symbol.upper(), peers=peers_list, sector=profile.sector, industry=profile.industry)


async def _peers_finnhub(symbol: str) -> PeerComparison:
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        raise ValueError("FINNHUB_API_KEY missing")

    data = await _get_json(
        "https://finnhub.io/api/v1",
        "/stock/peers",
        params={"symbol": symbol.upper(), "token": api_key},
    )

    peers = [p for p in (data or []) if isinstance(p, str) and p.upper() != symbol.upper()]
    if not peers:
        raise ValueError("No Finnhub peers")

    return PeerComparison(symbol=symbol.upper(), peers=peers)


async def _screener_fmp(criteria: dict[str, Any], limit: int) -> list[ScreeningHit]:
    from clients.fmp import FmpClient

    allowlist = {
        "marketCapMoreThan", "marketCapLowerThan",
        "priceMoreThan", "priceLowerThan",
        "betaMoreThan", "betaLowerThan",
        "volumeMoreThan", "volumeLowerThan",
        "dividendMoreThan", "dividendLowerThan",
        "isEtf", "isActivelyTrading",
        "sector", "industry", "country", "exchange",
        "peRatioMoreThan", "peRatioLowerThan",
    }

    params: dict[str, Any] = {k: v for k, v in criteria.items() if k in allowlist}
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
    if not hits:
        raise ValueError("No screener hits")
    return hits


def _is_historical_interval(interval: str) -> bool:
    return interval in {"1d", "5d", "1wk", "1mo", "3mo"}


async def get_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> ToolResult[list[OHLCVBar]]:
    started_at = _now_utc()
    tool_name = "get_ohlcv"

    category = "historical" if _is_historical_interval(interval) else "price"
    endpoint_id = "ohlcv_bars"

    handlers = {
        "yfinance": lambda: _ohlcv_from_yfinance(symbol, period, interval),
        "eodhd": lambda: _ohlcv_from_eodhd(symbol, period),
        "fmp": lambda: _ohlcv_from_fmp(symbol, period),
        "alpha_vantage": lambda: _ohlcv_from_alpha_vantage(symbol, period),
        "tiingo": lambda: _ohlcv_from_tiingo(symbol, period),
        "twelve_data": lambda: _ohlcv_from_twelve_data(symbol, interval),
        "finnhub": lambda: _ohlcv_from_finnhub(symbol, period),
    }

    try:
        result = await run_fallback_chain(
            category=category,
            endpoint_id=endpoint_id,
            handlers=handlers,
            per_provider_timeout=18.0,
        )
        bars: list[OHLCVBar] = sorted(result.payload, key=lambda b: b.date)
        return ToolResult(
            data=bars,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "get_technical_snapshot"

    ohlcv_result = await get_ohlcv(symbol, period="1y", interval="1d")
    if not ohlcv_result.success or not ohlcv_result.data:
        return ToolResult(
            data=TechnicalSnapshot(symbol=symbol.upper(), price=0.0),
            timing=_timing(tool_name, started_at),
            success=False,
            error=ohlcv_result.error or "Unable to compute technicals from any free-tier provider.",
        )

    import pandas as pd

    df = pd.DataFrame([
        {
            "Open": bar.open,
            "High": bar.high,
            "Low": bar.low,
            "Close": bar.close,
            "Volume": bar.volume,
        }
        for bar in ohlcv_result.data
    ])

    technicals = _compute_technicals(df)
    price = round(float(ohlcv_result.data[-1].close), 4)

    snapshot = TechnicalSnapshot(symbol=symbol.upper(), price=price, **technicals)
    return ToolResult(
        data=snapshot,
        sources=ohlcv_result.sources,
        timing=_timing(tool_name, started_at),
        success=True,
    )


async def validate_ticker(symbol: str) -> bool:
    info = await _yf_info(symbol)
    if not info:
        return False
    quote_type = info.get("quoteType")
    has_identity = bool(info.get("symbol") or info.get("longName") or info.get("shortName"))
    has_price = info.get("currentPrice") is not None or info.get("regularMarketPrice") is not None
    return bool(quote_type) or (has_identity and has_price)


async def detect_anomalies(
    symbols: list[str],
    price_drop_threshold: float = 0.05,
    volume_spike_multiplier: float = 2.0,
    gap_down_threshold: float = 0.03,
) -> list[AnomalySignal]:
    signals: list[AnomalySignal] = []
    now = _now_utc()

    for symbol in symbols:
        try:
            ohlcv = await get_ohlcv(symbol, period="1mo", interval="1d")
            bars = ohlcv.data if ohlcv.success else []
            if len(bars) < 2:
                continue

            latest = bars[-1]
            prev = bars[-2]
            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]

            latest_close = float(latest.close)
            prev_close = float(prev.close)
            latest_open = float(latest.open)
            latest_volume = float(latest.volume)

            vol_window = min(20, len(volumes) - 1)
            vol_avg_20 = sum(volumes[-(vol_window + 1):-1]) / max(vol_window, 1)

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


async def get_company_profile(symbol: str) -> ToolResult[FMPCompanyProfile]:
    started_at = _now_utc()
    tool_name = "get_company_profile"

    handlers = {
        "fmp": lambda: _profile_fmp(symbol),
        "yfinance": lambda: _profile_yfinance(symbol),
        "eodhd": lambda: _profile_eodhd(symbol),
        "alpha_vantage": lambda: _profile_alpha(symbol),
        "finnhub": lambda: _profile_finnhub(symbol),
        "tiingo": lambda: _profile_tiingo(symbol),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="company_profile",
            handlers=handlers,
            per_provider_timeout=16.0,
        )
        return ToolResult(
            data=result.payload,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=FMPCompanyProfile(symbol=symbol.upper()),
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "get_financial_statements"

    handlers = {
        "fmp": lambda: _income_fmp(symbol, period, limit),
        "yfinance": lambda: _income_yfinance(symbol, limit),
        "eodhd": lambda: _income_eodhd(symbol, period, limit),
        "alpha_vantage": lambda: _income_alpha(symbol, period, limit),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="income_statement",
            handlers=handlers,
            per_provider_timeout=18.0,
        )
        return ToolResult(
            data=result.payload,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "get_balance_sheet"

    handlers = {
        "fmp": lambda: _balance_fmp(symbol, period, limit),
        "yfinance": lambda: _balance_yfinance(symbol, limit),
        "eodhd": lambda: _balance_eodhd(symbol, period, limit),
        "alpha_vantage": lambda: _balance_alpha(symbol, period, limit),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="balance_sheet",
            handlers=handlers,
            per_provider_timeout=18.0,
        )
        return ToolResult(
            data=result.payload,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "get_institutional_holders"

    handlers = {
        "fmp": lambda: _holders_fmp(symbol),
        "yfinance": lambda: _holders_yfinance(symbol),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="institutional_holders",
            handlers=handlers,
            per_provider_timeout=16.0,
        )
        return ToolResult(
            data=result.payload,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "get_peers"

    handlers = {
        "fmp": lambda: _peers_fmp(symbol),
        "finnhub": lambda: _peers_finnhub(symbol),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="peers",
            handlers=handlers,
            per_provider_timeout=16.0,
        )
        peer_comp: PeerComparison = result.payload

        if not peer_comp.sector or not peer_comp.industry:
            info = await _yf_info(symbol)
            if info:
                peer_comp = PeerComparison(
                    symbol=peer_comp.symbol,
                    peers=peer_comp.peers,
                    sector=peer_comp.sector or info.get("sector"),
                    industry=peer_comp.industry or info.get("industry"),
                )

        return ToolResult(
            data=peer_comp,
            sources=[_provider_source(result.provider, symbol)],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        info = await _yf_info(symbol)
        return ToolResult(
            data=PeerComparison(
                symbol=symbol.upper(),
                peers=[],
                sector=info.get("sector") if info else None,
                industry=info.get("industry") if info else None,
            ),
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
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
    started_at = _now_utc()
    tool_name = "screen_stocks"

    handlers = {
        "fmp": lambda: _screener_fmp(criteria, limit),
    }

    try:
        result = await run_fallback_chain(
            category="fundamentals",
            endpoint_id="stock_screener",
            handlers=handlers,
            per_provider_timeout=20.0,
        )

        return ToolResult(
            data=result.payload,
            sources=[_provider_source(result.provider, "SCREEN")],
            timing=_timing(tool_name, started_at),
            success=True,
        )
    except FallbackChainExhaustedError as exc:
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )
    except Exception as exc:
        logger.error("screen_stocks: %s", exc)
        return ToolResult(
            data=[],
            timing=_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )
