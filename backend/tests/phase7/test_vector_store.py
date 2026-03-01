"""Tests for agent/vector_store.py — FaissManager lifecycle, search, upsert."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

# The phase7 conftest stubs faiss/fastembed as MagicMocks.  We need
# controlled behaviour for these tests, so we build our own lightweight
# stubs *before* importing vector_store.

import sys
from types import ModuleType


def _build_faiss_stub():
    """Create a minimal faiss stub with real-enough data structures."""
    mod = ModuleType("faiss")

    class IndexFlatL2:
        def __init__(self, d: int = 384):
            self.d = d
            self.ntotal = 0
            self._vecs: list[np.ndarray] = []
            self.nlist = 0  # not IVF

        def add(self, x):
            self._vecs.extend(x)
            self.ntotal += len(x)

    class IndexIVFFlat:
        def __init__(self, quantizer, d, nlist):
            self.quantizer = quantizer
            self.d = d
            self.nlist = nlist
            self.ntotal = 0
            self.nprobe = 1

        def train(self, x):
            pass

    class IndexIDMap:
        def __init__(self, inner):
            self.index = inner
            self.ntotal = 0
            self._ids: list[int] = []
            self._vecs: list[np.ndarray] = []

        def add_with_ids(self, vecs, ids):
            for v, i in zip(vecs, ids):
                self._vecs.append(v)
                self._ids.append(int(i))
            self.ntotal += len(ids)

        def search(self, query, k):
            """Brute-force L2 search on stored vectors."""
            if not self._vecs:
                return (
                    np.full((1, k), float("inf"), dtype=np.float32),
                    np.full((1, k), -1, dtype=np.int64),
                )
            stored = np.array(self._vecs, dtype=np.float32)
            dists = np.sum((stored - query) ** 2, axis=1)
            n = min(k, len(dists))
            topk = np.argsort(dists)[:n]
            result_dists = np.full((1, k), float("inf"), dtype=np.float32)
            result_ids = np.full((1, k), -1, dtype=np.int64)
            for j, idx in enumerate(topk):
                result_dists[0, j] = dists[idx]
                result_ids[0, j] = self._ids[idx]
            return result_dists, result_ids

    mod.IndexFlatL2 = IndexFlatL2
    mod.IndexIVFFlat = IndexIVFFlat
    mod.IndexIDMap = IndexIDMap
    mod.IO_FLAG_MMAP = 0
    mod.IO_FLAG_READ_ONLY = 0

    # read_index / write_index stubs
    _saved: dict[str, IndexIDMap] = {}

    def write_index(index, path):
        _saved[path] = index

    def read_index(path, flags=0):
        if path in _saved:
            return _saved[path]
        raise FileNotFoundError(f"No index at {path}")

    def downcast_index(idx):
        return idx

    mod.write_index = write_index
    mod.read_index = read_index
    mod.downcast_index = downcast_index
    mod._saved = _saved  # expose for test inspection
    return mod


def _build_fastembed_stub():
    """Minimal fastembed stub returning deterministic embeddings."""
    mod = ModuleType("fastembed")

    class TextEmbedding:
        def __init__(self, model_name: str = "", **kwargs):
            pass

        def embed(self, texts):
            """Yield one 384-dim vector per text (hash-based for determinism)."""
            for t in texts:
                rng = np.random.RandomState(abs(hash(t)) % (2**31))
                yield rng.randn(384).astype(np.float32)

    mod.TextEmbedding = TextEmbedding
    return mod


# Inject stubs BEFORE importing vector_store
_faiss_stub = _build_faiss_stub()
_fastembed_stub = _build_fastembed_stub()
sys.modules["faiss"] = _faiss_stub
sys.modules["fastembed"] = _fastembed_stub

from agent.vector_store import (
    FaissManager,
    _EMBED_DIM,
    _EMBED_MODEL_NAME,
    _IVF_MIN_VECTORS,
    _META_SCHEMA_VERSION,
    _REBUILD_THRESHOLD,
    _UPSERT_BATCH_SIZE,
    _index_dir,
    _index_path,
    _is_index_compatible,
    _read_meta,
    _write_meta,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _redirect_faiss_dir(tmp_path, monkeypatch):
    """Point FAISS index files to a temporary directory."""
    monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
    # Clear any cached index in the faiss stub
    _faiss_stub._saved.clear()


@pytest.fixture()
def manager() -> FaissManager:
    return FaissManager()


@pytest.fixture()
def db_with_nodes(db_session):
    """Insert a few KGNode rows into the test DB and return the session."""
    from models import KGNode  # noqa: F811

    for i, name in enumerate(["AAPL", "MSFT", "GOOG"], start=1):
        db_session.add(KGNode(
            id=i,
            node_type="company",
            name=name,
            metadata_json=json.dumps({"company_name": name}),
            is_deleted=False,
        ))
    db_session.commit()
    return db_session


# ---------------------------------------------------------------------------
# Embedding tests
# ---------------------------------------------------------------------------

class TestEmbedding:
    def test_embed_one_returns_384_float32(self, manager):
        vec = manager.embed_one("hello world")
        assert vec.shape == (384,)
        assert vec.dtype == np.float32

    def test_embed_batch(self, manager):
        vecs = manager.embed(["hello", "world"])
        assert vecs.shape == (2, 384)
        assert vecs.dtype == np.float32


# ---------------------------------------------------------------------------
# load_or_build tests
# ---------------------------------------------------------------------------

class TestLoadOrBuild:
    def test_fresh_build_when_no_file(self, manager, db_with_nodes, tmp_path):
        """No index file exists → builds from DB, writes to disk."""
        manager.load_or_build(db_with_nodes)
        assert manager._index is not None
        assert manager._index.ntotal == 3
        # Index file should have been written
        assert str(tmp_path / "openfin.index") in _faiss_stub._saved

    def test_load_existing_index(self, manager, db_with_nodes, tmp_path):
        """Pre-existing index → loaded, no rebuild."""
        # Build first
        manager.load_or_build(db_with_nodes)
        saved = manager._index

        # Create a new manager and "load" from disk
        mgr2 = FaissManager()
        mgr2.load_or_build(db_with_nodes)
        assert mgr2._index is not None
        assert mgr2._index.ntotal == saved.ntotal

    def test_corrupt_file_triggers_rebuild(self, manager, db_with_nodes, tmp_path):
        """When read_index raises, rebuild from DB rather than crashing."""
        # Pre-write a "corrupt" entry
        idx_path = str(tmp_path / "openfin.index")
        _faiss_stub._saved[idx_path] = None  # will cause AttributeError on access

        # Patch read_index to raise on this path
        orig_read = _faiss_stub.read_index

        def _bad_read(path, flags=0):
            if "openfin.index" in path:
                raise RuntimeError("corrupt")
            return orig_read(path, flags)

        _faiss_stub.read_index = _bad_read
        try:
            manager.load_or_build(db_with_nodes)
            assert manager._index is not None
            assert manager._index.ntotal == 3
        finally:
            _faiss_stub.read_index = orig_read

    def test_file_not_found_triggers_rebuild(self, manager, db_with_nodes, tmp_path):
        """Explicit FileNotFoundError handling triggers rebuild."""
        orig_read = _faiss_stub.read_index

        def _fnf_read(path, flags=0):
            raise FileNotFoundError(f"No file: {path}")

        # Make the file "exist" for path.exists() check but fail on read
        idx_file = tmp_path / "openfin.index"
        idx_file.touch()

        _faiss_stub.read_index = _fnf_read
        try:
            manager.load_or_build(db_with_nodes)
            assert manager._index is not None
            assert manager._index.ntotal == 3
        finally:
            _faiss_stub.read_index = orig_read

    def test_empty_db_creates_empty_index(self, manager, db_session):
        manager.load_or_build(db_session)
        assert manager._index is not None
        assert manager._index.ntotal == 0


# ---------------------------------------------------------------------------
# upsert_vectors tests
# ---------------------------------------------------------------------------

class TestUpsertVectors:
    def test_basic_upsert(self, manager, db_with_nodes):
        manager.load_or_build(db_with_nodes)
        initial = manager._index.ntotal

        manager.upsert_vectors([100, 101], ["New company A", "New company B"])
        assert manager._index.ntotal == initial + 2

    def test_empty_noop(self, manager, db_with_nodes):
        manager.load_or_build(db_with_nodes)
        initial = manager._index.ntotal
        manager.upsert_vectors([], [])
        assert manager._index.ntotal == initial

    def test_batch_limit_respected(self, manager, db_with_nodes):
        """Upserting >500 vectors should still work (chunked internally)."""
        manager.load_or_build(db_with_nodes)

        n = _UPSERT_BATCH_SIZE + 100  # 600
        ids = list(range(1000, 1000 + n))
        texts = [f"text_{i}" for i in ids]
        manager.upsert_vectors(ids, texts)
        assert manager._index.ntotal == 3 + n


# ---------------------------------------------------------------------------
# search tests
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_returns_ranked(self, manager, db_with_nodes):
        manager.load_or_build(db_with_nodes)
        results = manager.search("AAPL")
        assert len(results) > 0
        # Results are (node_id, distance) pairs
        for node_id, dist in results:
            assert isinstance(node_id, int)
            assert isinstance(dist, float)

    def test_search_empty_index(self, manager, db_session):
        manager.load_or_build(db_session)
        results = manager.search("anything")
        assert results == []

    def test_search_deduplicates(self, manager, db_with_nodes):
        """Duplicate IDs from upsert should be deduplicated in search."""
        manager.load_or_build(db_with_nodes)
        # Upsert a duplicate ID
        manager.upsert_vectors([1], ["AAPL updated text"])
        results = manager.search("AAPL", k=10)
        ids_returned = [r[0] for r in results]
        assert len(ids_returned) == len(set(ids_returned)), "Duplicate IDs in search results"


# ---------------------------------------------------------------------------
# maybe_rebuild tests
# ---------------------------------------------------------------------------

class TestMaybeRebuild:
    def test_triggers_when_over_threshold(self, manager, db_with_nodes):
        manager.load_or_build(db_with_nodes)
        # 2 deleted out of 3 = 66.7% > 10%
        result = manager.maybe_rebuild(db_with_nodes, deleted_count=2, total_count=3)
        assert result is True

    def test_no_trigger_below_threshold(self, manager, db_with_nodes):
        manager.load_or_build(db_with_nodes)
        # 0 deleted out of 100 = 0%
        result = manager.maybe_rebuild(db_with_nodes, deleted_count=0, total_count=100)
        assert result is False

    def test_zero_total_returns_false(self, manager, db_session):
        manager.load_or_build(db_session)
        result = manager.maybe_rebuild(db_session, deleted_count=0, total_count=0)
        assert result is False


# ---------------------------------------------------------------------------
# IVF vs FlatL2 threshold
# ---------------------------------------------------------------------------

class TestIndexTypeThreshold:
    def test_below_threshold_uses_flat(self, manager):
        vecs = np.random.randn(50, 384).astype(np.float32)
        inner = manager._build_inner_index(vecs)
        assert type(inner).__name__ == "IndexFlatL2"

    def test_at_threshold_uses_ivf(self, manager):
        vecs = np.random.randn(_IVF_MIN_VECTORS, 384).astype(np.float32)
        inner = manager._build_inner_index(vecs)
        assert type(inner).__name__ == "IndexIVFFlat"


# ---------------------------------------------------------------------------
# text_for_node static method
# ---------------------------------------------------------------------------

class TestTextForNode:
    def test_ticker(self):
        text = FaissManager.text_for_node(
            "ticker", "AAPL", {"company_name": "Apple", "sector": "Tech"}
        )
        assert "AAPL" in text
        assert "Apple" in text
        assert "Tech" in text

    def test_sector(self):
        text = FaissManager.text_for_node("sector", "sector:Technology")
        assert "Sector: Technology" == text

    def test_industry(self):
        text = FaissManager.text_for_node("industry", "industry:Software")
        assert "Industry: Software" == text

    def test_unknown_type(self):
        text = FaissManager.text_for_node("other", "some_name")
        assert text == "some_name"


# ---------------------------------------------------------------------------
# _rebuild_from_db batch embedding
# ---------------------------------------------------------------------------

class TestRebuildBatching:
    def test_rebuild_embeds_in_batches(self, manager, db_session):
        """When node count exceeds batch size, embedding is done in chunks."""
        from models import KGNode as KGN

        # Insert enough nodes to exceed one batch
        n = _UPSERT_BATCH_SIZE + 50
        for i in range(1, n + 1):
            db_session.add(KGN(
                id=i,
                node_type="company",
                name=f"SYM{i}",
                metadata_json="{}",
                is_deleted=False,
            ))
        db_session.commit()

        manager._rebuild_from_db(db_session)
        assert manager._index is not None
        assert manager._index.ntotal == n


# ---------------------------------------------------------------------------
# Sidecar metadata helpers and index compatibility
# ---------------------------------------------------------------------------


class TestIndexCompatibility:
    """Unit tests for _is_index_compatible and _write_meta/_read_meta helpers."""

    def test_compatible_returns_true_for_exact_match(self) -> None:
        meta = {
            "schema_version": _META_SCHEMA_VERSION,
            "embed_model": _EMBED_MODEL_NAME,
            "embed_dim": _EMBED_DIM,
        }
        assert _is_index_compatible(meta) is True

    def test_none_meta_returns_false(self) -> None:
        """Missing sidecar (legacy index) must force rebuild."""
        assert _is_index_compatible(None) is False

    def test_empty_dict_returns_false(self) -> None:
        assert _is_index_compatible({}) is False

    def test_wrong_model_name_returns_false(self) -> None:
        meta = {
            "schema_version": _META_SCHEMA_VERSION,
            "embed_model": "wrong/model",
            "embed_dim": _EMBED_DIM,
        }
        assert _is_index_compatible(meta) is False

    def test_wrong_embed_dim_returns_false(self) -> None:
        meta = {
            "schema_version": _META_SCHEMA_VERSION,
            "embed_model": _EMBED_MODEL_NAME,
            "embed_dim": 9999,
        }
        assert _is_index_compatible(meta) is False

    def test_wrong_schema_version_returns_false(self) -> None:
        meta = {
            "schema_version": _META_SCHEMA_VERSION + 999,
            "embed_model": _EMBED_MODEL_NAME,
            "embed_dim": _EMBED_DIM,
        }
        assert _is_index_compatible(meta) is False


class TestMetaHelpers:
    """Write/read/compatibility round-trip for the sidecar meta file."""

    def test_write_then_read_round_trip(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        _write_meta(node_count=42)
        meta = _read_meta()
        assert meta is not None
        assert meta["embed_model"] == _EMBED_MODEL_NAME
        assert meta["embed_dim"] == _EMBED_DIM
        assert meta["schema_version"] == _META_SCHEMA_VERSION
        assert meta["node_count"] == 42
        assert "built_at" in meta

    def test_read_missing_file_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        # No meta file — _read_meta should return None gracefully
        meta = _read_meta()
        assert meta is None

    def test_incompatible_after_write_with_patched_model(self, tmp_path, monkeypatch) -> None:
        """Simulate an upgrade: old meta has a different model name."""
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path))
        import json
        stale_meta = {
            "schema_version": _META_SCHEMA_VERSION,
            "embed_model": "old-model/v1",
            "embed_dim": _EMBED_DIM,
            "node_count": 10,
            "built_at": "2025-01-01T00:00:00+00:00",
        }
        (tmp_path / "openfin.meta.json").write_text(json.dumps(stale_meta), encoding="utf-8")
        meta = _read_meta()
        assert _is_index_compatible(meta) is False


class TestLoadOrBuildCompatibilityCheck:
    """load_or_build must rebuild when the on-disk metadata is incompatible."""

    def test_rebuilds_when_sidecar_has_wrong_model(self, manager, db_with_nodes, tmp_path) -> None:
        import json
        # Write stale metadata with a different embed model
        meta_file = tmp_path / "openfin.meta.json"
        stale = {
            "schema_version": _META_SCHEMA_VERSION,
            "embed_model": "old-model/obsolete",
            "embed_dim": _EMBED_DIM,
            "node_count": 3,
            "built_at": "2025-01-01T00:00:00+00:00",
        }
        meta_file.write_text(json.dumps(stale), encoding="utf-8")

        # Also touch the index file so path.exists() == True
        idx_file = tmp_path / "openfin.index"
        idx_file.touch()

        manager.load_or_build(db_with_nodes)
        # After rebuild, sidecar should reflect the correct current model
        fresh_meta = _read_meta()
        assert fresh_meta is not None
        assert fresh_meta["embed_model"] == _EMBED_MODEL_NAME
        # And the index was successfully built from 3 nodes
        assert manager._index is not None
        assert manager._index.ntotal == 3

    def test_rebuilds_when_sidecar_missing(self, manager, db_with_nodes, tmp_path) -> None:
        """No sidecar file at all forces a rebuild even if index file exists."""
        # Touch the index file to simulate a legacy index without metadata
        idx_file = tmp_path / "openfin.index"
        idx_file.touch()
        # No meta file written
        manager.load_or_build(db_with_nodes)
        assert manager._index is not None
        assert manager._index.ntotal == 3
