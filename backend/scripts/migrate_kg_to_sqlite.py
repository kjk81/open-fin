"""One-time migration: ``open_fin_kg.json`` → SQLite ``kg_nodes``/``kg_edges`` + FAISS index.

Run from the ``backend/`` directory:

    python scripts/migrate_kg_to_sqlite.py

The script is idempotent: existing nodes/edges with the same name/source/target
are skipped rather than duplicated.  Safe to re-run if interrupted.

Exit codes:
  0 — success (or nothing to migrate)
  1 — unrecoverable error
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow ``import database``, ``import models`` etc. from the backend root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("migrate_kg")


def main() -> int:
    try:
        import networkx as nx  # noqa: PLC0415
    except ImportError:
        logger.error(
            "networkx is not installed. Install it with: pip install networkx"
        )
        return 1

    from database import Base, SessionLocal, engine
    from models import KGEdge, KGNode
    from sqlalchemy import select
    from agent.vector_store import FaissManager

    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    # Locate the JSON dump
    kg_path = Path(__file__).resolve().parent.parent / "open_fin_kg.json"
    override = __import__("os").getenv("OPEN_FIN_KG_PATH")
    if override:
        kg_path = Path(override).expanduser().resolve()

    if not kg_path.exists():
        logger.info("No open_fin_kg.json found at %s — nothing to migrate.", kg_path)
        # Still build an empty FAISS index if one doesn't exist yet
        _ensure_faiss_index()
        return 0

    logger.info("Loading graph from %s ...", kg_path)
    try:
        raw = json.loads(kg_path.read_text(encoding="utf-8"))
        G: nx.MultiDiGraph = nx.node_link_graph(raw, directed=True, multigraph=True)
    except Exception as exc:
        logger.error("Failed to parse %s: %s", kg_path, exc)
        return 1

    logger.info(
        "Graph loaded: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
    )

    db = SessionLocal()
    name_to_id: dict[str, int] = {}

    try:
        # ---------------------------------------------------------------- #
        # Migrate nodes                                                     #
        # ---------------------------------------------------------------- #
        inserted_nodes = 0
        skipped_nodes = 0
        for n, attrs in G.nodes(data=True):
            node_type = attrs.get("kind", "ticker")
            existing = db.execute(
                select(KGNode).where(KGNode.name == n)
            ).scalar_one_or_none()
            if existing is not None:
                name_to_id[n] = existing.id
                skipped_nodes += 1
                continue

            meta = {
                k: v for k, v in attrs.items() if k not in ("kind", "updated_at")
            }
            node = KGNode(
                node_type=node_type,
                name=n,
                metadata_json=json.dumps(meta),
            )
            db.add(node)
            db.flush()
            name_to_id[n] = node.id
            inserted_nodes += 1

        db.commit()
        logger.info(
            "Nodes: %d inserted, %d already existed.", inserted_nodes, skipped_nodes
        )

        # ---------------------------------------------------------------- #
        # Migrate edges                                                     #
        # ---------------------------------------------------------------- #
        inserted_edges = 0
        skipped_edges = 0
        for u, v, attrs in G.edges(data=True):
            source_id = name_to_id.get(u)
            target_id = name_to_id.get(v)
            if source_id is None or target_id is None:
                logger.warning("Skipping edge %s→%s: node not found in DB.", u, v)
                continue

            relationship = attrs.get("kind", "CO_MENTION")
            existing = db.execute(
                select(KGEdge).where(
                    KGEdge.source_id == source_id,
                    KGEdge.target_id == target_id,
                    KGEdge.relationship == relationship,
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped_edges += 1
                continue

            db.add(
                KGEdge(
                    source_id=source_id,
                    target_id=target_id,
                    relationship=relationship,
                )
            )
            inserted_edges += 1

        db.commit()
        logger.info(
            "Edges: %d inserted, %d already existed.", inserted_edges, skipped_edges
        )

    except Exception:
        db.rollback()
        logger.exception("Migration failed — rolled back.")
        db.close()
        return 1

    # -------------------------------------------------------------------- #
    # Build / rebuild FAISS index from migrated rows                       #
    # -------------------------------------------------------------------- #
    logger.info("Building FAISS index from migrated nodes...")
    try:
        mgr = FaissManager()
        mgr._rebuild_from_db(db)
        logger.info("FAISS index built successfully.")
    except Exception:
        logger.exception("Failed to build FAISS index (non-fatal — will rebuild on next startup).")
    finally:
        db.close()

    logger.info("Migration complete.")
    return 0


def _ensure_faiss_index() -> None:
    """Build an empty FAISS index if none exists."""
    from agent.vector_store import FaissManager, _index_path
    from database import SessionLocal

    if _index_path().exists():
        return
    logger.info("Building empty FAISS index...")
    db = SessionLocal()
    try:
        FaissManager()._rebuild_from_db(db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
