#!/usr/bin/env python3
"""Manual verification script for the KG→FAISS async pipeline fix.

This script demonstrates that:
1. SQLite `kg_nodes` table is being updated (rows exist and timestamps change).
2. FAISS index file is mutated (mtime/size or ntotal changes after upsert).

Usage (from backend/ directory):
    python scripts/verify_kg_faiss_pipeline.py

Prerequisites:
- API server must be running (uvicorn main:app or python entry_api.py).
- FAISS must have initialized successfully (check /api/health).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import faiss  # type: ignore


def main() -> None:
    # Step 1: Check SQLite state
    db_path = os.getenv("OPEN_FIN_DB_PATH", "open_fin.db")
    if not Path(db_path).exists():
        print(f"❌ Database not found at {db_path}. Run the API server first.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM kg_nodes WHERE is_deleted = 0")
    node_count = cursor.fetchone()[0]
    cursor.execute("SELECT MAX(updated_at) FROM kg_nodes")
    latest_ts = cursor.fetchone()[0]
    conn.close()

    print("=== SQLite KG State ===")
    print(f"Active nodes: {node_count}")
    print(f"Latest updated_at: {latest_ts}")

    # Step 2: Check FAISS index state
    faiss_dir = Path(os.getenv("OPEN_FIN_FAISS_DIR", "faiss_data"))
    index_path = faiss_dir / "openfin.index"

    if not index_path.exists():
        print(f"\n❌ FAISS index not found at {index_path}.")
        return

    index_stat = index_path.stat()
    print("\n=== FAISS Index File ===")
    print(f"Path: {index_path}")
    print(f"Size: {index_stat.st_size} bytes")
    print(f"Modified: {time.ctime(index_stat.st_mtime)}")

    # Optional: Read FAISS ntotal
    try:
        idx = faiss.read_index(str(index_path))
        print(f"Vectors (ntotal): {idx.ntotal}")
    except Exception as exc:
        print(f"⚠️  Could not read FAISS index: {exc}")

    print("\n✅ Manual verification complete.")
    print(
        "To test live updates:\n"
        "  1. Trigger a chat or tool call that creates KG nodes (e.g., ask about a stock).\n"
        "  2. Re-run this script and confirm updated_at changed and FAISS mtime/size increased.\n"
        "  3. Check logs for 'FAISS upsert completed' or degraded-mode warnings if FAISS fails."
    )


if __name__ == "__main__":
    main()
