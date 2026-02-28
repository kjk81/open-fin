"""FastAPI router for Knowledge Graph queries — SQLite + FAISS backend.

Endpoints (same URLs and response shapes as the former NetworkX version):

  GET /api/graph/summary   – community clusters (sector-based, replaces Louvain)
  GET /api/graph/ego       – BFS structural subgraph + FAISS semantic neighbours
  GET /api/graph/nodes     – paginated node list with optional semantic search
  GET /api/graph/edges     – paginated edge list

The ``_faiss_mgr`` module-level reference is injected at startup from
``main.py`` via :func:`set_faiss_manager`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from database import get_db
from models import KGEdge, KGNode

logger = logging.getLogger(__name__)
router = APIRouter()

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")

# Injected by main.py at lifespan startup
_faiss_mgr = None


def set_faiss_manager(mgr: Any) -> None:
    """Register the shared :class:`~agent.vector_store.FaissManager` instance."""
    global _faiss_mgr
    _faiss_mgr = mgr


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _node_name(db: Session, node_id: int) -> str:
    """Return the ``name`` of a KGNode by PK, using SQLAlchemy identity map."""
    node = db.get(KGNode, node_id)
    return node.name if node else f"<unknown:{node_id}>"


def _node_degree(db: Session, node_id: int) -> int:
    """Count edges that touch *node_id* (both source and target)."""
    cnt = db.scalar(
        select(func.count())
        .select_from(KGEdge)
        .where((KGEdge.source_id == node_id) | (KGEdge.target_id == node_id))
    )
    return cnt or 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/graph/summary")
def graph_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return overall node/edge counts and sector-based community clusters.

    Sector communities are derived by grouping tickers on their ``IN_SECTOR``
    edges — a pure-SQL O(edges) operation that replaces the former Louvain
    community detection.
    """
    node_count: int = db.scalar(
        select(func.count())
        .select_from(KGNode)
        .where(KGNode.is_deleted == False)
    ) or 0

    edge_count: int = db.scalar(select(func.count()).select_from(KGEdge)) or 0

    if node_count == 0:
        return {"node_count": 0, "edge_count": 0, "communities": []}

    # Group tickers by their sector node (IN_SECTOR edge)
    sector_sql = text(
        """
        SELECT s.name AS sector_name,
               GROUP_CONCAT(t.name) AS members_csv,
               COUNT(*) AS cnt
        FROM kg_edges e
        JOIN kg_nodes t ON t.id = e.source_id AND t.is_deleted = 0
        JOIN kg_nodes s ON s.id = e.target_id AND s.is_deleted = 0
        WHERE e.relationship = 'IN_SECTOR'
        GROUP BY s.name
        ORDER BY cnt DESC
        """
    )
    sector_rows = db.execute(sector_sql).fetchall()

    communities: list[dict[str, Any]] = []
    for i, (sector_name, members_csv, cnt) in enumerate(sector_rows):
        member_list = members_csv.split(",") if members_csv else []
        communities.append(
            {
                "id": i,
                "size": cnt,
                "representative": sector_name,
                "members": member_list[:20],
            }
        )

    # Ungrouped tickers (no IN_SECTOR edge)
    ungrouped_sql = text(
        """
        SELECT t.name
        FROM kg_nodes t
        WHERE t.node_type = 'ticker'
          AND t.is_deleted = 0
          AND t.id NOT IN (
              SELECT e.source_id FROM kg_edges e WHERE e.relationship = 'IN_SECTOR'
          )
        """
    )
    ungrouped_rows = db.execute(ungrouped_sql).fetchall()
    if ungrouped_rows:
        communities.append(
            {
                "id": len(communities),
                "size": len(ungrouped_rows),
                "representative": "Ungrouped",
                "members": [r[0] for r in ungrouped_rows[:20]],
            }
        )

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "communities": communities,
    }


