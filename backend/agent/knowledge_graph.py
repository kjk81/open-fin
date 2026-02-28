"""Knowledge graph persistence layer — SQLite + FAISS backend.

Replaces the former NetworkX/JSON implementation.

Public API:

    upsert_ticker_snapshot(symbol, info, report_text)   — sync (legacy compat)
    upsert_from_tool_results(tool_results)              — async post-processing hook

Internal wiring (called once from ``main.py`` lifespan):

    set_faiss_manager(mgr)   — share the singleton FaissManager
    set_write_queue(q)       — share the asyncio writer queue

Write flow
----------
1. SQLite writes happen synchronously (legacy) or via AsyncSession (new path).
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
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, SessionLocal
from models import KGEdge, KGNode
from schemas.kg_entities import Company, FilingMetadata, MetricObservation, WebDocument

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
# Async SQLAlchemy helpers (new path — used by upsert_from_tool_results)
# ---------------------------------------------------------------------------

async def _aupsert_node(
    session: AsyncSession,
    node_type: str,
    name: str,
    metadata: dict | None = None,
) -> tuple[int, bool]:
    """Async insert-or-update a KGNode row.

    Returns ``(node_id, was_new)`` so callers can decide whether to enqueue
    a fresh FAISS embedding.
    """
    result = await session.execute(
        select(KGNode).where(KGNode.name == name, KGNode.is_deleted == False)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if metadata:
            merged = json.loads(existing.metadata_json or "{}")
            merged.update(metadata)
            existing.metadata_json = json.dumps(merged)
        existing.updated_at = datetime.utcnow()
        return existing.id, False

    node = KGNode(
        node_type=node_type,
        name=name,
        metadata_json=json.dumps(metadata or {}),
        updated_at=datetime.utcnow(),
    )
    session.add(node)
    await session.flush()  # populate node.id without committing
    return node.id, True


async def _aupsert_edge(
    session: AsyncSession,
    source_id: int,
    target_id: int,
    relationship: str,
) -> bool:
    """Async insert a KGEdge if not already present.  Returns ``True`` if new."""
    result = await session.execute(
        select(KGEdge).where(
            KGEdge.source_id == source_id,
            KGEdge.target_id == target_id,
            KGEdge.relationship == relationship,
        )
    )
    if result.scalar_one_or_none() is None:
        session.add(KGEdge(source_id=source_id, target_id=target_id, relationship=relationship))
        return True
    return False


def _embedding_text_for(node_type: str, name: str, metadata: dict | None = None) -> str:
    """Build a plain-text representation suitable for FAISS embedding."""
    if _faiss_mgr is not None:
        return _faiss_mgr.text_for_node(node_type, name, metadata or {})
    # Fallback: concatenate name and metadata values
    parts = [name]
    for v in (metadata or {}).values():
        if isinstance(v, str) and v:
            parts.append(v)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Async tool-specific processors
# ---------------------------------------------------------------------------
# Each processor receives (session, args, data) where:
#   args — the dict of arguments passed to the LangGraph tool wrapper
#   data — the deserialized ``ToolResult.data`` field (dict or list)
#
# Returns (nodes_created, edges_created, new_ids, new_texts).
# ---------------------------------------------------------------------------

async def _proc_company_profile(
    session: AsyncSession,
    args: dict,
    data: dict,
) -> tuple[int, int, list[int], list[str]]:
    symbol = (data.get("symbol") or args.get("ticker") or "").upper().strip()
    if not symbol:
        return 0, 0, [], []

    company = Company(
        ticker=symbol,
        name=data.get("name") or symbol,
        sector=data.get("sector") or None,
        industry=data.get("industry") or None,
        description=data.get("description") or None,
    )
    kw = company.to_kg_node_kwargs()
    meta = json.loads(kw["metadata_json"])
    node_id, is_new = await _aupsert_node(session, kw["node_type"], kw["name"], meta)

    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    if is_new:
        new_ids.append(node_id)
        new_texts.append(company.embedding_text())
        nodes_c += 1

    if company.sector:
        sector_name = f"sector:{company.sector}"
        s_id, s_new = await _aupsert_node(session, "sector", sector_name)
        if await _aupsert_edge(session, node_id, s_id, "IN_SECTOR"):
            edges_c += 1
        if s_new:
            new_ids.append(s_id)
            new_texts.append(_embedding_text_for("sector", sector_name))
            nodes_c += 1

    if company.industry:
        industry_name = f"industry:{company.industry}"
        i_id, i_new = await _aupsert_node(session, "industry", industry_name)
        if await _aupsert_edge(session, node_id, i_id, "IN_INDUSTRY"):
            edges_c += 1
        if i_new:
            new_ids.append(i_id)
            new_texts.append(_embedding_text_for("industry", industry_name))
            nodes_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_peers(
    session: AsyncSession,
    args: dict,
    data: dict,
) -> tuple[int, int, list[int], list[str]]:
    primary = (data.get("symbol") or args.get("ticker") or "").upper().strip()
    peers: list[str] = [p.upper() for p in (data.get("peers") or []) if isinstance(p, str)]
    sector = data.get("sector")
    industry = data.get("industry")

    if not primary:
        return 0, 0, [], []

    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    # Ensure primary company node exists
    primary_company = Company(ticker=primary, name=primary, sector=sector, industry=industry)
    kw = primary_company.to_kg_node_kwargs()
    primary_id, is_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
    if is_new:
        new_ids.append(primary_id)
        new_texts.append(primary_company.embedding_text())
        nodes_c += 1

    for peer_sym in peers:
        peer = Company(ticker=peer_sym, name=peer_sym, sector=sector, industry=industry)
        pkw = peer.to_kg_node_kwargs()
        peer_id, p_new = await _aupsert_node(session, pkw["node_type"], pkw["name"], json.loads(pkw["metadata_json"]))
        if p_new:
            new_ids.append(peer_id)
            new_texts.append(peer.embedding_text())
            nodes_c += 1
        if await _aupsert_edge(session, primary_id, peer_id, "PEER_OF"):
            edges_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_financial_statements(
    session: AsyncSession,
    args: dict,
    data: list | dict,
) -> tuple[int, int, list[int], list[str]]:
    rows = data if isinstance(data, list) else [data]
    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    _KEY_METRICS = ("revenue", "net_income", "eps", "gross_margin", "operating_margin")

    for row in rows:
        symbol = (row.get("symbol") or args.get("ticker") or "").upper().strip()
        period_raw = row.get("period") or str(date.today())
        if not symbol:
            continue
        # Ensure company node exists
        c_id, c_new = await _aupsert_node(session, "company", symbol)
        if c_new:
            new_ids.append(c_id)
            new_texts.append(symbol)
            nodes_c += 1

        for metric_name in _KEY_METRICS:
            val = row.get(metric_name)
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            obs = MetricObservation(
                metric_name=metric_name,
                value=val,
                unit="USD" if metric_name in ("revenue", "net_income") else None,
                observed_at=_parse_date(period_raw),
                source_ticker=symbol,
            )
            kw = obs.to_kg_node_kwargs()
            m_id, m_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
            if m_new:
                new_ids.append(m_id)
                new_texts.append(obs.embedding_text())
                nodes_c += 1
            if await _aupsert_edge(session, m_id, c_id, "OBSERVED_FOR"):
                edges_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_balance_sheet(
    session: AsyncSession,
    args: dict,
    data: list | dict,
) -> tuple[int, int, list[int], list[str]]:
    rows = data if isinstance(data, list) else [data]
    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    _KEY_METRICS = ("total_assets", "total_debt", "cash", "book_value_per_share")

    for row in rows:
        symbol = (row.get("symbol") or args.get("ticker") or "").upper().strip()
        period_raw = row.get("period") or str(date.today())
        if not symbol:
            continue
        c_id, c_new = await _aupsert_node(session, "company", symbol)
        if c_new:
            new_ids.append(c_id)
            new_texts.append(symbol)
            nodes_c += 1

        for metric_name in _KEY_METRICS:
            val = row.get(metric_name)
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            obs = MetricObservation(
                metric_name=metric_name,
                value=val,
                unit="USD",
                observed_at=_parse_date(period_raw),
                source_ticker=symbol,
            )
            kw = obs.to_kg_node_kwargs()
            m_id, m_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
            if m_new:
                new_ids.append(m_id)
                new_texts.append(obs.embedding_text())
                nodes_c += 1
            if await _aupsert_edge(session, m_id, c_id, "OBSERVED_FOR"):
                edges_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_technical_snapshot(
    session: AsyncSession,
    args: dict,
    data: dict,
) -> tuple[int, int, list[int], list[str]]:
    symbol = (data.get("symbol") or args.get("ticker") or "").upper().strip()
    if not symbol:
        return 0, 0, [], []

    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    c_id, c_new = await _aupsert_node(session, "company", symbol)
    if c_new:
        new_ids.append(c_id)
        new_texts.append(symbol)
        nodes_c += 1

    today = date.today()
    _TECH_METRICS = ("price", "rsi_14", "sma_20", "sma_50", "sma_200", "pct_change_1d", "pct_change_5d")
    for metric_name in _TECH_METRICS:
        val = data.get(metric_name)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        obs = MetricObservation(
            metric_name=metric_name,
            value=val,
            unit="%" if "pct" in metric_name else None,
            observed_at=today,
            source_ticker=symbol,
        )
        kw = obs.to_kg_node_kwargs()
        m_id, m_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
        if m_new:
            new_ids.append(m_id)
            new_texts.append(obs.embedding_text())
            nodes_c += 1
        if await _aupsert_edge(session, m_id, c_id, "OBSERVED_FOR"):
            edges_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_filings_metadata(
    session: AsyncSession,
    args: dict,
    data: list | dict,
) -> tuple[int, int, list[int], list[str]]:
    rows = data if isinstance(data, list) else [data]
    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    for row in rows:
        ticker = (row.get("company_ticker") or row.get("ticker") or args.get("ticker") or "").upper().strip()
        filing_type = row.get("filing_type") or row.get("type") or "UNKNOWN"
        filed_date_raw = row.get("filed_date") or row.get("date") or str(date.today())
        url = row.get("url") or row.get("reportUrl") or None

        if not ticker:
            continue

        # Ensure company node
        c_id, c_new = await _aupsert_node(session, "company", ticker)
        if c_new:
            new_ids.append(c_id)
            new_texts.append(ticker)
            nodes_c += 1

        try:
            filing = FilingMetadata(
                filing_type=filing_type,
                filed_date=_parse_date(filed_date_raw),
                company_ticker=ticker,
                period_end=_parse_date(row.get("period_end") or filed_date_raw),
                url=url,
            )
        except Exception:
            continue

        kw = filing.to_kg_node_kwargs()
        f_id, f_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
        if f_new:
            new_ids.append(f_id)
            new_texts.append(filing.embedding_text())
            nodes_c += 1
        if await _aupsert_edge(session, f_id, c_id, "FILED_BY"):
            edges_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_web_documents(
    session: AsyncSession,
    sources: list[dict],
) -> tuple[int, int, list[int], list[str]]:
    """Create WebDocument nodes for each unique source URL."""
    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    for src in sources:
        url = src.get("url") or ""
        title = src.get("title") or url
        if not url:
            continue
        try:
            doc = WebDocument(
                url=url,
                title=title,
                snippet=src.get("snippet"),
                fetched_at=datetime.utcnow(),
            )
        except Exception:
            continue
        kw = doc.to_kg_node_kwargs()
        d_id, d_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
        if d_new:
            new_ids.append(d_id)
            new_texts.append(doc.embedding_text())
            nodes_c += 1

    return nodes_c, edges_c, new_ids, new_texts


async def _proc_screen_stocks(
    session: AsyncSession,
    args: dict,
    data: list | dict,
) -> tuple[int, int, list[int], list[str]]:
    rows = data if isinstance(data, list) else [data]
    new_ids: list[int] = []
    new_texts: list[str] = []
    nodes_c = 0
    edges_c = 0

    for row in rows:
        symbol = (row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        company = Company(
            ticker=symbol,
            name=row.get("name") or symbol,
            sector=row.get("sector") or None,
        )
        kw = company.to_kg_node_kwargs()
        c_id, c_new = await _aupsert_node(session, kw["node_type"], kw["name"], json.loads(kw["metadata_json"]))
        if c_new:
            new_ids.append(c_id)
            new_texts.append(company.embedding_text())
            nodes_c += 1

        if company.sector:
            sector_name = f"sector:{company.sector}"
            s_id, s_new = await _aupsert_node(session, "sector", sector_name)
            if await _aupsert_edge(session, c_id, s_id, "IN_SECTOR"):
                edges_c += 1
            if s_new:
                new_ids.append(s_id)
                new_texts.append(_embedding_text_for("sector", sector_name))
                nodes_c += 1

    return nodes_c, edges_c, new_ids, new_texts


# Tool name → processor mapping
_TOOL_PROCESSORS: dict[str, Any] = {
    "get_company_profile": _proc_company_profile,
    "get_peers": _proc_peers,
    "get_financial_statements": _proc_financial_statements,
    "get_balance_sheet": _proc_balance_sheet,
    "get_technical_snapshot": _proc_technical_snapshot,
    "get_filings_metadata": _proc_filings_metadata,
    "screen_stocks": _proc_screen_stocks,
}

# Tools whose sources become WebDocument nodes
_DOCUMENT_TOOLS: frozenset[str] = frozenset(
    {"extract_filing_sections", "read_filings", "get_filings_metadata"}
)


def _parse_date(raw: Any) -> date:
    """Coerce various date representations to a ``datetime.date``."""
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()[:10]  # take YYYY-MM-DD prefix
    try:
        return date.fromisoformat(s)
    except ValueError:
        return date.today()


# ---------------------------------------------------------------------------
# Public API — async post-processing hook
# ---------------------------------------------------------------------------

async def upsert_from_tool_results(
    tool_results: list[dict],
    extra_sources: list[dict] | None = None,
) -> dict[str, Any]:
    """Persist entities extracted from LangGraph tool results into the KG.

    Parameters
    ----------
    tool_results:
        List of ``{"tool": str, "args": dict, "result": str}`` dicts from
        ``AgentState.tool_results``.  The ``result`` field is the
        ``ToolResult.model_dump_json()`` string.
    extra_sources:
        Additional ``SourceRef``-style dicts (``{"url", "title"}``) to persist
        as ``WebDocument`` nodes, typically collected during streaming.

    Returns
    -------
    dict
        ``{"nodes_created": int, "edges_created": int, "node_ids": list[int]}``
    """
    total_nodes = 0
    total_edges = 0
    all_new_ids: list[int] = []
    all_new_texts: list[str] = []
    doc_sources: list[dict] = list(extra_sources or [])

    async with AsyncSessionLocal() as session:
        for tr in tool_results:
            tool_name = tr.get("tool", "")
            args: dict = tr.get("args") or {}
            result_str: str = tr.get("result") or ""

            if not result_str:
                continue

            try:
                result = json.loads(result_str)
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON tool result for %s.", tool_name)
                continue

            if not result.get("success", True):
                continue

            data = result.get("data")
            if data is None:
                continue

            # Collect sources for document tools
            if tool_name in _DOCUMENT_TOOLS:
                for src in result.get("sources", []):
                    if src.get("url") and not any(s.get("url") == src["url"] for s in doc_sources):
                        doc_sources.append(src)

            processor = _TOOL_PROCESSORS.get(tool_name)
            if processor is None:
                continue

            try:
                nc, ec, ids, texts = await processor(session, args, data)
                total_nodes += nc
                total_edges += ec
                all_new_ids.extend(ids)
                all_new_texts.extend(texts)
            except Exception:
                logger.exception("KG processor failed for tool '%s'.", tool_name)

        # Persist WebDocument nodes for document tool sources
        if doc_sources:
            try:
                nc, ec, ids, texts = await _proc_web_documents(session, doc_sources)
                total_nodes += nc
                total_edges += ec
                all_new_ids.extend(ids)
                all_new_texts.extend(texts)
            except Exception:
                logger.exception("KG web-document upsert failed.")

        try:
            await session.commit()
            logger.info(
                "KG post-processing committed: %d nodes, %d edges.",
                total_nodes,
                total_edges,
            )
        except Exception:
            await session.rollback()
            logger.exception("KG post-processing commit failed.")
            return {"nodes_created": 0, "edges_created": 0, "node_ids": []}

    # Enqueue FAISS vector updates (non-blocking)
    if _write_queue is not None and all_new_ids:
        try:
            _write_queue.put_nowait(("upsert", all_new_ids, all_new_texts))
        except asyncio.QueueFull:
            logger.warning(
                "FAISS write queue full — %d new vectors will be stale until next rebuild.",
                len(all_new_ids),
            )

    return {
        "nodes_created": total_nodes,
        "edges_created": total_edges,
        "node_ids": all_new_ids,
    }


# ---------------------------------------------------------------------------
# Public API — sync legacy
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
