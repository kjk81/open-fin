from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from filelock import FileLock, Timeout

if "faiss" not in sys.modules:
    faiss_stub = ModuleType("faiss")

    class _IndexIVFFlat:  # pragma: no cover - structural stub
        pass

    faiss_stub.IndexIVFFlat = _IndexIVFFlat
    faiss_stub.write_index = lambda _index, _path: None
    faiss_stub.downcast_index = lambda idx: idx
    sys.modules["faiss"] = faiss_stub

if "fastembed" not in sys.modules:
    fastembed_stub = ModuleType("fastembed")

    class _TextEmbedding:  # pragma: no cover - structural stub
        def __init__(self, model_name: str = "", **kwargs):
            self.model_name = model_name

        def embed(self, texts):
            for _ in texts:
                yield np.zeros((384,), dtype=np.float32)

    fastembed_stub.TextEmbedding = _TextEmbedding
    sys.modules["fastembed"] = fastembed_stub

from agent import vector_store as vs


class _DummyIndex:
    def __init__(self) -> None:
        self.ntotal = 0
        self._pairs: list[tuple[int, np.ndarray]] = []

    def add_with_ids(self, vecs, ids) -> None:
        for vec, node_id in zip(vecs, ids):
            self._pairs.append((int(node_id), vec))
            self.ntotal += 1


class _SearchableDummyIndex(_DummyIndex):
    def __init__(self) -> None:
        super().__init__()
        self._guard = threading.Lock()

    def add_with_ids(self, vecs, ids) -> None:
        with self._guard:
            super().add_with_ids(vecs, ids)

    def search(self, _query, k):
        with self._guard:
            dists = np.full((1, k), 0.0, dtype=np.float32)
            ids = np.full((1, k), -1, dtype=np.int64)
            top = self._pairs[:k]
            for i, (node_id, _) in enumerate(top):
                ids[0, i] = node_id
            return dists, ids


class _SerializingSpyLock:
    def __init__(self) -> None:
        self._inner = threading.Lock()
        self.active_writers = 0
        self.max_active_writers = 0

    def __enter__(self):
        self._inner.acquire()
        self.active_writers += 1
        if self.active_writers > self.max_active_writers:
            self.max_active_writers = self.active_writers
        return self

    def __exit__(self, exc_type, exc, tb):
        self.active_writers -= 1
        self._inner.release()
        return False


def _build_manager(lock_obj) -> vs.FaissManager:
    mgr = vs.FaissManager.__new__(vs.FaissManager)
    mgr._index = _DummyIndex()
    mgr._file_lock = lock_obj
    mgr.embed = lambda texts: np.zeros((len(texts), vs._EMBED_DIM), dtype=np.float32)
    mgr.embed_one = lambda text: np.zeros((vs._EMBED_DIM,), dtype=np.float32)
    return mgr


def test_single_writer_lock_serializes_concurrent_upserts(monkeypatch):
    monkeypatch.setattr(vs.faiss, "write_index", lambda _index, _path: None)

    spy_lock = _SerializingSpyLock()
    manager = _build_manager(spy_lock)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(manager.upsert_vectors, [i], [f"node-{i}"])
            for i in range(40)
        ]
        for future in futures:
            future.result()

    assert manager._index.ntotal == 40
    assert spy_lock.max_active_writers == 1


def test_concurrent_writer_rejected_on_filelock_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(vs.faiss, "write_index", lambda _index, _path: None)
    monkeypatch.setattr(vs, "_index_path", lambda: Path(tmp_path) / "openfin.index")

    lock_path = Path(tmp_path) / "openfin.index.lock"
    holder = FileLock(str(lock_path), timeout=1.0)
    holder.acquire()

    manager = _build_manager(FileLock(str(lock_path), timeout=0.05))

    with pytest.raises(Timeout):
        manager.upsert_vectors([1], ["AAPL"])

    holder.release()


def test_maybe_rebuild_strictly_above_threshold():
    manager = vs.FaissManager.__new__(vs.FaissManager)
    manager._rebuild_from_db = MagicMock()

    exact_ten_percent = manager.maybe_rebuild(SimpleNamespace(), deleted_count=1, total_count=10)
    assert exact_ten_percent is False
    manager._rebuild_from_db.assert_not_called()

    above_ten_percent = manager.maybe_rebuild(SimpleNamespace(), deleted_count=2, total_count=10)
    assert above_ten_percent is True
    manager._rebuild_from_db.assert_called_once()


def test_concurrent_search_and_write_operations_do_not_race(monkeypatch):
    monkeypatch.setattr(vs.faiss, "write_index", lambda _index, _path: None)

    manager = _build_manager(_SerializingSpyLock())
    manager._index = _SearchableDummyIndex()

    search_errors: list[Exception] = []

    def _writer() -> None:
        for i in range(120):
            manager.upsert_vectors([i], [f"node-{i}"])

    def _reader() -> None:
        try:
            for _ in range(120):
                manager.search("AAPL", k=5)
        except Exception as exc:  # pragma: no cover - failure path assertion below
            search_errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_f = pool.submit(_writer)
        reader_f = pool.submit(_reader)
        writer_f.result()
        reader_f.result()

    assert search_errors == []
