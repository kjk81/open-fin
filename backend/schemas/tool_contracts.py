"""Tool contract schemas: SourceRef, ToolTiming, and generic ToolResult[T]."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, HttpUrl, model_validator

T = TypeVar("T")


class SourceRef(BaseModel):
    """Provenance record for a single fetched resource."""

    url: HttpUrl
    title: str
    fetched_at: datetime


class ToolTiming(BaseModel):
    """Execution timing for a single tool invocation."""

    tool_name: str
    started_at: datetime
    ended_at: datetime
    duration_ms: float = 0.0

    @model_validator(mode="after")
    def _compute_duration(self) -> "ToolTiming":
        delta = (self.ended_at - self.started_at).total_seconds() * 1000
        self.duration_ms = round(delta, 3)
        return self


class ToolResult(BaseModel, Generic[T]):
    """Generic wrapper for all tool outputs."""

    data: T
    sources: list[SourceRef] = []
    timing: ToolTiming
    success: bool = True
    error: str | None = None
