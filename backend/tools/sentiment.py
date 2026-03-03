"""Social sentiment research tool.

Runs targeted Reddit and Twitter/X searches, fetches top results, then uses
a subagent LLM to synthesise a Sentiment Snapshot (Overall Bias, Key Catalysts,
Majority Opinion). Results are cached in AnalysisSectionCache with a 24-hour TTL.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from database import SessionLocal
from models import AnalysisSectionCache
from schemas.sentiment import SentimentSnapshot
from schemas.tool_contracts import SourceRef, ToolResult, ToolTiming
from tools._utils import build_timing, now_utc
from tools.web import web_fetch, web_search

logger = logging.getLogger(__name__)

_SENTIMENT_TTL: int = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _check_sentiment_cache(ticker: str) -> SentimentSnapshot | None:
    """Return a cached SentimentSnapshot if the entry is still fresh."""
    db = SessionLocal()
    try:
        row = (
            db.query(AnalysisSectionCache)
            .filter(
                AnalysisSectionCache.ticker == ticker,
                AnalysisSectionCache.section == "sentiment",
            )
            .first()
        )
        if row is None:
            return None
        age = (datetime.now(timezone.utc) - row.generated_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > row.ttl_seconds:
            return None
        try:
            data = json.loads(row.content)
            return SentimentSnapshot(**data)
        except Exception:
            return None
    finally:
        db.close()


def _write_sentiment_cache(ticker: str, snapshot: SentimentSnapshot, rating: str) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AnalysisSectionCache)
            .filter(
                AnalysisSectionCache.ticker == ticker,
                AnalysisSectionCache.section == "sentiment",
            )
            .first()
        )
        content_json = snapshot.model_dump_json()
        if row:
            row.content = content_json
            row.rating = rating
            row.source = "llm"
            row.generated_at = datetime.now(timezone.utc)
            row.ttl_seconds = _SENTIMENT_TTL
        else:
            db.add(AnalysisSectionCache(
                ticker=ticker,
                section="sentiment",
                content=content_json,
                rating=rating,
                source="llm",
                ttl_seconds=_SENTIMENT_TTL,
            ))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed writing sentiment cache for %s", ticker)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = (
    "You are a financial sentiment analyst. Given raw social media posts and "
    "search snippets about a stock ticker, extract and synthesise a structured "
    "sentiment snapshot. Focus on genuine investor discussion; ignore spam, "
    "bots, and promotional content.\n\n"
    "Respond ONLY with a valid JSON object matching this exact schema — no "
    "markdown fences, no extra keys:\n"
    "{\n"
    '  "overall_bias": "<Bullish|Bearish|Neutral|Mixed>",\n'
    '  "key_catalysts": ["<catalyst 1>", "<catalyst 2>", "..."],\n'
    '  "majority_opinion": "<2-3 sentence summary of dominant narrative>",\n'
    '  "reddit_summary": "<1-2 sentence summary of Reddit tone>",\n'
    '  "twitter_summary": "<1-2 sentence summary of Twitter/X tone>",\n'
    '  "confidence": "<High|Medium|Low>"\n'
    "}\n\n"
    "overall_bias must be exactly one of: Bullish, Bearish, Neutral, Mixed.\n"
    "confidence must be exactly one of: High, Medium, Low.\n"
    "key_catalysts must be a list of 3-5 short strings."
)


async def _synthesise_snapshot(ticker: str, raw_content: str) -> SentimentSnapshot:
    """Call the subagent LLM to synthesise a SentimentSnapshot from raw text."""
    from agent.llm import get_llm

    llm = get_llm(role="subagent", purpose="analysis")
    messages = [
        SystemMessage(content=_SYNTHESIS_SYSTEM),
        HumanMessage(content=f"Ticker: {ticker}\n\n--- SOCIAL CONTENT ---\n{raw_content}"),
    ]
    response = await llm.ainvoke(messages)
    text = response.content.strip()

    # Strip markdown fences if any slipped through
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(text)
    return SentimentSnapshot(
        ticker=ticker,
        overall_bias=data.get("overall_bias", "Neutral"),
        key_catalysts=data.get("key_catalysts", []),
        majority_opinion=data.get("majority_opinion", ""),
        reddit_summary=data.get("reddit_summary", ""),
        twitter_summary=data.get("twitter_summary", ""),
        confidence=data.get("confidence", "Low"),
        searched_at=now_utc(),
    )


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

async def get_social_sentiment(ticker: str) -> ToolResult[SentimentSnapshot]:
    """Fetch and synthesise social media sentiment for a stock ticker.

    Runs targeted Reddit and Twitter/X searches, fetches the top articles,
    and synthesises a Sentiment Snapshot using an LLM subagent.  Results are
    cached in AnalysisSectionCache with a 24-hour TTL.
    """
    started_at = now_utc()
    ticker = ticker.upper().strip()

    # Cache check
    cached = _check_sentiment_cache(ticker)
    if cached is not None:
        logger.info("Sentiment cache hit for %s", ticker)
        return ToolResult(
            data=cached,
            sources=[],
            timing=build_timing("get_social_sentiment", started_at),
            success=True,
        )

    # --- Parallel searches ---
    reddit_query = f"site:reddit.com {ticker} stock"
    twitter_query = f"(site:twitter.com OR site:x.com) {ticker} stock sentiment"

    reddit_task = asyncio.create_task(web_search(reddit_query, max_results=5))
    twitter_task = asyncio.create_task(web_search(twitter_query, max_results=5))
    reddit_result, twitter_result = await asyncio.gather(reddit_task, twitter_task, return_exceptions=True)

    all_sources: list[SourceRef] = []
    fetch_tasks: list = []
    hit_labels: list[str] = []  # "reddit" or "twitter" per task

    for label, result in (("reddit", reddit_result), ("twitter", twitter_result)):
        if isinstance(result, Exception) or not result.success:
            logger.warning("Sentiment search failed for %s %s: %s", ticker, label, result)
            continue
        all_sources.extend(result.sources)
        for hit in result.data.hits[:2]:  # fetch top 2 per platform
            fetch_tasks.append(asyncio.create_task(web_fetch(str(hit.url))))
            hit_labels.append(label)

    # Fetch article contents in parallel
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # --- Assemble raw content for LLM ---
    sections: dict[str, list[str]] = {"reddit": [], "twitter": []}
    for label, fr in zip(hit_labels, fetch_results):
        if isinstance(fr, Exception) or not fr.success:
            continue
        all_sources.extend(fr.sources)
        # Use snippet (first 1000 chars) to keep context concise
        content = (fr.data.snippet or "")[:1000]
        if content:
            sections[label].append(content)

    # Fall back to search snippets if fetches yielded nothing
    for label, result in (("reddit", reddit_result), ("twitter", twitter_result)):
        if isinstance(result, Exception) or not result.success:
            continue
        if not sections[label]:
            for hit in result.data.hits:
                sections[label].append(f"{hit.title}: {hit.snippet}"[:500])

    raw_content = (
        f"=== Reddit ===\n" + "\n---\n".join(sections["reddit"] or ["(no data)"]) +
        f"\n\n=== Twitter/X ===\n" + "\n---\n".join(sections["twitter"] or ["(no data)"])
    )

    # --- LLM synthesis ---
    try:
        snapshot = await _synthesise_snapshot(ticker, raw_content)
    except Exception as exc:
        logger.error("Sentiment synthesis failed for %s: %s", ticker, exc)
        return ToolResult(
            data=SentimentSnapshot(
                ticker=ticker,
                overall_bias="Neutral",
                key_catalysts=[],
                majority_opinion="Sentiment synthesis failed; data unavailable.",
                reddit_summary="",
                twitter_summary="",
                confidence="Low",
                searched_at=now_utc(),
            ),
            sources=all_sources,
            timing=build_timing("get_social_sentiment", started_at),
            success=False,
            error=str(exc),
        )

    # Cache the result
    _write_sentiment_cache(ticker, snapshot, snapshot.overall_bias)

    return ToolResult(
        data=snapshot,
        sources=all_sources,
        timing=build_timing("get_social_sentiment", started_at),
        success=True,
    )
