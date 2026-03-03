"""Phase 1 — Tests for schemas/tool_contracts.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from schemas.tool_contracts import (
    SearchHit,
    SourceRef,
    SourceRef,
    ToolResult,
    ToolTiming,
    WebSearchResult,
    ToolResultEnvelope,
    compute_completeness,
    to_envelope,
)


# ---------------------------------------------------------------------------
# ToolTiming
# ---------------------------------------------------------------------------

class TestToolTiming:
    def test_auto_computes_duration(self):
        t0 = datetime(2025, 1, 1, 0, 0, 0)
        t1 = t0 + timedelta(milliseconds=500)
        timing = ToolTiming(tool_name="test", started_at=t0, ended_at=t1)
        assert timing.duration_ms == 500.0

    def test_zero_duration_when_same_timestamps(self):
        t = datetime(2025, 6, 15, 12, 0, 0)
        timing = ToolTiming(tool_name="noop", started_at=t, ended_at=t)
        assert timing.duration_ms == 0.0

    def test_negative_duration_raises(self):
        t0 = datetime(2025, 1, 1, 0, 0, 1)
        t1 = datetime(2025, 1, 1, 0, 0, 0)
        with pytest.raises(ValidationError, match="before started_at"):
            ToolTiming(tool_name="bad", started_at=t0, ended_at=t1)

    def test_repr(self):
        t = datetime(2025, 1, 1)
        timing = ToolTiming(tool_name="x", started_at=t, ended_at=t)
        assert "ToolTiming" in repr(timing)
        assert "x" in repr(timing)

    def test_large_duration(self):
        t0 = datetime(2025, 1, 1)
        t1 = t0 + timedelta(hours=1)
        timing = ToolTiming(tool_name="long", started_at=t0, ended_at=t1)
        assert timing.duration_ms == 3_600_000.0


# ---------------------------------------------------------------------------
# SearchHit / WebSearchResult
# ---------------------------------------------------------------------------

class TestSearchHit:
    def test_valid_hit(self):
        hit = SearchHit(
            title="Example",
            url="https://example.com",
            snippet="A test snippet.",
            score=0.95,
        )
        assert hit.title == "Example"
        assert str(hit.url) == "https://example.com/"

    def test_invalid_url_rejects(self):
        with pytest.raises(ValidationError):
            SearchHit(title="X", url="not-a-url", snippet="")

    def test_score_optional(self):
        hit = SearchHit(title="T", url="https://a.com", snippet="S")
        assert hit.score is None


class TestWebSearchResult:
    def test_basic(self):
        result = WebSearchResult(query="test", hits=[], provider="tavily")
        assert result.provider == "tavily"
        assert result.hits == []


# ---------------------------------------------------------------------------
# SourceRef
# ---------------------------------------------------------------------------

class TestSourceRef:
    def test_valid(self):
        ref = SourceRef(
            url="https://sec.gov/file",
            title="Filing",
            fetched_at=datetime(2025, 1, 1),
        )
        assert "sec.gov" in str(ref.url)


# ---------------------------------------------------------------------------
# ToolResult[T]
# ---------------------------------------------------------------------------

class TestToolResult:
    def _make_timing(self) -> ToolTiming:
        t = datetime(2025, 1, 1)
        return ToolTiming(tool_name="test", started_at=t, ended_at=t)

    def test_string_data(self):
        r: ToolResult[str] = ToolResult(
            data="hello",
            timing=self._make_timing(),
        )
        assert r.data == "hello"
        assert r.success is True
        assert r.error is None

    def test_dict_data(self):
        r: ToolResult[dict] = ToolResult(
            data={"key": "val"},
            timing=self._make_timing(),
        )
        assert r.data["key"] == "val"

    def test_list_data(self):
        r: ToolResult[list[int]] = ToolResult(
            data=[1, 2, 3],
            timing=self._make_timing(),
        )
        assert len(r.data) == 3

    def test_failure_state(self):
        r: ToolResult[None] = ToolResult(
            data=None,
            timing=self._make_timing(),
            success=False,
            error="boom",
        )
        assert r.success is False
        assert r.error == "boom"

    def test_serialisation_roundtrip(self):
        t = datetime(2025, 6, 1)
        timing = ToolTiming(tool_name="rt", started_at=t, ended_at=t)
        original: ToolResult[str] = ToolResult(
            data="payload",
            sources=[
                SourceRef(url="https://x.com", title="X", fetched_at=t),
            ],
            timing=timing,
        )
        json_str = original.model_dump_json()
        restored = ToolResult[str].model_validate_json(json_str)
        assert restored.data == original.data
        assert len(restored.sources) == 1

    def test_repr(self):
        r: ToolResult[str] = ToolResult(
            data="x",
            timing=self._make_timing(),
            success=False,
            error="fail",
        )
        assert "ToolResult" in repr(r)
        assert "fail" in repr(r)


class TestToolResultEnvelopeAndCompleteness:
    def _make_timing(self) -> ToolTiming:
        t = datetime(2025, 1, 1, tzinfo=timezone.utc)
        return ToolTiming(tool_name="profile", started_at=t, ended_at=t)

    def test_to_envelope_basic_fields_and_provenance(self):
        fetched_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        timing = self._make_timing()
        result: ToolResult[dict] = ToolResult(
            data={"name": "Apple Inc.", "sector": "Technology", "market_cap": 123.0, "description": "Desc"},
            sources=[
                SourceRef(
                    url="https://example.com/aapl",
                    title="FMP: AAPL",
                    fetched_at=fetched_at,
                )
            ],
            timing=timing,
        )

        envelope: ToolResultEnvelope = to_envelope(
            result,
            identifier="AAPL",
            source_label=None,
            as_of="2025-05-31",
            warnings=["w1"],
            completeness=0.75,
        )

        assert envelope.data["name"] == "Apple Inc."
        assert envelope.provenance.identifier == "AAPL"
        assert envelope.provenance.source == "fmp"
        assert envelope.provenance.as_of == "2025-05-31"
        assert envelope.provenance.retrieved_at == fetched_at.isoformat()
        assert envelope.quality.completeness == 0.75
        assert envelope.quality.warnings == ["w1"]
        assert len(envelope.sources) == 1
        assert str(envelope.sources[0].url) == "https://example.com/aapl"

    def test_compute_completeness_for_company_profile(self):
        data = {
            "name": "Apple Inc.",
            "sector": "Technology",
            "market_cap": 123.0,
            "description": None,
        }
        score, warnings = compute_completeness("get_company_profile", data)
        # 3/4 required fields are non-null
        assert score == 0.75
        assert warnings and "Missing fields" in warnings[0]
