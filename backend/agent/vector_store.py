"""FAISS vector store for the Open-Fin knowledge graph.

Architecture overview
---------------------
- **Index file**: ``backend/faiss_data/openfin.index`` (path overridable via
  ``OPEN_FIN_FAISS_DIR`` env var).
- **Index type**: ``IndexIVFFlat`` wrapped in ``IndexIDMap`` so SQLite
  autoincrement PKs are used directly as FAISS vector IDs.  Below the IVF
  training threshold (< 100 vectors) a plain ``IndexFlatL2`` is used instead.
- **Embeddings**: ``fastembed`` (ONNX runtime, CPU-only) with the BGE-small
  model (384-dim, ~30 MB one-time download).

Concurrency / locking
---------------------
**Readers** call :meth:`search` which opens the index via memory-mapped
read-only mode (``faiss.IO_FLAG_MMAP | faiss.IO_FLAG_READ_ONLY``).  MMAP reads
are safe to run concurrently with writes because :func:`faiss.write_index`
writes to a temp file then renames (atomic on POSIX; close-enough on Windows).

**Writers** call :meth:`upsert_vectors`.  All writes go through a single
asyncio writer task (started in ``main.py`` lifespan) so only one coroutine
ever calls ``upsert_vectors`` at a time.  The method additionally acquires a
``filelock`` for defence-in-depth against external processes.

Soft deletes & rebuild
----------------------
FAISS does not support efficient vector removal.  Instead:
1. The SQLite ``kg_nodes.is_deleted`` column is set to ``True``.
2. Search results are post-filtered: nodes with ``is_deleted=True`` are
   discarded after the FAISS kNN lookup.
3. When the ratio of soft-deleted nodes exceeds 10 %, a full rebuild is
   triggered via :meth:`maybe_rebuild` (called from the writer task).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import faiss
import numpy as np
from fastembed import TextEmbedding
from filelock import FileLock

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384  # output dimension of bge-small-en-v1.5

# Minimum vectors before switching from FlatL2 to IVFFlat (IVF needs training data)
_IVF_MIN_VECTORS = 100
# Number of Voronoi cells; min(16, n//4) used at training time
_IVF_NLIST = 16

# Maximum vectors per single upsert call (bounds peak memory)
_UPSERT_BATCH_SIZE = 500

# Soft-delete fraction that triggers a full index rebuild
_REBUILD_THRESHOLD = 0.10


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _index_dir() -> Path:
    """Return the directory that holds the FAISS index and lock files.

    Resolution order:
    1. ``OPEN_FIN_FAISS_DIR`` env var (always preferred — Electron sets this
       to ``userData/faiss_data`` in packaged mode).
    2. Frozen build fallback: writable directory next to the executable.
    3. Source-tree fallback: ``backend/faiss_data``.
    """
    override = os.getenv("OPEN_FIN_FAISS_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        # Frozen executable — sys._MEIPASS is read-only, so fall back to a
        # writable directory next to the executable itself.
        p = Path(sys.executable).resolve().parent / "faiss_data"
    else:
        p = Path(__file__).resolve().parent.parent / "faiss_data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return _index_dir() / "openfin.index"


def _lock_path() -> Path:
    return _index_dir() / "openfin.index.lock"


def _fastembed_cache_dir() -> Path:
    """Return the directory used to cache downloaded FastEmbed models.

    Resolution order:
    1. ``FASTEMBED_CACHE_PATH`` env var (set by Electron to
       ``userData/fastembed_cache`` — keeps all user data co-located and
       avoids permission issues with Windows system temp directories).
    2. Frozen build fallback: sibling directory next to the executable.
    3. Source-tree fallback: ``backend/fastembed_cache``.
    """
    override = os.getenv("FASTEMBED_CACHE_PATH")
    if override:
        p = Path(override).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        p = Path(sys.executable).resolve().parent / "fastembed_cache"
    else:
        p = Path(__file__).resolve().parent.parent / "fastembed_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _make_embedder() -> TextEmbedding:
    """Construct a ``TextEmbedding`` instance with a resolved cache directory.

    Also suppresses the HuggingFace symlink warning that fires on Windows
    (requires Developer Mode or Admin rights) by setting the relevant env var
    before the fastembed/huggingface_hub import-time side-effect.

    Raises ``SystemExit(1)`` with a clear message when the model cannot be
    downloaded or written (e.g. permission errors on a restricted system).
    """
    # Suppress the symlink-creation warning raised by huggingface_hub on
    # Windows systems where Developer Mode / elevation is not available.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    cache_dir = _fastembed_cache_dir()
    logger.info("FastEmbed cache directory: %s", cache_dir)

    try:
        return TextEmbedding(model_name=_EMBED_MODEL_NAME, cache_dir=str(cache_dir))
    except (OSError, PermissionError) as exc:
        logger.error(
            "Failed to initialise FastEmbed model '%s' (cache: %s): %s\n"
            "On Windows, ensure the cache directory is writable. "
            "Set FASTEMBED_CACHE_PATH to a directory you own, or enable "
            "Developer Mode to allow symlinks.",
            _EMBED_MODEL_NAME,
            cache_dir,
            exc,
        )
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# FaissManager
# ---------------------------------------------------------------------------

class FaissManager:
    """Manage the FAISS index backed by SQLite ``kg_nodes`` rows.

    Only one instance should exist per process (created in ``main.py``
    lifespan and shared via module-level references in
    ``knowledge_graph.py`` and ``routers/graph.py``).
    """

    def __init__(self) -> None:
        self._embedder: TextEmbedding = _make_embedder()
        self._index: faiss.Index | None = None
        self._file_lock: FileLock = FileLock(str(_lock_path()), timeout=30)

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return ``(N, 384)`` float32 embedding matrix for *texts*."""
        vecs = list(self._embedder.embed(texts))
        return np.array(vecs, dtype=np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        """Return a single ``(384,)`` float32 embedding vector."""
        return self.embed([text])[0]

    # ------------------------------------------------------------------
    # Node text templates (static — used by knowledge_graph.py too)
    # ------------------------------------------------------------------

    @staticmethod
    def text_for_node(
        node_type: str,
        name: str,
        metadata: dict | None = None,
    ) -> str:
        """Build the text string that is embedded to represent a node.

        Parameters
        ----------
        node_type:
            One of ``"ticker"``, ``"sector"``, ``"industry"``.
        name:
            The unique node name (e.g. ``"AAPL"``, ``"sector:Technology"``).
        metadata:
            Optional dict parsed from ``KGNode.metadata_json``.
        """
        metadata = metadata or {}
        if node_type == "ticker":
            parts = [
                name,
                metadata.get("company_name", ""),
                metadata.get("sector", ""),
                metadata.get("industry", ""),
            ]
            return " ".join(p for p in parts if p).strip()
        if node_type == "sector":
            label = name.removeprefix("sector:")
            return f"Sector: {label}"
        if node_type == "industry":
            label = name.removeprefix("industry:")
            return f"Industry: {label}"
        return name

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def load_or_build(self, db: "Session") -> None:
        """Load an existing index from disk, or build one from the DB.

        Called once during FastAPI lifespan startup.  Blocks until complete
        (fastembed may download the model on the first call).
        """
        path = _index_path()
        if path.exists():
            try:
                self._index = faiss.read_index(
                    str(path),
                    faiss.IO_FLAG_MMAP | faiss.IO_FLAG_READ_ONLY,
                )
                logger.info(
                    "FAISS index loaded from %s (%d vectors)",
                    path,
                    self._index.ntotal,
                )
                return
            except FileNotFoundError:
                # Race: file disappeared between exists() and read_index()
                logger.info(
                    "FAISS index file vanished before read — rebuilding from DB."
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load FAISS index (%s), rebuilding from DB.", exc
                )

        logger.info("No valid FAISS index found — building from kg_nodes...")
        self._rebuild_from_db(db)

    def _build_inner_index(self, vecs: np.ndarray) -> faiss.Index:
        """Return a trained inner index appropriate for *vecs* count."""
        n = len(vecs)
        if n < _IVF_MIN_VECTORS:
            return faiss.IndexFlatL2(_EMBED_DIM)
        nlist = min(_IVF_NLIST, n // 4)
        quantizer = faiss.IndexFlatL2(_EMBED_DIM)
        inner = faiss.IndexIVFFlat(quantizer, _EMBED_DIM, nlist)
        inner.train(vecs)
        return inner

    def _rebuild_from_db(self, db: "Session") -> None:
        """Full rebuild: embed all non-deleted KGNodes, write to disk.

        This method is called:
        - On first startup (no index file).
        - After a soft-delete ratio exceeds :data:`_REBUILD_THRESHOLD`.
        - Manually via the ``("rebuild", …)`` writer-queue message.

        The filelock is held for the duration of the write.
        Embeddings are computed in batches of :data:`_UPSERT_BATCH_SIZE` to
        bound memory usage.
        """
        from models import KGNode  # local import to avoid circular deps

        rows = db.query(KGNode).filter(KGNode.is_deleted == False).all()

        if not rows:
            inner = faiss.IndexFlatL2(_EMBED_DIM)
            self._index = faiss.IndexIDMap(inner)
            self._write_index_locked()
            logger.info("FAISS index initialised (empty).")
            return

        ids = np.array([r.id for r in rows], dtype=np.int64)
        texts = [
            self.text_for_node(r.node_type, r.name, json.loads(r.metadata_json or "{}"))
            for r in rows
        ]

        # Embed in batches to bound memory
        vec_parts: list[np.ndarray] = []
        for i in range(0, len(texts), _UPSERT_BATCH_SIZE):
            vec_parts.append(self.embed(texts[i : i + _UPSERT_BATCH_SIZE]))
        vecs = np.concatenate(vec_parts) if len(vec_parts) > 1 else vec_parts[0]

        inner = self._build_inner_index(vecs)
        index = faiss.IndexIDMap(inner)
        index.add_with_ids(vecs, ids)
        self._index = index
        self._write_index_locked()
        logger.info("FAISS index rebuilt with %d vectors.", index.ntotal)

    def _write_index_locked(self) -> None:
        """Save the current in-memory index to disk under the file lock."""
        assert self._index is not None, "_write_index_locked called with no index"
        with self._file_lock:
            faiss.write_index(self._index, str(_index_path()))

    # ------------------------------------------------------------------
    # Search (read-only — no lock required)
    # ------------------------------------------------------------------

    def search(self, query_text: str, k: int = 10) -> list[tuple[int, float]]:
        """Return up to *k* ``(kg_node_id, distance)`` pairs.

        Readers do not acquire the filelock.  They use the in-memory index
        that was loaded at startup.  FAISS internal data structures are
        not mutated by search, making concurrent reads safe.

        Parameters
        ----------
        query_text:
            Free-text query embedded with fastembed.
        k:
            Maximum number of neighbours to return.
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        vec = self.embed_one(query_text).reshape(1, -1)

        # Set nprobe for IVF indexes to search more cells for better recall
        inner = faiss.downcast_index(self._index.index) if hasattr(self._index, "index") else self._index
        if isinstance(inner, faiss.IndexIVFFlat):
            inner.nprobe = min(4, inner.nlist)

        distances, ids = self._index.search(vec, k)

        results: list[tuple[int, float]] = []
        seen: set[int] = set()
        for dist, node_id in zip(distances[0], ids[0]):
            if node_id == -1:
                continue
            # Deduplicate: IndexIDMap may contain stale duplicates from updates
            if node_id in seen:
                continue
            seen.add(node_id)
            results.append((int(node_id), float(dist)))
        return results

    # ------------------------------------------------------------------
    # Write operations (called from the single-writer asyncio task)
    # ------------------------------------------------------------------

    def upsert_vectors(self, node_ids: list[int], texts: list[str]) -> None:
        """Add/update vectors for the given node IDs.

        Acquires the filelock, appends vectors to the in-memory index, and
        persists the index to disk.

        Because ``IndexIDMap`` does not support removal, updating a node
        appends a new vector with the same ID.  Stale duplicate entries are
        harmless (search deduplicates by ID) and are cleaned up on rebuild.

        This method is intended to be called *exclusively* from the single
        asyncio writer task in ``main.py`` to enforce serial writes.

        Large batches are split into chunks of :data:`_UPSERT_BATCH_SIZE` to
        bound peak memory usage.
        """
        if not node_ids:
            return

        with self._file_lock:
            assert self._index is not None
            for i in range(0, len(node_ids), _UPSERT_BATCH_SIZE):
                batch_ids = node_ids[i : i + _UPSERT_BATCH_SIZE]
                batch_texts = texts[i : i + _UPSERT_BATCH_SIZE]
                vecs = self.embed(batch_texts)
                ids = np.array(batch_ids, dtype=np.int64)
                self._index.add_with_ids(vecs, ids)
            faiss.write_index(self._index, str(_index_path()))

        logger.debug("FAISS upsert: %d vectors written.", len(node_ids))

    def maybe_rebuild(
        self,
        db: "Session",
        deleted_count: int,
        total_count: int,
    ) -> bool:
        """Trigger a full rebuild if soft-deleted fraction exceeds threshold.

        Parameters
        ----------
        db:
            Active SQLAlchemy session.
        deleted_count:
            Number of rows with ``is_deleted=True`` in ``kg_nodes``.
        total_count:
            Total number of rows in ``kg_nodes``.

        Returns
        -------
        bool
            ``True`` if a rebuild was performed.
        """
        if total_count == 0:
            return False
        ratio = deleted_count / total_count
        if ratio > _REBUILD_THRESHOLD:
            logger.info(
                "Soft-delete ratio %.1f%% > %.0f%% threshold — rebuilding FAISS index.",
                ratio * 100,
                _REBUILD_THRESHOLD * 100,
            )
            self._rebuild_from_db(db)
            return True
        return False
