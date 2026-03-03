"""Tool contract schemas: SourceRef, ToolTiming, ToolResult[T], and ToolResultEnvelope."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

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


# ---------------------------------------------------------------------------
# Tool Result Envelope — standardized output for deterministic downstream use
# ---------------------------------------------------------------------------

class Provenance(BaseModel):
    """Structured provenance metadata for a tool result."""

    source: str       # e.g. "fmp", "yfinance", "sec.gov", "tavily"
    retrieved_at: str  # ISO 8601 timestamp
    as_of: str         # Market data date or "real-time"
    identifier: str    # Primary entity: ticker symbol, query, or URL


class Quality(BaseModel):
    """Data quality signals for a tool result."""

    warnings: list[str] = []
    completeness: float = 1.0   # 0.0 (empty/failed) → 1.0 (all fields present)


class RawRef(BaseModel):
    """Pointer to stored raw/unprocessed data (cache or file reference)."""

    storage_type: str  # "cache_key" | "file_path"
    ref: str           # The actual reference value


class ToolResultEnvelope(BaseModel):
    """Standardized envelope emitted by every graph.py tool wrapper.

    Superset of ToolResult — preserves top-level ``success``, ``sources``,
    and ``timing`` so existing SSE parsing and KG post-processing keep working
    without modification.
    """

    data: Any                   # Serialized payload (dict or list)
    provenance: Provenance
    quality: Quality = Quality()
    timing: ToolTiming
    sources: list[SourceRef] = []
    success: bool = True
    error: str | None = None
    raw_ref: RawRef | None = None


# ---------------------------------------------------------------------------
# Completeness heuristics
# ---------------------------------------------------------------------------

def compute_completeness(tool_name: str, data: Any) -> tuple[float, list[str]]:
    """Return (completeness_score, warnings) using tool-specific field checks."""
    warnings: list[str] = []

    if tool_name == "get_company_profile":
        if isinstance(data, dict):
            required = {"name", "sector", "market_cap", "description"}
            present = {k for k in required if data.get(k) is not None}
            missing = required - present
            if missing:
                warnings.append(f"Missing fields: {', '.join(sorted(missing))}")
            return len(present) / len(required), warnings

    elif tool_name == "get_technical_snapshot":
        if isinstance(data, dict):
            required = {"sma_20", "sma_50", "sma_200", "rsi_14", "atr_14"}
            present = {k for k in required if data.get(k) is not None}
            if data.get("rsi_14") is None:
                warnings.append("RSI unavailable (insufficient price history)")
            return len(present) / len(required), warnings

    elif tool_name == "get_financial_statements":
        if isinstance(data, list):
            if not data:
                return 0.0, ["No financial statements returned"]
            return min(len(data) / 4.0, 1.0), warnings

    elif tool_name == "get_balance_sheet":
        if isinstance(data, list):
            if not data:
                return 0.0, ["No balance sheet data returned"]
            return min(len(data) / 4.0, 1.0), warnings

    elif tool_name == "get_ohlcv":
        if isinstance(data, list):
            if not data:
                return 0.0, ["No OHLCV bars returned"]
            return 1.0, warnings

    elif tool_name in ("search_web", "get_social_sentiment"):
        if isinstance(data, dict):
            hits = data.get("hits", [])
            if not hits:
                return 0.0, ["No results found"]
            return min(len(hits) / 5.0, 1.0), warnings

    elif tool_name == "read_filings":
        if isinstance(data, dict):
            filings = data.get("filings", [])
            if not filings:
                return 0.0, ["No filings retrieved"]
            return 1.0, warnings

    elif tool_name == "extract_filing_sections":
        if isinstance(data, list):
            if not data:
                return 0.0, ["No filing sections extracted"]
            return 1.0, warnings

    elif tool_name == "screen_stocks":
        if isinstance(data, list):
            if not data:
                return 0.0, ["Screen returned no hits"]
            return 1.0, warnings

    # Default
    return (1.0 if data else 0.0), warnings


# ---------------------------------------------------------------------------
# Conversion helper
# ---------------------------------------------------------------------------

def to_envelope(
    result: "ToolResult[Any]",
    *,
    identifier: str,
    source_label: str | None = None,
    as_of: str | None = None,
    warnings: list[str] | None = None,
    completeness: float | None = None,
    raw_ref: RawRef | None = None,
) -> ToolResultEnvelope:
    """Convert a ToolResult into a ToolResultEnvelope."""
    # Infer source label from first SourceRef title when not provided
    if source_label is None and result.sources:
        source_label = result.sources[0].title.split(":")[0].strip().lower()
    source_label = source_label or "unknown"

    retrieved_at = (
        result.sources[0].fetched_at.isoformat()
        if result.sources
        else result.timing.started_at.isoformat()
    )

    # Serialize Pydantic models inside data
    raw_data = result.data
    if hasattr(raw_data, "model_dump"):
        raw_data = raw_data.model_dump()
    elif (
        isinstance(raw_data, list)
        and raw_data
        and hasattr(raw_data[0], "model_dump")
    ):
        raw_data = [item.model_dump() for item in raw_data]

    return ToolResultEnvelope(
        data=raw_data,
        provenance=Provenance(
            source=source_label,
            retrieved_at=retrieved_at,
            as_of=as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            identifier=identifier,
        ),
        quality=Quality(
            warnings=warnings or [],
            completeness=completeness if completeness is not None else (1.0 if result.success else 0.0),
        ),
        timing=result.timing,
        sources=result.sources,
        success=result.success,
        error=result.error,
        raw_ref=raw_ref,
    )
