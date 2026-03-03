from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AgentRun,
    AgentRunEvent,
    EpisodicSummary,
    PortfolioSnapshot,
    ResearchLibrary,
    UserPreferences,
)

router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_BUNDLE_VERSION = 1
_REDACTED = "***REDACTED***"
_SENSITIVE_KEY_TOKENS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "authorization",
    "auth",
    "ssn",
    "email",
    "phone",
    "dob",
    "account_number",
    "routing_number",
}


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


def _loads_json(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    try:
        parsed = json.loads(value)
        return parsed
    except Exception:
        return default


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEY_TOKENS:
        return True
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def _redact_sensitive(value: Any, *, parent_key: str | None = None) -> Any:
    if parent_key and _is_sensitive_key(parent_key):
        return _REDACTED

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, inner in value.items():
            if _is_sensitive_key(key):
                redacted[key] = _REDACTED
            else:
                redacted[key] = _redact_sensitive(inner, parent_key=key)
        return redacted

    if isinstance(value, list):
        return [_redact_sensitive(item, parent_key=parent_key) for item in value]

    return value


def _serialize_snapshot_row(row: Any) -> dict[str, Any]:
    content = _loads_json(getattr(row, "content_json", None), default={})
    citations = _loads_json(getattr(row, "citations_json", None), default=[])

    return {
        "id": row.id,
        "run_id": row.run_id,
        "category": row.category,
        "content": _redact_sensitive(content),
        "citations": _redact_sensitive(citations),
        "tags": _loads_json(getattr(row, "tags_json", None), default=[]),
        "confidence": getattr(row, "confidence", None),
        "expires_at": row.expires_at.isoformat() if getattr(row, "expires_at", None) else None,
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


def _collect_citations_from_value(value: Any, citations: list[dict[str, Any]], seen: set[str]) -> None:
    if isinstance(value, dict):
        if value.get("url"):
            url = str(value.get("url") or "").strip()
            title = str(value.get("title") or "").strip()
            source_key = f"{url}|{title}|{json.dumps(value, sort_keys=True, default=str)}"
            if source_key not in seen:
                seen.add(source_key)
                citations.append(value)

        for key, inner in value.items():
            key_norm = _normalize_key(key)
            if key_norm in {"sources", "citation", "citations"} and isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        url = str(item.get("url") or "").strip()
                        title = str(item.get("title") or "").strip()
                        source_key = f"{url}|{title}|{json.dumps(item, sort_keys=True, default=str)}"
                        if source_key not in seen:
                            seen.add(source_key)
                            citations.append(item)
            _collect_citations_from_value(inner, citations, seen)
        return

    if isinstance(value, list):
        for inner in value:
            _collect_citations_from_value(inner, citations, seen)


def _build_artifact_registry(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []

    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue

        if event.get("type") == "tool_end":
            artifacts.append(
                {
                    "artifact_type": "tool_result",
                    "event_id": event.get("id"),
                    "seq": event.get("seq"),
                    "created_at": event.get("created_at"),
                    "tool": payload.get("tool"),
                    "tool_call_id": payload.get("trace", {}).get("tool_call_id"),
                    "step_id": payload.get("step_id"),
                    "success": payload.get("success"),
                    "result_envelope": payload.get("result_envelope"),
                }
            )

        nested_artifact = payload.get("artifact")
        if nested_artifact is not None:
            artifacts.append(
                {
                    "artifact_type": "artifact",
                    "event_id": event.get("id"),
                    "seq": event.get("seq"),
                    "created_at": event.get("created_at"),
                    "data": nested_artifact,
                }
            )

        nested_artifacts = payload.get("artifacts")
        if isinstance(nested_artifacts, list):
            for item in nested_artifacts:
                artifacts.append(
                    {
                        "artifact_type": "artifact",
                        "event_id": event.get("id"),
                        "seq": event.get("seq"),
                        "created_at": event.get("created_at"),
                        "data": item,
                    }
                )

    return artifacts


def _serialize_export_event(event: AgentRunEvent) -> dict[str, Any]:
    payload_raw = _loads_json(event.payload_json, default=None)
    payload = payload_raw if isinstance(payload_raw, dict) else None
    redacted_payload = _redact_sensitive(payload) if payload is not None else None

    return {
        "id": event.id,
        "run_id": event.run_id,
        "seq": event.seq,
        "type": event.type,
        "payload": redacted_payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _build_context_snapshots(db: Session, run_id: str) -> dict[str, list[dict[str, Any]]]:
    portfolio_rows = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.id.asc())
        .all()
    )
    preference_rows = (
        db.query(UserPreferences)
        .filter(UserPreferences.run_id == run_id)
        .order_by(UserPreferences.id.asc())
        .all()
    )
    episodic_rows = (
        db.query(EpisodicSummary)
        .filter(EpisodicSummary.run_id == run_id)
        .order_by(EpisodicSummary.id.asc())
        .all()
    )
    research_rows = (
        db.query(ResearchLibrary)
        .filter(ResearchLibrary.run_id == run_id)
        .order_by(ResearchLibrary.id.asc())
        .all()
    )

    return {
        "portfolio_snapshots": [_serialize_snapshot_row(row) for row in portfolio_rows],
        "user_preferences": [_serialize_snapshot_row(row) for row in preference_rows],
        "episodic_summaries": [_serialize_snapshot_row(row) for row in episodic_rows],
        "research_library": [_serialize_snapshot_row(row) for row in research_rows],
    }


def _collect_bundle_citations(
    events: list[dict[str, Any]],
    context_snapshots: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()

    for event in events:
        _collect_citations_from_value(event.get("payload"), citations, seen)

    for rows in context_snapshots.values():
        for row in rows:
            _collect_citations_from_value(row.get("citations"), citations, seen)

    return citations


def _build_run_bundle(db: Session, run: AgentRun) -> dict[str, Any]:
    events = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.asc(), AgentRunEvent.id.asc())
        .all()
    )

    timeline = [_serialize_export_event(event) for event in events]
    context_snapshots = _build_context_snapshots(db, run.id)
    artifacts_registry = _build_artifact_registry(timeline)
    citations = _collect_bundle_citations(timeline, context_snapshots)

    return {
        "bundle_version": _BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_header": _serialize_run(run),
        "event_timeline": timeline,
        "context_snapshots": context_snapshots,
        "artifacts_registry": artifacts_registry,
        "citations": citations,
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


@router.get("/runs/{run_id}/export")
def export_run_bundle(run_id: str, db: Session = Depends(get_db)):
    run_id = _validate_run_id(run_id)
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return _build_run_bundle(db, run)
