from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import AgentRun, ResearchLibrary
from agent.memory_consent import confirm_persistence_proposal

router = APIRouter()

_DECISION_VALUES = {"confirm", "discard"}


class ConfirmProposalRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=64)
    decision: str = Field(..., pattern="^(confirm|discard)$")


class SourceRef(BaseModel):
    url: str
    title: str


class SaveToLibraryRequest(BaseModel):
    run_id: str = Field(..., min_length=1, max_length=36)
    category: str = Field(default="chat_response", max_length=100)
    content: str = Field(..., min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


@router.post("/memory/confirm")
def confirm_memory(payload: ConfirmProposalRequest):
    """Confirm or discard a pending memory persistence proposal."""
    result = confirm_persistence_proposal(payload.proposal_id, payload.decision)
    if not result.get("success"):
        err_status = result.get("status", "")
        if err_status == "not_found":
            raise HTTPException(status_code=404, detail="Proposal not found or expired")
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid decision"))
    return result


@router.post("/memory/save-to-library", status_code=status.HTTP_201_CREATED)
def save_to_library(payload: SaveToLibraryRequest, db: Session = Depends(get_db)):
    """Persist a chat response artifact to the ResearchLibrary."""
    # Verify the run exists
    run = db.query(AgentRun).filter(AgentRun.id == payload.run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")

    now = datetime.now(timezone.utc)
    entry = ResearchLibrary(
        run_id=payload.run_id,
        category=payload.category,
        content_json=json.dumps({"text": payload.content}),
        citations_json=json.dumps([{"url": s.url, "title": s.title} for s in payload.sources]),
        tags_json=json.dumps(payload.tags),
        created_at=now,
        updated_at=now,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return {
        "id": entry.id,
        "created_at": entry.created_at.isoformat(),
    }
