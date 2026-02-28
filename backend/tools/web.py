"""Web research tools: static HTML fetch + web search.

Functions
---------
web_fetch(url, extract_mode)
    Download a URL, parse HTML → Markdown (or plain text), cache in SQLite
    with a 15-minute TTL, and return ``ToolResult[WebDocument]``.

web_search(query, provider, max_results)
    Execute a web search via Tavily (default) or Exa, normalise into
    ``ToolResult[WebSearchResult]``.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from bs4 import BeautifulSoup

from clients.http_base import HttpClient, HttpClientError
from database import SessionLocal
from models import HttpCache
from schemas.kg_entities import WebDocument
from schemas.tool_contracts import (
    SearchHit,
    SourceRef,
    ToolResult,
    ToolTiming,
    WebSearchResult,
)
from tools._utils import html_to_markdown, now_utc

logger = logging.getLogger(__name__)

_FETCH_TTL_SECONDS: int = 900  # 15 minutes
_STRIP_TAGS: list[str] = ["script", "style", "nav", "footer", "header", "aside"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_get(url: str) -> str | None:
    """Return cached response text if a fresh entry exists, else None."""
    db = SessionLocal()
    try:
        row = db.query(HttpCache).filter(HttpCache.url == url).first()
        if row is None:
            return None
        expiry = row.fetched_at + timedelta(seconds=row.ttl_seconds)
        if now_utc() > expiry:
            return None
        return row.response_text
    finally:
        db.close()


def _cache_put(url: str, text: str) -> None:
    """Upsert a cache entry with a 15-minute TTL."""
    db = SessionLocal()
    try:
        row = db.query(HttpCache).filter(HttpCache.url == url).first()
        if row is None:
            row = HttpCache(url=url, response_text=text, ttl_seconds=_FETCH_TTL_SECONDS)
            db.add(row)
        else:
            row.response_text = text
            row.fetched_at = now_utc()
            row.ttl_seconds = _FETCH_TTL_SECONDS
        db.commit()
    finally:
        db.close()


def _extract_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else fallback


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

async def web_fetch(
    url: str,
    extract_mode: str = "markdown",
) -> ToolResult[WebDocument]:
    """Fetch a URL and return its content as a WebDocument.

    Parameters
    ----------
    url:
        Fully-qualified URL to fetch.
    extract_mode:
        ``"markdown"`` (default) converts HTML → Markdown via markdownify.
        ``"text"`` returns plain text via BeautifulSoup's ``.get_text()``.
    """
    started_at = now_utc()

    try:
        # 1. Cache check -------------------------------------------------------
        cached = _cache_get(url)
        if cached is not None:
            logger.debug("web_fetch cache hit: %s", url)
            content = cached
            title = _extract_title(content, url)
        else:
            # 2. HTTP fetch ----------------------------------------------------
            async with HttpClient(timeout=30.0) as client:
                response = await client.get(url)
            raw_html = response.text

            # 3. Extract -------------------------------------------------------
            if extract_mode == "text":
                soup = BeautifulSoup(raw_html, "html.parser")
                for tag in soup(_STRIP_TAGS):
                    tag.decompose()
                content = soup.get_text(separator="\n", strip=True)
            else:
                content = html_to_markdown(raw_html)

            title = _extract_title(raw_html, url)

            # 4. Cache write ---------------------------------------------------
            _cache_put(url, raw_html)

        ended_at = now_utc()
        doc = WebDocument(
            url=url,  # type: ignore[arg-type]
            title=title,
            snippet=content[:500],
            fetched_at=ended_at,
        )
        # Attach full markdown to snippet for downstream LLM use
        doc.snippet = content

        return ToolResult(
            data=doc,
            sources=[SourceRef(url=url, title=title, fetched_at=ended_at)],  # type: ignore[arg-type]
            timing=ToolTiming(
                tool_name="web_fetch",
                started_at=started_at,
                ended_at=ended_at,
            ),
            success=True,
        )

    except (HttpClientError, Exception) as exc:
        ended_at = now_utc()
        logger.warning("web_fetch failed for %s: %s", url, exc)
        # Return a minimal failed result with a placeholder doc
        return ToolResult(
            data=WebDocument(
                url=url,  # type: ignore[arg-type]
                title="",
                snippet=None,
                fetched_at=ended_at,
            ),
            timing=ToolTiming(
                tool_name="web_fetch",
                started_at=started_at,
                ended_at=ended_at,
            ),
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

async def web_search(
    query: str,
    provider: str = "tavily",
    max_results: int = 5,
) -> ToolResult[WebSearchResult]:
    """Search the web and return normalised results.

    Parameters
    ----------
    query:
        Natural-language search query.
    provider:
        ``"tavily"`` (default) or ``"exa"``.
    max_results:
        Maximum number of result hits to return.

    Environment variables
    ---------------------
    TAVILY_API_KEY
        Required when ``provider="tavily"``.
    EXA_API_KEY
        Required when ``provider="exa"``.
    """
    started_at = now_utc()

    try:
        if provider == "tavily":
            hits = await _search_tavily(query, max_results)
        elif provider == "exa":
            hits = await _search_exa(query, max_results)
        else:
            raise ValueError(f"Unknown provider: {provider!r}. Use 'tavily' or 'exa'.")

        ended_at = now_utc()
        result_data = WebSearchResult(query=query, hits=hits, provider=provider)
        sources = [
            SourceRef(url=h.url, title=h.title, fetched_at=ended_at)
            for h in hits
        ]
        return ToolResult(
            data=result_data,
            sources=sources,
            timing=ToolTiming(
                tool_name="web_search",
                started_at=started_at,
                ended_at=ended_at,
            ),
            success=True,
        )

    except Exception as exc:
        ended_at = now_utc()
        logger.warning("web_search failed (provider=%s, query=%r): %s", provider, query, exc)
        return ToolResult(
            data=WebSearchResult(query=query, hits=[], provider=provider),
            timing=ToolTiming(
                tool_name="web_search",
                started_at=started_at,
                ended_at=ended_at,
            ),
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Provider-specific adapters
# ---------------------------------------------------------------------------

async def _search_tavily(query: str, max_results: int) -> list[SearchHit]:
    """Call the Tavily search API and normalise results."""
    from tavily import TavilyClient  # type: ignore[import-untyped]

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "TAVILY_API_KEY environment variable is not set. "
            "Sign up at https://tavily.com and add the key to backend/.env."
        )

    # TavilyClient.search is synchronous — run in executor to keep async path clean
    import asyncio

    loop = asyncio.get_event_loop()
    client = TavilyClient(api_key=api_key)
    response = await loop.run_in_executor(
        None,
        lambda: client.search(query, max_results=max_results),
    )

    hits: list[SearchHit] = []
    for item in response.get("results", []):
        hits.append(
            SearchHit(
                title=item.get("title", ""),
                url=item["url"],
                snippet=item.get("content", ""),
                score=item.get("score"),
            )
        )
    return hits


async def _search_exa(query: str, max_results: int) -> list[SearchHit]:
    """Call the Exa search API and normalise results."""
    from exa_py import Exa  # type: ignore[import-untyped]

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "EXA_API_KEY environment variable is not set. "
            "Sign up at https://exa.ai and add the key to backend/.env."
        )

    import asyncio

    loop = asyncio.get_event_loop()
    exa = Exa(api_key=api_key)
    response = await loop.run_in_executor(
        None,
        lambda: exa.search(query, num_results=max_results, use_autoprompt=True),
    )

    hits: list[SearchHit] = []
    for item in response.results:
        hits.append(
            SearchHit(
                title=getattr(item, "title", "") or "",
                url=item.url,
                snippet=getattr(item, "text", "") or "",
                score=getattr(item, "score", None),
            )
        )
    return hits
