"""Tool contract schemas: SourceRef, ToolTiming, and generic ToolResult[T]."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, HttpUrl, model_validator

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Web search schemas
# ---------------------------------------------------------------------------

class SearchHit(BaseModel):
    """A single search result from a web search provider."""

    title: str
    url: HttpUrl
    snippet: str
    score: float | None = None


class WebSearchResult(BaseModel):
    """Normalised output from any web search provider."""

    query: str
    hits: list[SearchHit]
    provider: str


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
        if delta < 0:
            raise ValueError(
                f"ended_at ({self.ended_at}) is before started_at ({self.started_at})"
            )
        self.duration_ms = round(delta, 3)
        return self

    def __repr__(self) -> str:
        return (
            f"ToolTiming(tool={self.tool_name!r}, "
            f"duration_ms={self.duration_ms})"
        )


class ToolResult(BaseModel, Generic[T]):
    """Generic wrapper for all tool outputs."""

    data: T
    sources: list[SourceRef] = []
    timing: ToolTiming
    success: bool = True
    error: str | None = None

    def __repr__(self) -> str:
        return (
            f"ToolResult(success={self.success}, "
            f"tool={self.timing.tool_name!r}, "
            f"error={self.error!r})"
        )
