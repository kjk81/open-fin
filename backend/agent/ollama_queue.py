"""Ollama concurrency gate — singleton semaphore + chat-active event.

Ensures only one Ollama request runs at a time (local LLM is single-threaded)
while allowing cloud providers to run with higher concurrency.  A chat-active
event lets analysis requests queue politely behind an active chat stream.

Usage
-----
Call ``init_queue(mode)`` once at startup from ``main.py``.

In routers / LLM wrappers::

    async with ollama_chat_slot():
        ...  # holds the slot for the entire chat stream

    async with ollama_analysis_slot(timeout=120):
        ...  # waits for chat to finish, then acquires
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons — populated by init_queue()
# ---------------------------------------------------------------------------

_ollama_sem: asyncio.Semaphore | None = None
_cloud_sem: asyncio.Semaphore | None = None
_chat_active: asyncio.Event | None = None  # *cleared* when chat is idle
_queue_mode: str | None = None


def init_queue(mode: str) -> None:
    """Initialise the concurrency primitives.

    Args:
        mode: ``"ollama"`` (Semaphore(1)) or ``"cloud"`` (Semaphore(10)).
    """
    global _ollama_sem, _cloud_sem, _chat_active, _queue_mode

    _queue_mode = (mode or "ollama").lower()

    _ollama_sem = asyncio.Semaphore(1)
    _cloud_sem = asyncio.Semaphore(10)
    # Event semantics: *set* = chat idle (analysis may proceed)
    _chat_active = asyncio.Event()
    _chat_active.set()  # start idle

    logger.info("Ollama queue initialised (mode=%s)", _queue_mode)


# ---------------------------------------------------------------------------
# Public context managers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def ollama_chat_slot() -> AsyncIterator[None]:
    """Acquire the Ollama semaphore for a chat stream and mark chat as active.

    Non-Ollama callers can still use this to signal "chat is running" so that
    analysis requests wait.
    """
    if _ollama_sem is None or _chat_active is None:
        # Queue not initialised — pass through (tests / import time)
        yield
        return

    if _queue_mode != "ollama":
        # Cloud / non-ollama modes should not serialize requests.
        yield
        return

    _chat_active.clear()  # signal: chat in progress
    try:
        await _ollama_sem.acquire()
        try:
            yield
        finally:
            _ollama_sem.release()
    finally:
        _chat_active.set()  # signal: chat finished


@asynccontextmanager
async def ollama_analysis_slot(timeout: float = 120.0) -> AsyncIterator[str]:
    """Wait for any active chat to complete, then acquire the Ollama semaphore.

    Yields a status string:
        ``"immediate"`` — no waiting was required
        ``"queued"``    — had to wait for chat to finish

    Raises ``asyncio.TimeoutError`` if the combined wait exceeds *timeout*.
    """
    if _ollama_sem is None or _chat_active is None:
        yield "immediate"
        return

    if _queue_mode != "ollama":
        # Cloud / non-ollama modes should not wait on chat or semaphore.
        yield "immediate"
        return

    status = "immediate"

    if not _chat_active.is_set():
        status = "queued"
        logger.info("Analysis slot queued — waiting for active chat to finish")
        await asyncio.wait_for(_chat_active.wait(), timeout=timeout)

    await asyncio.wait_for(_ollama_sem.acquire(), timeout=timeout)
    try:
        yield status
    finally:
        _ollama_sem.release()


def is_chat_active() -> bool:
    """Return True if a chat stream currently holds the slot."""
    if _chat_active is None or _queue_mode != "ollama":
        return False
    return not _chat_active.is_set()
