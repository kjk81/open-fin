from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

_LOCK = threading.Lock()
_PROPOSALS_BY_ID: dict[str, dict[str, Any]] = {}
_PENDING_BY_SESSION: dict[str, str] = {}
_PROPOSAL_TTL_SECONDS = 30 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _prune_expired(now: datetime | None = None) -> None:
    now = now or _utc_now()
    expired_ids = [
        proposal_id
        for proposal_id, payload in _PROPOSALS_BY_ID.items()
        if payload.get("expires_at") and payload["expires_at"] <= now
    ]
    for proposal_id in expired_ids:
        payload = _PROPOSALS_BY_ID.pop(proposal_id, None)
        if not payload:
            continue
        session_id = payload.get("session_id")
        if session_id and _PENDING_BY_SESSION.get(session_id) == proposal_id:
            _PENDING_BY_SESSION.pop(session_id, None)


def register_persistence_proposal(
    *,
    session_id: str,
    run_id: str,
    tool_results: list[dict[str, Any]],
    extra_sources: list[dict[str, Any]] | None = None,
    reason: str = "kg_faiss_post_process",
) -> dict[str, Any]:
    now = _utc_now()
    expires_at = now + timedelta(seconds=_PROPOSAL_TTL_SECONDS)
    proposal_id = str(uuid.uuid4())
    payload = {
        "proposal_id": proposal_id,
        "session_id": session_id,
        "run_id": run_id,
        "reason": reason,
        "status": "pending",
        "created_at": now,
        "expires_at": expires_at,
        "tool_results": deepcopy(tool_results),
        "extra_sources": deepcopy(extra_sources or []),
    }

    with _LOCK:
        _prune_expired(now)
        old_id = _PENDING_BY_SESSION.get(session_id)
        if old_id:
            _PROPOSALS_BY_ID.pop(old_id, None)
        _PROPOSALS_BY_ID[proposal_id] = payload
        _PENDING_BY_SESSION[session_id] = proposal_id

    return {
        "proposal_id": proposal_id,
        "status": "pending",
        "reason": reason,
        "tool_result_count": len(tool_results),
        "source_count": len(extra_sources or []),
        "expires_at": expires_at.isoformat(),
    }


def confirm_persistence_proposal(proposal_id: str, decision: str) -> dict[str, Any]:
    decision_norm = str(decision or "").strip().lower()
    if decision_norm in {"confirm", "yes", "approve", "approved"}:
        target_status = "confirmed"
    elif decision_norm in {"discard", "deny", "denied", "reject", "rejected", "no"}:
        target_status = "discarded"
    else:
        return {
            "success": False,
            "proposal_id": proposal_id,
            "status": "invalid_decision",
            "error": "decision must be confirm|discard",
        }

    with _LOCK:
        _prune_expired()
        payload = _PROPOSALS_BY_ID.get(proposal_id)
        if payload is None:
            return {
                "success": False,
                "proposal_id": proposal_id,
                "status": "not_found",
                "error": "proposal not found or expired",
            }

        payload["status"] = target_status
        payload["updated_at"] = _utc_now()

    return {
        "success": True,
        "proposal_id": proposal_id,
        "status": target_status,
    }


def consume_confirmed_proposal(session_id: str) -> dict[str, Any] | None:
    with _LOCK:
        _prune_expired()
        proposal_id = _PENDING_BY_SESSION.get(session_id)
        if not proposal_id:
            return None

        payload = _PROPOSALS_BY_ID.get(proposal_id)
        if payload is None:
            _PENDING_BY_SESSION.pop(session_id, None)
            return None

        if payload.get("status") != "confirmed":
            return None

        _PENDING_BY_SESSION.pop(session_id, None)
        _PROPOSALS_BY_ID.pop(proposal_id, None)
        return deepcopy(payload)


def has_persistence_payload(tool_results: list[dict[str, Any]], extra_sources: list[dict[str, Any]] | None) -> bool:
    return bool(tool_results or extra_sources)


def _reset_for_tests() -> None:
    with _LOCK:
        _PROPOSALS_BY_ID.clear()
        _PENDING_BY_SESSION.clear()
