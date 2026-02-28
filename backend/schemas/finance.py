"""Finance-specific Pydantic schemas for yfinance (Market Action) and FMP (Fundamental Research).

Data source labels
------------------
- (yfinance)   : polled from unofficial Yahoo Finance API — low latency, fragile schema
- (FMP)        : polled from Financial Modeling Prep — stable JSON, well-suited for LLMs
- (SEC EDGAR)  : polled from data.sec.gov — free, no auth, canonical regulatory data
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Market Action — yfinance outputs
# ---------------------------------------------------------------------------

class OHLCVBar(BaseModel):
    """A single OHLCV candlestick bar. (yfinance)"""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class TechnicalSnapshot(BaseModel):
    """Technical indicators computed from recent price history. (yfinance)"""

    symbol: str
    price: float
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    volume_avg_20d: float | None = None
    atr_14: float | None = None
    pct_change_1d: float | None = None
    pct_change_5d: float | None = None


class AnomalySignal(BaseModel):
    """A detected market anomaly for a single symbol. (yfinance)"""

    symbol: str
    signal_type: Literal["price_drop", "volume_spike", "gap_down"]
    magnitude: float       # e.g. -0.07 for a 7% drop; 2.5 for 2.5× volume spike
    detected_at: datetime
    context_summary: str   # Human-readable one-liner


# ---------------------------------------------------------------------------
# Fundamental Research — FMP outputs (yfinance fallback where noted)
# ---------------------------------------------------------------------------

class FMPCompanyProfile(BaseModel):
    """Company profile from FMP; yfinance fallback fields: name, sector, industry, market_cap, description, exchange."""

    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    description: str | None = None
    ceo: str | None = None           # FMP only — None on yfinance fallback
    ipo_date: date | None = None     # FMP only — None on yfinance fallback
    exchange: str | None = None


class IncomeStatementSummary(BaseModel):
    """Condensed income statement row from FMP; partial on yfinance fallback (no EPS / op. margin)."""

    symbol: str
    period: str              # ISO date string e.g. "2024-09-30" or quarter label
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None          # FMP only
    gross_margin: float | None = None  # ratio 0–1
    operating_margin: float | None = None  # ratio 0–1; FMP only


class BalanceSheetSummary(BaseModel):
    """Condensed balance sheet row from FMP; book_value_per_share is FMP only."""

    symbol: str
    period: str
    total_assets: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    book_value_per_share: float | None = None  # FMP only


class InstitutionalHolder(BaseModel):
    """A single institutional holder record. FMP provides change_pct; yfinance does not."""

    holder_name: str
    shares: int | None = None
    pct_ownership: float | None = None   # ratio 0–1
    change_pct: float | None = None      # FMP only: change from prior quarter


class PeerComparison(BaseModel):
    """Peer ticker list for a given symbol (FMP). On yfinance fallback peers list is empty."""

    symbol: str
    peers: list[str]
    sector: str | None = None
    industry: str | None = None


# ---------------------------------------------------------------------------
# Screening — combined yfinance + FMP output
# ---------------------------------------------------------------------------

class ScreeningHit(BaseModel):
    """A single stock matching the screening criteria from FMP's stock-screener."""

    symbol: str
    name: str | None = None
    pe_ratio: float | None = None
    price_to_book: float | None = None
    free_cash_flow_yield: float | None = None
    market_cap: float | None = None
    sector: str | None = None


class ScreeningResult(BaseModel):
    """FMP screener results with yfinance technicals cross-referenced per hit."""

    criteria_description: str
    hits: list[ScreeningHit]
    cross_ref_technicals: dict[str, TechnicalSnapshot] = {}  # keyed by symbol


# ---------------------------------------------------------------------------
# SEC EDGAR — 8-K regulatory filings
# ---------------------------------------------------------------------------

class Filing8K(BaseModel):
    """Metadata for a single 8-K (or 8-K/A) filing from SEC EDGAR."""

    accession_number: str          # e.g. "0000320193-24-000001"
    filed_date: date
    form_type: str                 # "8-K" or "8-K/A"
    items: list[str]               # e.g. ["Item 2.02", "Item 9.01"]
    filing_url: str
    company_name: str
    cik: str                       # zero-padded 10-digit CIK


class Filing8KDetail(BaseModel):
    """Full 8-K document text with extracted per-item sections."""

    filing: Filing8K
    full_text: str                         # truncated to ≤50 K chars for LLM consumption
    extracted_items: dict[str, str]        # {"Item 2.02": "text of that item ..."}
