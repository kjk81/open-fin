import logging
import yfinance as yf
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ticker/{symbol}")
def get_ticker(symbol: str):
    symbol = symbol.upper()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or info.get("trailingPegRatio") is None and info.get("symbol") is None:
            # yfinance returns a dict with quoteType for valid tickers
            pass

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
    except Exception as exc:
        logger.error("Ticker lookup failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for {symbol}: {exc}")
