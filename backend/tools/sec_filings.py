"""SEC filings meta-tool with planning + targeted extraction.

This module implements a two-step generative process:
1) Plan extraction from natural-language query via structured LLM output.
2) Deterministic filing metadata retrieval and section extraction.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup
from langchain_core.messages import HumanMessage, SystemMessage

from agent.llm import get_llm
from clients.edgar import EdgarClient
from clients.http_base import HttpClient
from clients.url_guard import validate_url_no_resolve
from schemas.finance import FilingExtract, FilingPlan, FilingSection, FilingsResult
from schemas.tool_contracts import SourceRef, ToolResult
from tools._utils import build_timing, html_to_markdown, now_utc

logger = logging.getLogger(__name__)

_SEC_USER_AGENT = "OpenFin/1.0 (financial-ai-copilot; contact@openfin.local)"
_DEFAULT_SECTION_CHARS = 8_000
_MAX_SECTION_CHARS = 50_000  # hard cap to prevent abuse

_SECTION_PATTERNS: dict[str, str] = {
    "risk factors": r"(?:item\s*1a\b|risk\s+factors)",
    "management discussion": r"(?:item\s*7\b|management\s+discussion(?:\s+and\s+analysis)?|md&a)",
    "management's discussion and analysis": r"(?:item\s*7\b|management.?s\s+discussion(?:\s+and\s+analysis)?|md&a)",
    "business": r"(?:item\s*1\b|\bbusiness\b)",
    "legal proceedings": r"(?:item\s*3\b|legal\s+proceedings)",
    "financial statements": r"(?:item\s*8\b|financial\s+statements)",
    "controls and procedures": r"(?:item\s*9a\b|controls\s+and\s+procedures)",
    "quantitative and qualitative disclosures": r"(?:item\s*7a\b|quantitative\s+and\s+qualitative\s+disclosures)",
}

_GENERIC_SECTION_BOUNDARY_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:part\s+[ivx]+\b|item\s+\d+[a-z]?\b)",
    flags=re.IGNORECASE,
)


def _normalize_form_types(form_types: list[str]) -> set[str]:
    normalized: set[str] = set()
    for form in form_types:
        token = str(form).upper().replace(" ", "")
        if not token:
            continue
        normalized.add(token)
        if not token.endswith("/A"):
            normalized.add(f"{token}/A")
    return normalized


def _safe_filing_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except Exception:
        return date.today()


def _resolve_section_regex(section_name: str) -> re.Pattern[str]:
    key = section_name.lower().strip()
    pattern = _SECTION_PATTERNS.get(key)
    if pattern is None:
        pattern = re.escape(section_name)
    return re.compile(pattern, flags=re.IGNORECASE)


def _extract_section_markdown(
    markdown: str,
    section_name: str,
    max_chars_per_section: int,
) -> FilingSection:
    lines = markdown.splitlines()
    matcher = _resolve_section_regex(section_name)

    start_index: int | None = None
    for idx, line in enumerate(lines):
        if matcher.search(line):
            start_index = idx
            break

    if start_index is None:
        not_found = "Section not found in filing."
        return FilingSection(section_name=section_name, content_md=not_found, char_count=len(not_found))

    end_index = len(lines)
    for idx in range(start_index + 1, len(lines)):
        candidate = lines[idx]
        if _GENERIC_SECTION_BOUNDARY_RE.search(candidate):
            end_index = idx
            break

    content = "\n".join(lines[start_index:end_index]).strip()
    if not content:
        content = "Section heading found but section body was empty after parsing."

    if len(content) > max_chars_per_section:
        content = content[:max_chars_per_section].rstrip() + "\n\n...[truncated]"

    return FilingSection(section_name=section_name, content_md=content, char_count=len(content))


async def get_filings_metadata(
    ticker: str,
    form_types: list[str] | None = None,
    limit: int = 3,
) -> ToolResult[list[FilingExtract]]:
    """Fetch recent filing metadata (URL + accession number) for a ticker."""
    started_at = now_utc()
    tool_name = "get_filings_metadata"

    try:
        selected_forms = _normalize_form_types(form_types or ["10-K", "10-Q"])

        async with EdgarClient() as edgar:
            cik = await edgar.ticker_to_cik(ticker)
            if cik is None:
                return ToolResult(
                    data=[],
                    timing=build_timing(tool_name, started_at),
                    success=False,
                    error=f"Could not resolve ticker {ticker!r} to a SEC CIK.",
                )
            submissions = await edgar.get(f"/submissions/CIK{cik}.json")

        company_name = submissions.get("name", ticker.upper())
        recent = submissions.get("filings", {}).get("recent", {})

        forms: list[str] = recent.get("form", [])
        accession_numbers: list[str] = recent.get("accessionNumber", [])
        filing_dates: list[str] = recent.get("filingDate", [])

        filings: list[FilingExtract] = []
        cik_int = str(int(cik))

        for idx, form in enumerate(forms):
            normalized = str(form).upper().replace(" ", "")
            if normalized not in selected_forms:
                continue

            accession_number = accession_numbers[idx] if idx < len(accession_numbers) else ""
            if not accession_number:
                continue

            filed_date = _safe_filing_date(filing_dates[idx] if idx < len(filing_dates) else "")
            accession_clean = accession_number.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{accession_clean}/{accession_number}-index.htm"
            )

            filings.append(
                FilingExtract(
                    accession_number=accession_number,
                    filed_date=filed_date,
                    form_type=form,
                    company_name=company_name,
                    filing_url=filing_url,
                )
            )

            if len(filings) >= max(limit, 1):
                break

        return ToolResult(
            data=filings,
            sources=[
                SourceRef(
                    url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&owner=include&count=40",  # type: ignore[arg-type]
                    title=f"SEC EDGAR filings for {company_name}",
                    fetched_at=now_utc(),
                )
            ],
            timing=build_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("get_filings_metadata(%s): %s", ticker, exc)
        return ToolResult(
            data=[],
            timing=build_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


async def extract_filing_sections(
    filing_url: str,
    sections: list[str],
    max_chars_per_section: int = _DEFAULT_SECTION_CHARS,
) -> list[FilingSection]:
    """Fetch a filing document and return requested sections as truncated markdown."""
    if not sections:
        return []

    # Cap max_chars to prevent abuse
    max_chars_per_section = min(max_chars_per_section, _MAX_SECTION_CHARS)

    # Validate the filing URL domain (allow only sec.gov)
    validate_url_no_resolve(filing_url)
    if not any(d in filing_url for d in ("sec.gov", "SEC.gov")):
        return [
            FilingSection(section_name=s, content_md="Filing URL must be a sec.gov domain.", char_count=39)
            for s in sections
        ]

    async with HttpClient(timeout=45.0, user_agent=_SEC_USER_AGENT) as http:
        index_response = await http.get(filing_url)
        index_html = index_response.text

        soup = BeautifulSoup(index_html, "html.parser")
        doc_url: str | None = None
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            href_lower = href.lower()
            if "index" in href_lower:
                continue
            if not (href_lower.endswith(".htm") or href_lower.endswith(".html")):
                continue
            if href.startswith("http"):
                doc_url = href
            elif href.startswith("/"):
                doc_url = f"https://www.sec.gov{href}"
            else:
                base = filing_url.rsplit("/", 1)[0]
                doc_url = f"{base}/{href}"
            break

        if doc_url is None:
            message = "Could not identify filing document in SEC filing index."
            return [
                FilingSection(section_name=section, content_md=message, char_count=len(message))
                for section in sections
            ]

        doc_response = await http.get(doc_url)
        markdown = html_to_markdown(doc_response.text)

    extracted: list[FilingSection] = []
    for section_name in sections:
        extracted.append(
            _extract_section_markdown(
                markdown=markdown,
                section_name=section_name,
                max_chars_per_section=max_chars_per_section,
            )
        )

    return extracted


async def read_filings(
    query: str,
    max_chars_per_section: int = _DEFAULT_SECTION_CHARS,
) -> ToolResult[FilingsResult]:
    """Read SEC filings via two-step planning + extraction workflow."""
    started_at = now_utc()
    tool_name = "read_filings"

    try:
        llm = get_llm()
        planning_messages = [
            SystemMessage(
                content=(
                    "You are a SEC filings planner. Convert the user query into a FilingPlan. "
                    "Infer ticker, form types, and section focus. "
                    "Use 10-K/10-Q for annual/quarterly report requests. "
                    "Set num_filings to a small number (1-5), default 3."
                )
            ),
            HumanMessage(content=query),
        ]

        plan_raw = await llm.ainvoke_structured(planning_messages, FilingPlan)
        plan = plan_raw if isinstance(plan_raw, FilingPlan) else FilingPlan.model_validate(plan_raw)

        metadata_result = await get_filings_metadata(
            ticker=plan.ticker,
            form_types=plan.form_types,
            limit=plan.num_filings,
        )
        if not metadata_result.success:
            return ToolResult(
                data=FilingsResult(plan=plan, filings=[]),
                sources=metadata_result.sources,
                timing=build_timing(tool_name, started_at),
                success=False,
                error=metadata_result.error,
            )

        enriched_filings: list[FilingExtract] = []
        for filing in metadata_result.data:
            sections = await extract_filing_sections(
                filing_url=filing.filing_url,
                sections=plan.section_focus,
                max_chars_per_section=max_chars_per_section,
            )
            enriched_filings.append(
                FilingExtract(
                    accession_number=filing.accession_number,
                    filed_date=filing.filed_date,
                    form_type=filing.form_type,
                    company_name=filing.company_name,
                    filing_url=filing.filing_url,
                    sections=sections,
                )
            )

        sources = list(metadata_result.sources)
        for filing in enriched_filings:
            sources.append(
                SourceRef(
                    url=filing.filing_url,  # type: ignore[arg-type]
                    title=f"{filing.form_type} {filing.company_name} ({filing.filed_date})",
                    fetched_at=now_utc(),
                )
            )

        return ToolResult(
            data=FilingsResult(plan=plan, filings=enriched_filings),
            sources=sources,
            timing=build_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("read_filings failed: %s", exc)
        fallback_plan = FilingPlan(ticker="", form_types=["10-K", "10-Q"], section_focus=["Risk Factors", "Management Discussion"], num_filings=3)
        return ToolResult(
            data=FilingsResult(plan=fallback_plan, filings=[]),
            timing=build_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )
