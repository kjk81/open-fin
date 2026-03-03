"""Read-through layer for knowledge graph data used by the analysis endpoint.

Each function queries KGNode / KGEdge tables for pre-existing observations
and returns a structured dict when fresh data is available.  Freshness is
controlled by per-section TTLs:

    - fundamentals: 24 h
    - technical: 4 h
    - sentiment: 24 h

Returns ``None`` when insufficient or stale data is found, signalling the
caller to trigger a live LLM / tool invocation instead.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import KGEdge, KGNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Freshness thresholds
# ---------------------------------------------------------------------------

_FUNDAMENTALS_TTL = timedelta(hours=24)
_TECHNICAL_TTL = timedelta(hours=4)
_SENTIMENT_TTL = timedelta(hours=24)


def _cutoff(ttl: timedelta) -> datetime:
    return datetime.now(timezone.utc) - ttl


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_ticker_node(db: Session, ticker: str) -> KGNode | None:
    return (
        db.query(KGNode)
        .filter(
            KGNode.name == ticker.upper(),
            KGNode.node_type == "ticker",
            KGNode.is_deleted == False,  # noqa: E712
        )
        .first()
    )


def _parse_metadata(node: KGNode) -> dict[str, Any]:
    try:
        return json.loads(node.metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_fresh(node: KGNode, ttl: timedelta) -> bool:
    if node.updated_at is None:
        return False
    updated = node.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated > _cutoff(ttl)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_kg_fundamentals(ticker: str) -> dict[str, Any] | None:
    """Query KG for fundamental observations (revenue, EPS, margins, etc.).

    Returns a dict with ``source: "kg"`` on hit, or ``None`` on miss/stale.
    """
    db = SessionLocal()
    try:
        node = _get_ticker_node(db, ticker)
        if node is None or not _is_fresh(node, _FUNDAMENTALS_TTL):
            return None

        meta = _parse_metadata(node)

        # Look for fundamental-specific keys populated by upsert_from_tool_results
        wanted = {"revenue", "net_income", "eps", "gross_margin", "operating_margin",
                  "market_cap", "pe_ratio", "debt_to_equity", "sector", "industry"}
        found = {k: v for k, v in meta.items() if k in wanted and v is not None}

        if len(found) < 3:
            logger.debug("KG fundamentals miss for %s: only %d keys found", ticker, len(found))
            return None

        logger.info("KG fundamentals hit for %s (%d keys)", ticker, len(found))
        return {"ticker": ticker, "data": found, "source": "kg"}
    finally:
        db.close()


def get_kg_technical(ticker: str) -> dict[str, Any] | None:
    """Query KG for technical observations (price, RSI, SMA, pct_change).

    Uses a shorter TTL (4h) since price data ages quickly.
    """
    db = SessionLocal()
    try:
        node = _get_ticker_node(db, ticker)
        if node is None or not _is_fresh(node, _TECHNICAL_TTL):
            return None

        meta = _parse_metadata(node)

        wanted = {"price", "rsi_14", "sma_20", "sma_50", "sma_200",
                  "atr_14", "pct_change", "volume", "52w_high", "52w_low"}
        found = {k: v for k, v in meta.items() if k in wanted and v is not None}

        if len(found) < 2:
            logger.debug("KG technical miss for %s: only %d keys found", ticker, len(found))
            return None

        logger.info("KG technical hit for %s (%d keys)", ticker, len(found))
        return {"ticker": ticker, "data": found, "source": "kg"}
    finally:
        db.close()


def get_kg_sentiment(ticker: str) -> dict[str, Any] | None:
    """Query KG for sentiment-related data (institutional holders, peers/edges).

    Returns structured dict with holder info and peer relationships.
    """
    db = SessionLocal()
    try:
        node = _get_ticker_node(db, ticker)
        if node is None or not _is_fresh(node, _SENTIMENT_TTL):
            return None

        meta = _parse_metadata(node)

        # Check for institutional holder data
        holders = meta.get("institutional_holders")
        peers_data: list[str] = []

        # Gather peer edges (CO_MENTION or shared sector)
        if node.id:
            edges = (
                db.query(KGEdge)
                .filter(
                    or_(
                        KGEdge.source_id == node.id,
                        KGEdge.target_id == node.id,
                    )
                )
                .limit(20)
                .all()
            )

            peer_ids = set()
            for e in edges:
                peer_id = e.target_id if e.source_id == node.id else e.source_id
                peer_ids.add(peer_id)

            if peer_ids:
                peer_nodes = (
                    db.query(KGNode)
                    .filter(
                        KGNode.id.in_(peer_ids),
                        KGNode.node_type == "ticker",
                        KGNode.is_deleted == False,  # noqa: E712
                    )
                    .limit(10)
                    .all()
                )
                peers_data = [pn.name for pn in peer_nodes]

        if not holders and not peers_data:
            logger.debug("KG sentiment miss for %s: no holders or peers", ticker)
            return None

        result: dict[str, Any] = {"ticker": ticker, "source": "kg"}
        if holders:
            result["institutional_holders"] = holders
        if peers_data:
            result["peers"] = peers_data

        logger.info("KG sentiment hit for %s", ticker)
        return result
    finally:
        db.close()
