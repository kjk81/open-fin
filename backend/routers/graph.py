"""FastAPI router for Knowledge Graph queries.

Endpoints:
  GET /api/graph/summary        – community clusters for initial zoomed-out view
  GET /api/graph/ego            – bounded ego subgraph for a given ticker
  GET /api/graph/nodes          – paginated node list (for Table View)
  GET /api/graph/edges          – paginated edge list (for Table View)
"""

from __future__ import annotations

import logging
import re
from typing import Any
from typing import Literal

import networkx as nx
from fastapi import APIRouter, HTTPException, Query

from agent.knowledge_graph import load_graph

logger = logging.getLogger(__name__)
router = APIRouter()
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_subgraph(G: nx.MultiDiGraph) -> dict[str, Any]:
    return {
        "nodes": [{"id": n, **d} for n, d in G.nodes(data=True)],
        "edges": [
            {"source": u, "target": v, **d}
            for u, v, d in G.edges(data=True)
        ],
    }


def _degree_map(G: nx.MultiDiGraph) -> dict[str, int]:
    return dict(G.degree())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/graph/summary")
def graph_summary() -> dict[str, Any]:
    """Return high-level community clusters using NetworkX Louvain."""
    G = load_graph()
    if G.number_of_nodes() == 0:
        return {
            "node_count": 0,
            "edge_count": 0,
            "communities": [],
        }

    undirected = G.to_undirected()
    # nx.community.louvain_communities requires networkx >= 3.0
    try:
        raw_communities: list[set[str]] = list(
            nx.community.louvain_communities(undirected, seed=42)
        )
    except Exception as exc:
        logger.warning("Louvain failed, falling back to empty communities: %s", exc)
        raw_communities = []

    degrees = _degree_map(G)

    communities_out = []
    for i, members in enumerate(
        sorted(raw_communities, key=lambda s: -len(s))
    ):
        member_list = sorted(members)
        representative = max(member_list, key=lambda n: degrees.get(n, 0))
        communities_out.append(
            {
                "id": i,
                "size": len(members),
                "representative": representative,
                "members": member_list[:20],  # preview cap
            }
        )

    return {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "communities": communities_out,
    }


@router.get("/graph/ego")
def graph_ego(
    ticker: str,
    depth: int = Query(default=2, ge=1, le=3),
) -> dict[str, Any]:
    """Return a bounded ego subgraph for progressive loading."""
    ticker = ticker.upper().strip()
    if not TICKER_RE.match(ticker):
        raise HTTPException(status_code=422, detail="Invalid ticker format.")

    G = load_graph()

    if ticker not in G:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{ticker}' not found in knowledge graph. "
                   "Analyze it in the Co-Pilot tab first.",
        )

    ego = nx.ego_graph(G, ticker, radius=depth)
    data = _serialize_subgraph(ego)

    # Attach degree (within full graph, not ego subgraph) for node sizing
    degrees = _degree_map(G)
    for node in data["nodes"]:
        node["degree"] = degrees.get(node["id"], 0)

    return data


@router.get("/graph/nodes")
def graph_nodes(
    kind: Literal["ticker", "sector", "industry"] | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Paginated node list for the Table View."""
    G = load_graph()
    degrees = _degree_map(G)

    rows = []
    for n, attrs in G.nodes(data=True):
        if kind and attrs.get("kind") != kind:
            continue
        if search and search.upper() not in n.upper():
            continue
        rows.append({"id": n, **attrs, "degree": degrees.get(n, 0)})

    rows.sort(key=lambda x: -x["degree"])

    return {
        "total": len(rows),
        "items": rows[offset : offset + limit],
    }


@router.get("/graph/edges")
def graph_edges(
    kind: Literal["IN_SECTOR", "IN_INDUSTRY", "CO_MENTION"] | None = Query(default=None),
    source: str | None = Query(default=None, min_length=1, max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Paginated edge list for the Table View."""
    G = load_graph()

    rows = []
    for u, v, data in G.edges(data=True):
        if kind and data.get("kind") != kind:
            continue
        if source and u.upper() != source.upper():
            continue
        rows.append({"source": u, "target": v, **data})

    return {
        "total": len(rows),
        "items": rows[offset : offset + limit],
    }
