from __future__ import annotations

import logging

import yfinance as yf
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import AnalysisSectionCache, UserPortfolio, Watchlist

logger = logging.getLogger(__name__)
router = APIRouter()

_POSITIVE_RATINGS = {"bullish", "strong", "uptrend", "buy"}
_NEGATIVE_RATINGS = {"bearish", "weak", "downtrend", "sell"}
_NEUTRAL_RATINGS = {"neutral", "fair", "hold", "sideways"}


def _score_rating(rating: str | None) -> int:
    if not rating:
        return 0
    value = rating.strip().lower()
    if value in _POSITIVE_RATINGS:
        return 1
    if value in _NEGATIVE_RATINGS:
        return -1
    if value in _NEUTRAL_RATINGS:
        return 0
    return 0


def _fetch_trailing_pe(symbol: str) -> float | None:
    try:
        info = yf.Ticker(symbol).info
        pe = info.get("trailingPE") if isinstance(info, dict) else None
        if pe is None:
            return None
        pe_float = float(pe)
        if pe_float <= 0:
            return None
        return pe_float
    except Exception as exc:
        logger.debug("Failed to fetch trailingPE for %s: %s", symbol, exc)
        return None


@router.get("/dashboard/metrics")
def get_dashboard_metrics(db: Session = Depends(get_db)):
    tickers: set[str] = set()

    for symbol, in db.query(UserPortfolio.symbol).all():
        if symbol:
            tickers.add(symbol.upper())

    for ticker, in db.query(Watchlist.ticker).all():
        if ticker:
            tickers.add(ticker.upper())

    ratings_rows = db.query(AnalysisSectionCache.ticker, AnalysisSectionCache.rating).all()
    agent_scores: dict[str, int] = {}
    for ticker, rating in ratings_rows:
        if not ticker:
            continue
        symbol = ticker.upper()
        tickers.add(symbol)
        agent_scores[symbol] = agent_scores.get(symbol, 0) + _score_rating(rating)

    metrics: list[dict[str, float | int | str | None]] = []
    for symbol in sorted(tickers):
        metrics.append(
            {
                "symbol": symbol,
                "trailing_pe": _fetch_trailing_pe(symbol),
                "agent_score": agent_scores.get(symbol, 0),
            }
        )

    best_pe = sorted(
        [row for row in metrics if isinstance(row["trailing_pe"], float)],
        key=lambda row: row["trailing_pe"],
    )[:5]

    best_agent_score = sorted(
        metrics,
        key=lambda row: (
            row["agent_score"],
            -(row["trailing_pe"] or 0.0),
        ),
        reverse=True,
    )[:5]

    return {
        "best_pe": best_pe,
        "best_agent_score": best_agent_score,
    }
