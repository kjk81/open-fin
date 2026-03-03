import logging
from datetime import datetime, timezone

import yfinance as yf
from fastapi import APIRouter, HTTPException

from tools.web import web_search

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ticker/{symbol}")
def get_ticker(symbol: str):
    symbol = symbol.upper()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        # Heuristic validity check: yfinance returns a dict, but invalid symbols
        # often come back empty or without basic quote fields.
        if not info or not isinstance(info, dict):
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

        quote_type = info.get("quoteType")
        has_identity = bool(info.get("symbol") or info.get("longName") or info.get("shortName"))
        has_price = info.get("currentPrice") is not None or info.get("regularMarketPrice") is not None
        if quote_type is None and not (has_identity and has_price):
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        return {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName"),
            "price": price,
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Ticker lookup failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for {symbol}: {exc}")


@router.get("/ticker/{symbol}/events")
async def get_ticker_events(symbol: str):
    symbol = symbol.upper()
    query = f"{symbol} stock news current events"
    search_result = await web_search(query=query, max_results=10)

    occurred_at = datetime.now(timezone.utc).isoformat()
    hits = search_result.data.hits if search_result.success else []
    provider = search_result.data.provider

    return [
        {
            "title": hit.title,
            "url": str(hit.url),
            "snippet": hit.snippet,
            "provider": provider,
            "rank": idx + 1,
            "occurred_at": occurred_at,
        }
        for idx, hit in enumerate(hits)
    ]
