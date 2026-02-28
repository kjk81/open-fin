"""Alert endpoints: SSE stream + REST access for anomaly alerts.

Endpoints
---------
``GET /api/alerts/stream``  — Server-Sent Events stream (one event per anomaly)
``GET /api/alerts``         — Paginated list of recent alerts
``GET /api/alerts/{id}``    — Single alert with optional research summary
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import AnomalyAlert
from tools.anomaly_worker import register_sse_queue, unregister_sse_queue

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

async def _sse_event_generator(request: Request, queue: asyncio.Queue):
    """Yield SSE-formatted events from the broadcast queue until client disconnects."""
    try:
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                event_type = payload.pop("event", "anomaly")
                data = json.dumps(payload)
                yield f"event: {event_type}\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                # Send keep-alive comment to prevent connection timeout
                yield ": keep-alive\n\n"
    except asyncio.CancelledError:
        pass


@router.get("/alerts/stream")
async def alerts_stream(request: Request):
    """SSE endpoint — keeps connection open.

    Anomaly worker pushes events like::

        event: anomaly
        data: {"alert_id": 123, "symbol": "AAPL", "signal_type": "price_drop", ...}
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    await register_sse_queue(queue)

    async def _on_disconnect():
        await unregister_sse_queue(queue)

    response = StreamingResponse(
        _sse_event_generator(request, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    response.background = _on_disconnect  # type: ignore[assignment]
    return response


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.get("/alerts")
def list_alerts(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Return recent anomaly alerts, newest first."""
    rows = (
        db.query(AnomalyAlert)
        .order_by(AnomalyAlert.detected_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "symbol": a.symbol,
            "signal_type": a.signal_type,
            "magnitude": a.magnitude,
            "detected_at": a.detected_at.isoformat() if a.detected_at else None,
            "researched": a.researched,
            "research_summary": a.research_summary,
        }
        for a in rows
    ]


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    """Return a single alert by ID, including its research summary."""
    alert = db.query(AnomalyAlert).filter(AnomalyAlert.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {
        "id": alert.id,
        "symbol": alert.symbol,
        "signal_type": alert.signal_type,
        "magnitude": alert.magnitude,
        "detected_at": alert.detected_at.isoformat() if alert.detected_at else None,
        "researched": alert.researched,
        "research_summary": alert.research_summary,
    }
