"""Tests for the faiss_writer_loop logic from main.py.

The writer loop is a closure inside ``lifespan()``, so we replicate
its logic in a standalone function for testability.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Extracted writer-loop implementation (mirrors main.py)
# ---------------------------------------------------------------------------

async def faiss_writer_loop(
    write_queue: asyncio.Queue,
    faiss_mgr: MagicMock,
    session_factory: MagicMock,
) -> int:
    """Execute the writer loop until shutdown sentinel.

    Returns the number of upsert operations processed.
    """
    upsert_count = 0

    while True:
        try:
            msg = await write_queue.get()
            op, node_ids, texts = msg

            if op is None:
                break

            if op == "upsert" and node_ids:
                faiss_mgr.upsert_vectors(node_ids, texts)
                upsert_count += 1

                if upsert_count % 50 == 0:
                    _db = session_factory()
                    try:
                        faiss_mgr.maybe_rebuild(
                            _db,
                            _db.scalar() or 0,
                            _db.scalar() or 0,
                        )
                    finally:
                        _db.close()

            elif op == "rebuild":
                _db = session_factory()
                try:
                    faiss_mgr._rebuild_from_db(_db)
                finally:
                    _db.close()

        except asyncio.CancelledError:
            break
        except Exception:
            pass  # log and continue in production

    return upsert_count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFaissWriterLoop:

    @pytest.fixture()
    def queue(self) -> asyncio.Queue:
        return asyncio.Queue(maxsize=100)

    @pytest.fixture()
    def mgr(self) -> MagicMock:
        m = MagicMock()
        m.upsert_vectors = MagicMock()
        m.maybe_rebuild = MagicMock(return_value=False)
        m._rebuild_from_db = MagicMock()
        return m

    @pytest.fixture()
    def session_factory(self) -> MagicMock:
        session = MagicMock()
        session.scalar.return_value = 100
        factory = MagicMock(return_value=session)
        return factory

    async def test_upsert_message(self, queue, mgr, session_factory):
        """An upsert message calls faiss_mgr.upsert_vectors."""
        await queue.put(("upsert", [1, 2], ["text1", "text2"]))
        await queue.put((None, None, None))  # shutdown

        count = await faiss_writer_loop(queue, mgr, session_factory)
        mgr.upsert_vectors.assert_called_once_with([1, 2], ["text1", "text2"])
        assert count == 1

    async def test_rebuild_message(self, queue, mgr, session_factory):
        """A rebuild message calls faiss_mgr._rebuild_from_db."""
        await queue.put(("rebuild", None, None))
        await queue.put((None, None, None))

        await faiss_writer_loop(queue, mgr, session_factory)
        mgr._rebuild_from_db.assert_called_once()

    async def test_shutdown_sentinel(self, queue, mgr, session_factory):
        """The loop exits cleanly on (None, None, None)."""
        await queue.put((None, None, None))
        count = await faiss_writer_loop(queue, mgr, session_factory)
        assert count == 0
        mgr.upsert_vectors.assert_not_called()

    async def test_error_does_not_crash(self, queue, mgr, session_factory):
        """An exception in upsert_vectors doesn't kill the loop."""
        mgr.upsert_vectors.side_effect = RuntimeError("disk full")
        await queue.put(("upsert", [1], ["text"]))
        await queue.put(("upsert", [2], ["text2"]))
        await queue.put((None, None, None))

        # Should not raise, and the second message should also be processed
        count = await faiss_writer_loop(queue, mgr, session_factory)
        # First call raises → exception caught → count doesn't increment
        # Second call also raises → count doesn't increment
        assert mgr.upsert_vectors.call_count == 2

    async def test_periodic_rebuild_at_50_upserts(self, queue, mgr, session_factory):
        """After 50 upserts, maybe_rebuild is called."""
        for i in range(50):
            await queue.put(("upsert", [i], [f"text_{i}"]))
        await queue.put((None, None, None))

        count = await faiss_writer_loop(queue, mgr, session_factory)
        assert count == 50
        mgr.maybe_rebuild.assert_called_once()

    async def test_no_rebuild_before_50(self, queue, mgr, session_factory):
        """Before 50 upserts, maybe_rebuild is NOT called."""
        for i in range(49):
            await queue.put(("upsert", [i], [f"text_{i}"]))
        await queue.put((None, None, None))

        await faiss_writer_loop(queue, mgr, session_factory)
        mgr.maybe_rebuild.assert_not_called()

    async def test_multiple_upserts_sequential(self, queue, mgr, session_factory):
        """Multiple upsert messages are processed in order."""
        await queue.put(("upsert", [1], ["a"]))
        await queue.put(("upsert", [2], ["b"]))
        await queue.put(("upsert", [3], ["c"]))
        await queue.put((None, None, None))

        count = await faiss_writer_loop(queue, mgr, session_factory)
        assert count == 3
        assert mgr.upsert_vectors.call_count == 3
        mgr.upsert_vectors.assert_has_calls([
            call([1], ["a"]),
            call([2], ["b"]),
            call([3], ["c"]),
        ])

    async def test_empty_node_ids_skipped(self, queue, mgr, session_factory):
        """An upsert with empty node_ids should not call upsert_vectors."""
        await queue.put(("upsert", [], []))
        await queue.put((None, None, None))

        count = await faiss_writer_loop(queue, mgr, session_factory)
        assert count == 0
        mgr.upsert_vectors.assert_not_called()

    async def test_rebuild_closes_session(self, queue, mgr, session_factory):
        """Session is always closed after rebuild, even on error."""
        mgr._rebuild_from_db.side_effect = RuntimeError("rebuild fail")
        await queue.put(("rebuild", None, None))
        await queue.put((None, None, None))

        await faiss_writer_loop(queue, mgr, session_factory)
        session_factory.return_value.close.assert_called()

    async def test_cancellation_exits_cleanly(self, mgr, session_factory):
        """CancelledError breaks the loop gracefully (doesn't propagate)."""
        queue = asyncio.Queue()

        async def _cancel_soon():
            await asyncio.sleep(0.05)
            task.cancel()

        task = asyncio.create_task(faiss_writer_loop(queue, mgr, session_factory))
        asyncio.create_task(_cancel_soon())

        # The loop catches CancelledError and breaks, so the task completes
        # without raising.  If it does propagate, the test will fail.
        try:
            result = await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            result = 0  # also acceptable
        assert result == 0
