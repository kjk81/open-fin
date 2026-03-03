from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import TickerNote

router = APIRouter()
TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")


class TickerNoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("Content cannot be empty")
        return content


def _ensure_tables() -> None:
    Base.metadata.create_all(bind=engine)


def _normalize_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(status_code=422, detail="Invalid ticker format")
    return ticker


def _serialize_note(note: TickerNote) -> dict:
    return {
        "id": note.id,
        "ticker": note.ticker,
        "content": note.content,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


@router.get("/ticker/{symbol}/notes")
def list_ticker_notes(
    symbol: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    ticker = _normalize_ticker(symbol)

    query = db.query(TickerNote).filter(TickerNote.ticker == ticker)
    total = query.count()
    items = (
        query.order_by(TickerNote.created_at.desc(), TickerNote.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "items": [_serialize_note(note) for note in items],
    }


@router.post("/ticker/{symbol}/notes", status_code=status.HTTP_201_CREATED)
def create_ticker_note(
    symbol: str,
    payload: TickerNoteCreate,
    db: Session = Depends(get_db),
):
    _ensure_tables()
    ticker = _normalize_ticker(symbol)
    now = datetime.now(timezone.utc)

    note = TickerNote(
        ticker=ticker,
        content=payload.content,
        created_at=now,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _serialize_note(note)


@router.delete("/ticker/{symbol}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticker_note(
    symbol: str,
    note_id: int,
    db: Session = Depends(get_db),
):
    _ensure_tables()
    ticker = _normalize_ticker(symbol)

    note = (
        db.query(TickerNote)
        .filter(TickerNote.id == note_id, TickerNote.ticker == ticker)
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    db.delete(note)
    db.commit()
