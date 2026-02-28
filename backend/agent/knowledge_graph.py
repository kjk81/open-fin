"""Knowledge graph persistence layer — SQLite + FAISS backend.

Replaces the former NetworkX/JSON implementation.

Public API (unchanged from the NetworkX version so ``agent/nodes.py`` needs
no edits):

    upsert_ticker_snapshot(symbol, info, report_text)

Internal wiring (called once from ``main.py`` lifespan):

    set_faiss_manager(mgr)   — share the singleton FaissManager
    set_write_queue(q)       — share the asyncio writer queue

Write flow
----------
1. ``upsert_ticker_snapshot`` writes nodes/edges to SQLite synchronously.
2. New/updated node IDs + their embedding texts are placed on the asyncio
   write queue (non-blocking ``put_nowait``).
3. The single writer task in ``main.py`` drains the queue and calls
   ``FaissManager.upsert_vectors`` — ensuring serial, lock-protected writes
   to the on-disk FAISS index.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from database import SessionLocal
from models import KGEdge, KGNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Co-mention stopwords (carried over from NetworkX implementation)
# ---------------------------------------------------------------------------

_CO_MENTION_STOPWORDS: frozenset[str] = frozenset(
    {
        # Finance / report boilerplate
        "P", "E", "PE", "EPS", "TTM", "YOY", "QOQ", "FCF",
        "EBIT", "EBITDA", "ROI", "ROE", "ROA", "USD", "ETF",
        # Generic English
        "A", "I", "AN", "THE", "AND", "OR", "TO", "IN", "OF", "ON", "IS",
    }
)

# ---------------------------------------------------------------------------
# Module-level singletons (set during FastAPI lifespan startup)
# ---------------------------------------------------------------------------

_faiss_mgr = None            # FaissManager | None
_write_queue: asyncio.Queue | None = None


def set_faiss_manager(mgr: Any) -> None:
    """Register the shared :class:`~agent.vector_store.FaissManager` instance."""
    global _faiss_mgr
    _faiss_mgr = mgr


def set_write_queue(q: asyncio.Queue) -> None:
    """Register the asyncio queue consumed by the single writer task."""
    global _write_queue
    _write_queue = q


# ---------------------------------------------------------------------------
# Internal SQLAlchemy helpers
# ---------------------------------------------------------------------------

def _upsert_node(
    db,
    node_type: str,
    name: str,
    metadata: dict | None = None,
) -> int:
    """Insert-or-update a KGNode row; return its primary key.

    Uses ``name`` as the unique key.  If a row already exists and
    ``metadata`` is provided, the stored JSON blob is merged (existing
    keys win).
    """
    existing = db.execute(
        select(KGNode).where(KGNode.name == name, KGNode.is_deleted == False)
    ).scalar_one_or_none()

    if existing is not None:
        if metadata:
            merged = json.loads(existing.metadata_json or "{}")
            merged.update(metadata)
            existing.metadata_json = json.dumps(merged)
        existing.updated_at = datetime.utcnow()
        db.flush()
        return existing.id

    node = KGNode(
        node_type=node_type,
        name=name,
        metadata_json=json.dumps(metadata or {}),
        updated_at=datetime.utcnow(),
    )
    db.add(node)
    db.flush()  # populate node.id without committing the transaction
    return node.id


def _upsert_edge(
    db,
    source_id: int,
    target_id: int,
    relationship: str,
) -> None:
    """Insert a KGEdge if an identical one does not already exist."""
    exists = db.execute(
        select(KGEdge).where(
            KGEdge.source_id == source_id,
            KGEdge.target_id == target_id,
            KGEdge.relationship == relationship,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(KGEdge(source_id=source_id, target_id=target_id, relationship=relationship))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upsert_ticker_snapshot(
    symbol: str,
    info: dict[str, Any] | None,
    report_text: str | None,
) -> None:
    """Persist a ticker snapshot to SQLite and enqueue FAISS vector updates.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"AAPL"``).  Normalised to uppercase.
    info:
        yfinance ``Ticker.info`` dict.  Used to extract sector, industry,
        and company name.  May be ``None`` if the fetch failed.
    report_text:
        LLM-generated analysis text.  Used to extract co-mention edges via
        simple regex.  May be ``None``.
    """
    symbol = symbol.upper().strip()
    if not symbol:
        return

    db = SessionLocal()
    new_node_ids: list[int] = []
    new_node_texts: list[str] = []

    try:
        # --- Ticker node ---
        meta: dict[str, Any] = {}
        if info:
            meta = {
                "company_name": info.get("shortName") or info.get("longName") or "",
                "sector": info.get("sector") or "",
                "industry": info.get("industry") or "",
            }

        ticker_id = _upsert_node(db, "ticker", symbol, meta)
        new_node_ids.append(ticker_id)
        if _faiss_mgr is not None:
            new_node_texts.append(_faiss_mgr.text_for_node("ticker", symbol, meta))
        else:
            new_node_texts.append(symbol)

        # --- Sector / industry nodes and edges ---
        if info:
            sector = (info.get("sector") or "").strip()
            industry = (info.get("industry") or "").strip()

            if sector:
                sector_name = f"sector:{sector}"
                sector_id = _upsert_node(db, "sector", sector_name)
                _upsert_edge(db, ticker_id, sector_id, "IN_SECTOR")
                new_node_ids.append(sector_id)
                if _faiss_mgr is not None:
                    new_node_texts.append(_faiss_mgr.text_for_node("sector", sector_name))
                else:
                    new_node_texts.append(sector_name)

            if industry:
                industry_name = f"industry:{industry}"
                industry_id = _upsert_node(db, "industry", industry_name)
                _upsert_edge(db, ticker_id, industry_id, "IN_INDUSTRY")
                new_node_ids.append(industry_id)
                if _faiss_mgr is not None:
                    new_node_texts.append(_faiss_mgr.text_for_node("industry", industry_name))
                else:
                    new_node_texts.append(industry_name)

        # --- Co-mention edges ---
        if report_text:
            candidates = set(re.findall(r"\b[A-Z]{1,10}\b", report_text))
            candidates.discard(symbol)
            for other in sorted(candidates):
                if other in _CO_MENTION_STOPWORDS:
                    continue
                if 1 <= len(other) <= 10:
                    other_id = _upsert_node(db, "ticker", other)
                    _upsert_edge(db, ticker_id, other_id, "CO_MENTION")
                    new_node_ids.append(other_id)
                    if _faiss_mgr is not None:
                        new_node_texts.append(_faiss_mgr.text_for_node("ticker", other))
                    else:
                        new_node_texts.append(other)

        db.commit()
        logger.debug("KG upsert committed for %s (%d nodes).", symbol, len(new_node_ids))

    except Exception:
        db.rollback()
        logger.exception("KG upsert failed for %s.", symbol)
        return
    finally:
        db.close()

    # --- Enqueue FAISS vector update (non-blocking) ---
    if _write_queue is not None and new_node_ids:
        try:
            _write_queue.put_nowait(("upsert", new_node_ids, new_node_texts))
        except asyncio.QueueFull:
            logger.warning(
                "FAISS write queue is full — vectors for %s will be stale until next rebuild.",
                symbol,
            )
