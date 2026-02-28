"""SEC EDGAR tools: 8-K filing lookup and full-text extraction.

Uses the free ``data.sec.gov`` API — no API key required.

Functions
---------
``get_recent_8k_filings(symbol, limit=5)``
    Fetch recent 8-K filing metadata via the submissions API.
    Resolves ticker → CIK via the SEC company tickers mapping.

``get_8k_detail(filing)``
    Fetch the full 8-K document and extract per-item text sections
    (e.g. *Item 2.02 Results of Operations*, *Item 8.01 Other Events*).

Primary use-case
----------------
After an anomaly is detected (e.g. a 5% price drop), the LangGraph agent calls
``get_recent_8k_filings`` to check whether a material event was disclosed, then
``get_8k_detail`` to read what happened — all without an API key.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from clients.edgar import EdgarClient
from schemas.finance import Filing8K, Filing8KDetail
from schemas.tool_contracts import SourceRef, ToolResult
from tools._utils import build_timing, now_utc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# get_recent_8k_filings
# ---------------------------------------------------------------------------

async def get_recent_8k_filings(
    symbol: str,
    limit: int = 5,
) -> ToolResult[list[Filing8K]]:
    """Fetch recent 8-K filings for a ticker from SEC EDGAR.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"AAPL"``).
    limit:
        Maximum number of 8-K filings to return, ordered most-recent first.
    """
    started_at = now_utc()
    tool_name = "get_recent_8k_filings"

    try:
        async with EdgarClient() as edgar:
            cik = await edgar.ticker_to_cik(symbol)
            if cik is None:
                return ToolResult(
                    data=[],
                    timing=build_timing(tool_name, started_at),
                    success=False,
                    error=(
                        f"Could not resolve {symbol!r} to a CIK. "
                        "The company may not file with the SEC, or the ticker is incorrect."
                    ),
                )

            submissions = await edgar.get(f"/submissions/CIK{cik}.json")

        company_name: str = submissions.get("name", symbol.upper())
        recent: dict = submissions.get("filings", {}).get("recent", {})

        form_types: list[str] = recent.get("form", [])
        filed_dates: list[str] = recent.get("filingDate", [])
        accession_numbers: list[str] = recent.get("accessionNumber", [])
        items_raw_list: list[str] = recent.get("items", [])

        filings: list[Filing8K] = []
        cik_int = str(int(cik))  # un-padded for URL construction

        for i, form_type in enumerate(form_types):
            if form_type not in ("8-K", "8-K/A"):
                continue

            acc_no: str = accession_numbers[i] if i < len(accession_numbers) else ""
            filed_str: str = filed_dates[i] if i < len(filed_dates) else ""
            items_raw: str = items_raw_list[i] if i < len(items_raw_list) else ""

            # Items field is a comma-separated string: "2.02,9.01"
            items_parsed = [
                f"Item {x.strip()}"
                for x in str(items_raw).split(",")
                if x.strip() and x.strip() != "nan"
            ]

            acc_clean = acc_no.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{acc_no}-index.htm"
            )

            try:
                filed_date = date.fromisoformat(filed_str)
            except (ValueError, TypeError):
                filed_date = date.today()

            filings.append(Filing8K(
                accession_number=acc_no,
                filed_date=filed_date,
                form_type=form_type,
                items=items_parsed,
                filing_url=filing_url,
                company_name=company_name,
                cik=cik,
            ))

            if len(filings) >= limit:
                break

        edgar_index_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={cik}&type=8-K&dateb=&owner=include&count=40"
        )

        return ToolResult(
            data=filings,
            sources=[SourceRef(
                url=edgar_index_url,  # type: ignore[arg-type]
                title=f"SEC EDGAR 8-K filings: {company_name}",
                fetched_at=now_utc(),
            )],
            timing=build_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("get_recent_8k_filings(%s): %s", symbol, exc)
        return ToolResult(
            data=[],
            timing=build_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# get_8k_detail
# ---------------------------------------------------------------------------

async def get_8k_detail(filing: Filing8K) -> ToolResult[Filing8KDetail]:
    """Fetch and parse the full text of an 8-K filing from SEC EDGAR.

    Downloads the filing index, discovers the primary document, and extracts
    per-item text sections suitable for LLM consumption (≤50 K chars total).

    Parameters
    ----------
    filing:
        A ``Filing8K`` returned by ``get_recent_8k_filings``.
    """
    started_at = now_utc()
    tool_name = "get_8k_detail"

    try:
        from bs4 import BeautifulSoup
        from clients.http_base import HttpClient

        cik_int = str(int(filing.cik))
        acc_clean = filing.accession_number.replace("-", "")

        _UA = "OpenFin/1.0 (financial-ai-copilot; contact@openfin.local)"

        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
            f"{acc_clean}/{filing.accession_number}-index.htm"
        )

        async with HttpClient(timeout=30.0, user_agent=_UA) as http:
            try:
                idx_resp = await http.get(index_url)
                index_html = idx_resp.text
            except Exception as idx_exc:
                logger.warning(
                    "get_8k_detail: index fetch failed (%s), trying JSON index. %s",
                    index_url, idx_exc,
                )
                # Some older filings use a .json index
                json_index_url = (
                    f"https://data.sec.gov/submissions/CIK{filing.cik}.json"
                )
                idx_resp = await http.get(json_index_url)
                # If this also fails it will raise and be caught by the outer try
                index_html = ""

        # --- Discover the primary 8-K document from the index page ---
        doc_url: str | None = None
        if index_html:
            soup = BeautifulSoup(index_html, "html.parser")
            for link in soup.find_all("a", href=True):
                href = str(link["href"])
                lower = href.lower()
                # Skip the index file itself; prefer .htm documents
                if "index" in lower:
                    continue
                if lower.endswith(".htm") or lower.endswith(".html"):
                    if href.startswith("/"):
                        doc_url = f"https://www.sec.gov{href}"
                    elif href.startswith("http"):
                        doc_url = href
                    else:
                        doc_url = (
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{cik_int}/{acc_clean}/{href}"
                        )
                    break

        # --- Fetch and parse the primary document ---
        full_text = ""
        if doc_url:
            try:
                async with HttpClient(timeout=60.0, user_agent=_UA) as http:
                    doc_resp = await http.get(doc_url)
                doc_html = doc_resp.text

                doc_soup = BeautifulSoup(doc_html, "html.parser")
                for tag in doc_soup(["script", "style"]):
                    tag.decompose()
                full_text = doc_soup.get_text(separator="\n", strip=True)
                full_text = full_text[:50_000]  # truncate for LLM
            except Exception as doc_exc:
                logger.warning("get_8k_detail: document fetch failed (%s): %s", doc_url, doc_exc)

        # --- Extract individual item sections ---
        extracted_items: dict[str, str] = {}
        if full_text:
            # Match "Item X.XX" headers followed by their text
            item_pat = re.compile(
                r"(Item\s+\d+\.\d+[^\n]{0,80})\n(.*?)(?=Item\s+\d+\.\d+|\Z)",
                re.DOTALL | re.IGNORECASE,
            )
            for match in item_pat.finditer(full_text):
                header = match.group(1).strip()
                body = match.group(2).strip()[:5_000]  # 5 K chars per item
                if body:
                    extracted_items[header] = body

        return ToolResult(
            data=Filing8KDetail(
                filing=filing,
                full_text=full_text,
                extracted_items=extracted_items,
            ),
            sources=[SourceRef(
                url=filing.filing_url,  # type: ignore[arg-type]
                title=f"8-K: {filing.company_name} ({filing.filed_date})",
                fetched_at=now_utc(),
            )],
            timing=build_timing(tool_name, started_at),
            success=True,
        )

    except Exception as exc:
        logger.warning("get_8k_detail(%s): %s", filing.accession_number, exc)
        return ToolResult(
            data=Filing8KDetail(
                filing=filing,
                full_text="",
                extracted_items={},
            ),
            timing=build_timing(tool_name, started_at),
            success=False,
            error=str(exc),
        )
