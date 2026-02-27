from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.llm import settings_payload, persist_settings

router = APIRouter()


class LLMSettingsUpdateRequest(BaseModel):
    mode: str = Field(..., min_length=1, max_length=20)
    fallback_order: list[str] = Field(..., min_length=1)


@router.get("/llm/settings")
def get_llm_settings():
    return settings_payload()


@router.put("/llm/settings")
def update_llm_settings(request: LLMSettingsUpdateRequest):
    try:
        return persist_settings(mode=request.mode, fallback_order=request.fallback_order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