@router.get("/graph/ego")
def graph_ego(
    ticker: str,
    depth: int = Query(default=2, ge=1, le=3),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a hybrid ego subgraph for *ticker*.

    Structural neighbours are collected via BFS on the ``kg_edges`` table up
    to *depth* hops.  If the FAISS index is available, the top-10 semantic
    neighbours are merged in as well (useful for discovering related tickers
    that are not yet connected by explicit edges).

    Node degree is computed against the full graph (not the subgraph) so the
    frontend can size nodes consistently.
    """
    ticker = ticker.upper().strip()
    if not TICKER_RE.match(ticker):
        raise HTTPException(status_code=422, detail="Invalid ticker format.")

    center: KGNode | None = db.execute(
        select(KGNode).where(KGNode.name == ticker, KGNode.is_deleted == False)
    ).scalar_one_or_none()

    if center is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Ticker '{ticker}' not found in knowledge graph. "
                "Analyze it in the Co-Pilot tab first."
            ),
        )

    # --- BFS structural traversal ---
    visited_ids: set[int] = {center.id}
    frontier: set[int] = {center.id}
    collected_edges: list[KGEdge] = []

    for _ in range(depth):
        if not frontier:
            break
        edges: list[KGEdge] = (
            db.execute(
                select(KGEdge).where(
                    (KGEdge.source_id.in_(frontier))
                    | (KGEdge.target_id.in_(frontier))
                )
            )
            .scalars()
            .all()
        )
        next_frontier: set[int] = set()
        for e in edges:
            collected_edges.append(e)
            for nid in (e.source_id, e.target_id):
                if nid not in visited_ids:
                    visited_ids.add(nid)
                    next_frontier.add(nid)
        frontier = next_frontier

    # --- FAISS semantic neighbours ---
    if _faiss_mgr is not None:
        import json as _json
        query_text = _faiss_mgr.text_for_node(
            center.node_type,
            center.name,
            _json.loads(center.metadata_json or "{}"),
        )
        faiss_hits = _faiss_mgr.search(query_text, k=10)
        for node_id, _dist in faiss_hits:
            visited_ids.add(node_id)

    # --- Fetch subgraph nodes ---
    nodes: list[KGNode] = (
        db.execute(
            select(KGNode).where(
                KGNode.id.in_(visited_ids),
                KGNode.is_deleted == False,
            )
        )
        .scalars()
        .all()
    )

    # Deduplicate edges (BFS may yield the same edge multiple times)
    seen_edge_ids: set[int] = set()
    unique_edges: list[KGEdge] = []
    for e in collected_edges:
        if e.id not in seen_edge_ids:
            seen_edge_ids.add(e.id)
            unique_edges.append(e)

    return {
        "nodes": [
            {
                "id": n.name,
                "kind": n.node_type,
                "degree": _node_degree(db, n.id),
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": _node_name(db, e.source_id),
                "target": _node_name(db, e.target_id),
                "kind": e.relationship,
            }
            for e in unique_edges
        ],
    }


@router.get("/graph/nodes")
def graph_nodes(
    kind: Literal["ticker", "sector", "industry"] | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated node list.

    When *search* is provided and the FAISS index is available, results are
    ordered by semantic similarity.  Otherwise, a case-insensitive SQL
    ``LIKE`` filter is applied.
    """
    # --- Semantic search via FAISS ---
    if search and _faiss_mgr is not None:
        faiss_hits = _faiss_mgr.search(search, k=offset + limit)
        if not faiss_hits:
            return {"total": 0, "items": []}

        node_ids = [nid for nid, _ in faiss_hits]
        q = select(KGNode).where(
            KGNode.id.in_(node_ids), KGNode.is_deleted == False
        )
        if kind:
            q = q.where(KGNode.node_type == kind)
        nodes: list[KGNode] = db.execute(q).scalars().all()

        # Preserve FAISS relevance order
        id_to_node = {n.id: n for n in nodes}
        ordered = [id_to_node[nid] for nid in node_ids if nid in id_to_node]
        page = ordered[offset : offset + limit]

        return {
            "total": len(ordered),
            "items": [
                {
                    "id": n.name,
                    "kind": n.node_type,
                    "updated_at": n.updated_at.isoformat() if n.updated_at else None,
                }
                for n in page
            ],
        }

    # --- SQL fallback ---
    base_q = select(KGNode).where(KGNode.is_deleted == False)
    if kind:
        base_q = base_q.where(KGNode.node_type == kind)
    if search:
        base_q = base_q.where(KGNode.name.ilike(f"%{search}%"))

    total: int = db.scalar(
        select(func.count()).select_from(base_q.subquery())
    ) or 0
    nodes = db.execute(base_q.offset(offset).limit(limit)).scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": n.name,
                "kind": n.node_type,
                "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            }
            for n in nodes
        ],
    }


@router.get("/graph/edges")
def graph_edges(
    kind: Literal["IN_SECTOR", "IN_INDUSTRY", "CO_MENTION"] | None = Query(default=None),
    source: str | None = Query(default=None, min_length=1, max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated edge list with optional ``kind`` and ``source`` filters."""
    q = select(KGEdge)

    if kind:
        q = q.where(KGEdge.relationship == kind)

    if source:
        source_node: KGNode | None = db.execute(
            select(KGNode).where(
                KGNode.name == source.upper(), KGNode.is_deleted == False
            )
        ).scalar_one_or_none()
        if source_node is None:
            return {"total": 0, "items": []}
        q = q.where(KGEdge.source_id == source_node.id)

    total: int = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    edges: list[KGEdge] = db.execute(q.offset(offset).limit(limit)).scalars().all()

    return {
        "total": total,
        "items": [
            {
                "source": _node_name(db, e.source_id),
                "target": _node_name(db, e.target_id),
                "kind": e.relationship,
                "weight": e.weight,
            }
            for e in edges
        ],
    }
