from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


_CO_MENTION_STOPWORDS: frozenset[str] = frozenset(
    {
        # Finance / report boilerplate
        "P",
        "E",
        "PE",
        "EPS",
        "TTM",
        "YOY",
        "QOQ",
        "FCF",
        "EBIT",
        "EBITDA",
        "ROI",
        "ROE",
        "ROA",
        "USD",
        "ETF",
        # Generic English
        "A",
        "I",
        "AN",
        "THE",
        "AND",
        "OR",
        "TO",
        "IN",
        "OF",
        "ON",
        "IS",
    }
)


def _kg_path() -> Path:
    override = os.getenv("OPEN_FIN_KG_PATH")
    if override:
        path = Path(override).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return Path(__file__).resolve().parent.parent / "open_fin_kg.json"


def load_graph() -> nx.MultiDiGraph:
    path = _kg_path()
    if not path.exists():
        return nx.MultiDiGraph()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return nx.node_link_graph(raw, directed=True, multigraph=True)  # type: ignore[no-any-return]
    except Exception as exc:
        logger.warning("Failed to load KG from %s: %s", path, exc)
        return nx.MultiDiGraph()


def save_graph(graph: nx.MultiDiGraph) -> None:
    path = _kg_path()
    try:
        payload = nx.node_link_data(graph)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save KG to %s: %s", path, exc)


def upsert_ticker_snapshot(symbol: str, info: dict[str, Any] | None, report_text: str | None) -> None:
    """Upsert simple relationships for a ticker.

    This is intentionally lightweight: it records sector/industry nodes and
    creates edges between tickers that appear in the generated report.
    """

    symbol = symbol.upper().strip()
    if not symbol:
        return

    graph = load_graph()

    graph.add_node(symbol, kind="ticker", updated_at=datetime.utcnow().isoformat())

    if info:
        sector = (info.get("sector") or "").strip()
        industry = (info.get("industry") or "").strip()

        if sector:
            sector_node = f"sector:{sector}"
            graph.add_node(sector_node, kind="sector")
            graph.add_edge(symbol, sector_node, kind="IN_SECTOR")

        if industry:
            industry_node = f"industry:{industry}"
            graph.add_node(industry_node, kind="industry")
            graph.add_edge(symbol, industry_node, kind="IN_INDUSTRY")

    if report_text:
        # Naive co-mention extraction for relationships (kept local-only).
        # Filter out obvious abbreviations/noise so the KG remains useful.
        import re

        candidates = set(re.findall(r"\b[A-Z]{1,10}\b", report_text))
        candidates.discard(symbol)
        for other in sorted(candidates):
            if other in _CO_MENTION_STOPWORDS:
                continue
            if 1 <= len(other) <= 10:
                graph.add_node(other, kind="ticker")
                graph.add_edge(symbol, other, kind="CO_MENTION")

    save_graph(graph)
