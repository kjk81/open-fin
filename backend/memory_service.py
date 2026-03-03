from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    EpisodicSummary,
    PortfolioSnapshot,
    ResearchLibrary,
    UserPreferences,
)

_REQUIRED_CITATION_KEYS = {"title", "url", "tool", "accessed_at"}


class MemoryValidationError(ValueError):
    """Raised when typed memory payloads are invalid."""


def _coerce_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _validate_citations(citations: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(citations, list):
        raise MemoryValidationError("citations must be a list of SourceRef objects")

    normalized: list[dict[str, str]] = []
    for idx, item in enumerate(citations):
        if not isinstance(item, dict):
            raise MemoryValidationError(f"citation at index {idx} must be an object")

        missing = _REQUIRED_CITATION_KEYS.difference(item.keys())
        if missing:
            raise MemoryValidationError(
                f"citation at index {idx} missing required keys: {sorted(missing)}"
            )

        citation: dict[str, str] = {}
        for key in _REQUIRED_CITATION_KEYS:
            val = item.get(key)
            if not isinstance(val, str) or not val.strip():
                raise MemoryValidationError(
                    f"citation key '{key}' at index {idx} must be a non-empty string"
                )
            citation[key] = val.strip()

        try:
            _coerce_datetime(citation["accessed_at"])
        except Exception as exc:
            raise MemoryValidationError(
                f"citation key 'accessed_at' at index {idx} must be ISO-8601 datetime"
            ) from exc

        normalized.append(citation)

    return normalized


def _encode_json(payload: Any, field_name: str) -> str:
    try:
        return json.dumps(payload)
    except Exception as exc:
        raise MemoryValidationError(f"{field_name} must be JSON-serializable") from exc


def _decode_json(payload_json: str, fallback: Any) -> Any:
    try:
        return json.loads(payload_json)
    except Exception:
        return fallback


def _persist(db: Session | None, callback):
    if db is not None:
        return callback(db)

    owned = SessionLocal()
    try:
        result = callback(owned)
        owned.commit()
        return result
    except Exception:
        owned.rollback()
        raise
    finally:
        owned.close()


def _serialize_record(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "run_id": row.run_id,
        "category": row.category,
        "content": _decode_json(row.content_json, {}),
        "citations": _decode_json(row.citations_json, []),
        "tags": _decode_json(row.tags_json, []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if hasattr(row, "confidence"):
        data["confidence"] = getattr(row, "confidence")
    if hasattr(row, "expires_at"):
        expires_at = getattr(row, "expires_at")
        data["expires_at"] = expires_at.isoformat() if expires_at else None
    return data


def upsert_user_preference(
    *,
    run_id: str,
    category: str,
    content: dict[str, Any],
    citations: list[dict[str, Any]],
    tags: list[str] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    normalized_citations = _validate_citations(citations)
    normalized_category = str(category).strip().lower()
    if not normalized_category:
        raise MemoryValidationError("category must be a non-empty string")

    def _op(session: Session) -> dict[str, Any]:
        row = session.query(UserPreferences).filter(UserPreferences.category == normalized_category).first()
        now = datetime.utcnow()
        if row is None:
            row = UserPreferences(
                run_id=run_id,
                category=normalized_category,
                content_json=_encode_json(content, "content"),
                citations_json=_encode_json(normalized_citations, "citations"),
                tags_json=_encode_json(tags or [], "tags"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.run_id = run_id
            row.content_json = _encode_json(content, "content")
            row.citations_json = _encode_json(normalized_citations, "citations")
            row.tags_json = _encode_json(tags or [], "tags")
            row.updated_at = now

        session.flush()
        return _serialize_record(row)

    return _persist(db, _op)


def get_user_preference(
    category: str,
    *,
    db: Session | None = None,
) -> dict[str, Any] | None:
    normalized_category = str(category).strip().lower()
    if not normalized_category:
        raise MemoryValidationError("category must be a non-empty string")

    def _op(session: Session) -> dict[str, Any] | None:
        row = session.query(UserPreferences).filter(UserPreferences.category == normalized_category).first()
        return _serialize_record(row) if row else None

    return _persist(db, _op)


def create_portfolio_snapshot(
    *,
    run_id: str,
    category: str,
    content: dict[str, Any],
    citations: list[dict[str, Any]],
    tags: list[str] | None = None,
    confidence: float = 0.0,
    db: Session | None = None,
) -> dict[str, Any]:
    normalized_citations = _validate_citations(citations)
    normalized_category = str(category).strip().lower()
    if not normalized_category:
        raise MemoryValidationError("category must be a non-empty string")

    def _op(session: Session) -> dict[str, Any]:
        now = datetime.utcnow()
        row = PortfolioSnapshot(
            run_id=run_id,
            category=normalized_category,
            content_json=_encode_json(content, "content"),
            citations_json=_encode_json(normalized_citations, "citations"),
            tags_json=_encode_json(tags or [], "tags"),
            confidence=float(confidence),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _serialize_record(row)

    return _persist(db, _op)


def get_portfolio_snapshot_by_run_id(
    run_id: str,
    *,
    db: Session | None = None,
) -> dict[str, Any] | None:
    def _op(session: Session) -> dict[str, Any] | None:
        row = session.query(PortfolioSnapshot).filter(PortfolioSnapshot.run_id == run_id).first()
        return _serialize_record(row) if row else None

    return _persist(db, _op)


def create_episodic_summary(
    *,
    run_id: str,
    category: str,
    content: dict[str, Any],
    citations: list[dict[str, Any]],
    tags: list[str] | None = None,
    expires_at: datetime | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    normalized_citations = _validate_citations(citations)
    normalized_category = str(category).strip().lower()
    if not normalized_category:
        raise MemoryValidationError("category must be a non-empty string")

    def _op(session: Session) -> dict[str, Any]:
        now = datetime.utcnow()
        row = EpisodicSummary(
            run_id=run_id,
            category=normalized_category,
            content_json=_encode_json(content, "content"),
            citations_json=_encode_json(normalized_citations, "citations"),
            tags_json=_encode_json(tags or [], "tags"),
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _serialize_record(row)

    return _persist(db, _op)


def get_episodic_summary_by_run_id(
    run_id: str,
    *,
    db: Session | None = None,
) -> dict[str, Any] | None:
    def _op(session: Session) -> dict[str, Any] | None:
        row = session.query(EpisodicSummary).filter(EpisodicSummary.run_id == run_id).first()
        return _serialize_record(row) if row else None

    return _persist(db, _op)


def create_research_library_entry(
    *,
    run_id: str,
    category: str,
    content: dict[str, Any],
    citations: list[dict[str, Any]],
    tags: list[str] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    normalized_citations = _validate_citations(citations)
    normalized_category = str(category).strip().lower()
    if not normalized_category:
        raise MemoryValidationError("category must be a non-empty string")

    def _op(session: Session) -> dict[str, Any]:
        now = datetime.utcnow()
        row = ResearchLibrary(
            run_id=run_id,
            category=normalized_category,
            content_json=_encode_json(content, "content"),
            citations_json=_encode_json(normalized_citations, "citations"),
            tags_json=_encode_json(tags or [], "tags"),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _serialize_record(row)

    return _persist(db, _op)


def list_research_library_by_run_id(
    run_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 500))
    bounded_offset = max(0, int(offset))

    def _op(session: Session) -> list[dict[str, Any]]:
        rows = (
            session.query(ResearchLibrary)
            .filter(ResearchLibrary.run_id == run_id)
            .order_by(ResearchLibrary.id.asc())
            .offset(bounded_offset)
            .limit(bounded_limit)
            .all()
        )
        return [_serialize_record(row) for row in rows]

    return _persist(db, _op)
