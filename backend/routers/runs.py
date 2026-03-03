from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import AgentRun, AgentRunEvent

router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_run_id(run_id: str) -> str:
    if not _UUID_RE.match(run_id):
        raise HTTPException(status_code=422, detail="run_id must be a valid UUID")
    return run_id


def _serialize_run(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "mode": run.mode,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _serialize_event(event: AgentRunEvent) -> dict:
    payload: dict | None = None
    try:
        parsed = json.loads(event.payload_json or "{}")
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = None

    return {
        "id": event.id,
        "run_id": event.run_id,
        "seq": event.seq,
        "type": event.type,
        "payload_json": event.payload_json,
        "payload": payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run_id = _validate_run_id(run_id)
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _serialize_run(run)


@router.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    run_id = _validate_run_id(run_id)
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    query = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.seq.asc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()

    return {
        "run_id": run_id,
        "total": total,
        "items": [_serialize_event(e) for e in items],
    }
