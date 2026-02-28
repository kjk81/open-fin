"""Background anomaly monitor: polls watchlist + portfolio symbols for technical
anomalies and broadcasts alerts via SSE to connected frontends.

The worker runs as an ``asyncio.Task`` started in ``main.py``'s lifespan
alongside the FAISS writer task.

Configurable via environment variables
---------------------------------------
``ANOMALY_INTERVAL_MINUTES``  — polling interval (default 5)
``ANOMALY_PRICE_DROP``        — single-day price drop threshold (default 0.05)
``ANOMALY_VOLUME_SPIKE``      — volume spike multiplier (default 2.0)
``ANOMALY_GAP_DOWN``          — overnight gap-down threshold (default 0.03)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from database import SessionLocal
from models import AnomalyAlert, UserPortfolio, Watchlist
from tools.finance import detect_anomalies

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env-var overridable)
# ---------------------------------------------------------------------------
_INTERVAL_MINUTES = float(os.getenv("ANOMALY_INTERVAL_MINUTES", "5"))
_PRICE_DROP = float(os.getenv("ANOMALY_PRICE_DROP", "0.05"))
_VOLUME_SPIKE = float(os.getenv("ANOMALY_VOLUME_SPIKE", "2.0"))
_GAP_DOWN = float(os.getenv("ANOMALY_GAP_DOWN", "0.03"))


# ---------------------------------------------------------------------------
# SSE broadcast registry
# ---------------------------------------------------------------------------
# Each connected SSE client registers an asyncio.Queue here.  The worker
# pushes alert dicts to every queue so every connection receives it.
_sse_queues: list[asyncio.Queue] = []
_sse_lock = asyncio.Lock()


async def register_sse_queue(q: asyncio.Queue) -> None:
    """Register a new SSE client queue for broadcast."""
    async with _sse_lock:
        _sse_queues.append(q)


async def unregister_sse_queue(q: asyncio.Queue) -> None:
    """Remove a disconnected SSE client queue."""
    async with _sse_lock:
        try:
            _sse_queues.remove(q)
        except ValueError:
            pass


async def _broadcast(payload: dict) -> None:
    """Push *payload* to every connected SSE client queue."""
    async with _sse_lock:
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE queue full — dropping alert for a client.")


# ---------------------------------------------------------------------------
# Symbol collection
# ---------------------------------------------------------------------------
def _collect_symbols() -> list[str]:
    """Read unique symbols from the Watchlist + UserPortfolio tables."""
    db = SessionLocal()
    try:
        symbols: set[str] = set()
        for row in db.query(Watchlist.ticker).all():
            symbols.add(row[0].upper())
        for row in db.query(UserPortfolio.symbol).all():
            symbols.add(row[0].upper())
        return sorted(symbols)
    except Exception as exc:
        logger.warning("anomaly_worker: failed to collect symbols: %s", exc)
        return []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Persist + broadcast helper
# ---------------------------------------------------------------------------
async def _handle_signals(signals) -> None:
    """Persist each AnomalySignal and push an SSE event."""
    if not signals:
        return

    db = SessionLocal()
    try:
        for sig in signals:
            alert = AnomalyAlert(
                symbol=sig.symbol,
                signal_type=sig.signal_type,
                magnitude=sig.magnitude,
                detected_at=sig.detected_at or datetime.now(tz=timezone.utc).replace(tzinfo=None),
            )
            db.add(alert)
            db.flush()  # populate alert.id

            await _broadcast({
                "event": "anomaly",
                "alert_id": alert.id,
                "symbol": alert.symbol,
                "signal_type": alert.signal_type,
                "magnitude": alert.magnitude,
                "context_summary": sig.context_summary,
            })

            logger.info(
                "AnomalyAlert persisted: id=%s symbol=%s type=%s mag=%.4f",
                alert.id, alert.symbol, alert.signal_type, alert.magnitude,
            )

        db.commit()
    except Exception as exc:
        logger.error("anomaly_worker: persist/broadcast failed: %s", exc)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def anomaly_worker_loop() -> None:
    """Long-running async task polling for anomalies every N minutes.

    Call ``asyncio.create_task(anomaly_worker_loop())`` in the app lifespan.
    Send ``asyncio.CancelledError`` to stop gracefully.
    """
    logger.info(
        "Anomaly worker started (interval=%s min, thresholds: drop=%.2f, vol=%.1f×, gap=%.2f).",
        _INTERVAL_MINUTES, _PRICE_DROP, _VOLUME_SPIKE, _GAP_DOWN,
    )

    while True:
        try:
            symbols = _collect_symbols()
            if symbols:
                logger.debug("anomaly_worker: scanning %d symbols", len(symbols))
                signals = await detect_anomalies(
                    symbols,
                    price_drop_threshold=_PRICE_DROP,
                    volume_spike_multiplier=_VOLUME_SPIKE,
                    gap_down_threshold=_GAP_DOWN,
                )
                await _handle_signals(signals)
            else:
                logger.debug("anomaly_worker: no symbols in watchlist/portfolio — skipping.")
        except asyncio.CancelledError:
            logger.info("Anomaly worker cancelled.")
            return
        except Exception:
            logger.exception("Unhandled error in anomaly worker loop.")

        try:
            await asyncio.sleep(_INTERVAL_MINUTES * 60)
        except asyncio.CancelledError:
            logger.info("Anomaly worker cancelled during sleep.")
            return
