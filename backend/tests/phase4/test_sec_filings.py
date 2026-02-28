"""Phase 4 — Tests for tools/sec_filings.py."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.finance import FilingExtract, FilingPlan, FilingSection, FilingsResult
from schemas.tool_contracts import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_submissions_json(
    name: str = "Apple Inc",
    forms: list | None = None,
    accessions: list | None = None,
    dates: list | None = None,
):
    forms = forms or ["10-K", "10-Q", "8-K"]
    accessions = accessions or ["0001-24-000001", "0001-24-000002", "0001-24-000003"]
    dates = dates or ["2024-10-30", "2024-07-30", "2024-04-30"]
    return {
        "name": name,
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": accessions,
                "filingDate": dates,
            }
        },
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestNormalizeFormTypes:
    def test_adds_amendment(self):
        from tools.sec_filings import _normalize_form_types
        result = _normalize_form_types(["10-K"])
        assert "10-K" in result
        assert "10-K/A" in result

    def test_empty_stripped(self):
        from tools.sec_filings import _normalize_form_types
        result = _normalize_form_types(["", " "])
        assert result == set()


class TestExtractSectionMarkdown:
    def test_finds_risk_factors(self):
        from tools.sec_filings import _extract_section_markdown
        md = """## Item 1A Risk Factors

The company faces significant risks including...

## Item 2 Properties

The company owns several buildings...
"""
        section = _extract_section_markdown(md, "risk factors", 5000)
        assert "significant risks" in section.content_md
        assert "Properties" not in section.content_md

    def test_section_not_found(self):
        from tools.sec_filings import _extract_section_markdown
        md = "## Introduction\n\nSome unrelated text."
        section = _extract_section_markdown(md, "risk factors", 5000)
        assert "not found" in section.content_md.lower()

    def test_truncation(self):
        from tools.sec_filings import _extract_section_markdown
        md = "## Item 1A Risk Factors\n\n" + "x" * 10000
        section = _extract_section_markdown(md, "risk factors", 100)
        assert len(section.content_md) <= 200  # 100 + truncation marker
        assert "truncated" in section.content_md.lower()


# ---------------------------------------------------------------------------
# get_filings_metadata
# ---------------------------------------------------------------------------

class TestGetFilingsMetadata:
    async def test_success(self):
        mock_edgar = AsyncMock()
        mock_edgar.ticker_to_cik = AsyncMock(return_value="0000320193")
        mock_edgar.get = AsyncMock(return_value=_make_submissions_json())
        mock_edgar.__aenter__ = AsyncMock(return_value=mock_edgar)
        mock_edgar.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.sec_filings.EdgarClient", return_value=mock_edgar):
            from tools.sec_filings import get_filings_metadata
            result = await get_filings_metadata("AAPL", form_types=["10-K"], limit=2)

        assert result.success is True
        assert len(result.data) >= 1
        assert result.data[0].company_name == "Apple Inc"

    async def test_unknown_ticker(self):
        mock_edgar = AsyncMock()
        mock_edgar.ticker_to_cik = AsyncMock(return_value=None)
        mock_edgar.__aenter__ = AsyncMock(return_value=mock_edgar)
        mock_edgar.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.sec_filings.EdgarClient", return_value=mock_edgar):
            from tools.sec_filings import get_filings_metadata
            result = await get_filings_metadata("ZZZZZ")

        assert result.success is False
        assert "CIK" in (result.error or "")


# ---------------------------------------------------------------------------
# extract_filing_sections
# ---------------------------------------------------------------------------

class TestExtractFilingSections:
    async def test_empty_sections_returns_empty(self):
        from tools.sec_filings import extract_filing_sections
        result = await extract_filing_sections(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/0001-24-000001-index.htm",
            sections=[],
        )
        assert result == []

    async def test_non_sec_url_rejected(self):
        from tools.sec_filings import extract_filing_sections
        result = await extract_filing_sections(
            "https://evil.com/some-filing.htm",
            sections=["risk factors"],
        )
        assert len(result) == 1
        assert "sec.gov" in result[0].content_md.lower()

    async def test_max_chars_capped(self):
        """max_chars_per_section beyond _MAX_SECTION_CHARS should be capped."""
        from tools.sec_filings import _MAX_SECTION_CHARS

        mock_index_html = '<html><body><a href="filing.htm">Document</a></body></html>'
        mock_doc_html = "<html><body><h2>Item 1A Risk Factors</h2><p>" + "x" * 100000 + "</p></body></html>"

        mock_response_index = MagicMock()
        mock_response_index.text = mock_index_html
        mock_response_doc = MagicMock()
        mock_response_doc.text = mock_doc_html

        mock_http = AsyncMock()
        call_count = [0]

        async def mock_get(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_response_index
            return mock_response_doc

        mock_http.get = mock_get
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.sec_filings.HttpClient", return_value=mock_http), \
             patch("tools.sec_filings.validate_url_no_resolve"):
            from tools.sec_filings import extract_filing_sections
            result = await extract_filing_sections(
                "https://www.sec.gov/Archives/test/index.htm",
                sections=["risk factors"],
                max_chars_per_section=999_999,  # exceeds cap
            )

        assert len(result) == 1
        # Content should be capped at _MAX_SECTION_CHARS + truncation marker
        assert result[0].char_count <= _MAX_SECTION_CHARS + 50


# ---------------------------------------------------------------------------
# read_filings (end-to-end with mocked LLM + EDGAR)
# ---------------------------------------------------------------------------

class TestReadFilings:
    async def test_llm_planning_failure(self):
        """If the LLM fails, read_filings should return success=False."""
        mock_llm = MagicMock()
        mock_llm.ainvoke_structured = AsyncMock(side_effect=RuntimeError("LLM down"))

        with patch("tools.sec_filings.get_llm", return_value=mock_llm):
            from tools.sec_filings import read_filings
            result = await read_filings("Show me Apple's risk factors")

        assert result.success is False
        assert "LLM down" in (result.error or "")
