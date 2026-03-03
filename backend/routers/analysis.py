"""Analysis endpoint — POST /api/analysis/{ticker}

Streams per-section analysis (fundamentals, sentiment, technical) via SSE.
Sections resolve progressively from three sources, checked in order:

1. **Cache** — ``AnalysisSectionCache`` with TTL guard
2. **KG**   — fresh knowledge-graph data synthesised with a short LLM call
3. **LLM**  — full mini-graph invocation via the agent with ``agent_mode``

All Ollama calls go through ``ollama_analysis_slot()`` so they queue behind
any active chat stream.

SSE event types:
    ``status``          — progress / queue state
    ``section_ready``   — one section completed (content + rating + source)
    ``overall_rating``  — derived from individual section ratings
    ``done``            — stream finished
    ``error``           — unrecoverable error
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.kg_reader import get_kg_fundamentals, get_kg_sentiment, get_kg_technical
from agent.llm import get_llm
from agent.modes import resolve_requested_mode
from agent.ollama_queue import ollama_analysis_slot
from database import SessionLocal
from models import AnalysisSectionCache

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECTION_NAMES = ("fundamentals", "sentiment", "technical")

_SECTION_TTL: dict[str, int] = {
    "fundamentals": 86400,  # 24 h
    "sentiment": 86400,
    "technical": 14400,     # 4 h
}

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")

# Rating classification for the overall derivation
_POSITIVE_RATINGS = {"strong", "bullish", "uptrend", "buy"}
_NEUTRAL_RATINGS = {"fair", "neutral", "sideways", "hold"}
_NEGATIVE_RATINGS = {"weak", "bearish", "downtrend", "sell"}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _extract_rating(text: str) -> str:
    """Best-effort extraction of a single-word rating from LLM output."""
    # Look for known rating words near the top of the text
    for line in text.split("\n")[:8]:
        upper = line.strip().upper()
        for word in ["STRONG", "FAIR", "WEAK", "BULLISH", "NEUTRAL", "BEARISH",
                     "UPTREND", "SIDEWAYS", "DOWNTREND", "BUY", "HOLD", "SELL"]:
            if word in upper:
                return word.capitalize()
    return ""


def _derive_overall(ratings: list[str]) -> str:
    """Derive an overall rating from individual section ratings."""
    scores = []
    for r in ratings:
        rl = r.lower()
        if rl in _POSITIVE_RATINGS:
            scores.append(1)
        elif rl in _NEUTRAL_RATINGS:
            scores.append(0)
        elif rl in _NEGATIVE_RATINGS:
            scores.append(-1)
    if not scores:
        return "Neutral"
    avg = sum(scores) / len(scores)
    if avg > 0.3:
        return "Bullish"
    if avg < -0.3:
        return "Bearish"
    return "Neutral"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _check_cache(ticker: str, section: str) -> dict[str, Any] | None:
    """Return cached section if TTL is still valid."""
    db = SessionLocal()
    try:
        row = (
            db.query(AnalysisSectionCache)
            .filter(
                AnalysisSectionCache.ticker == ticker,
                AnalysisSectionCache.section == section,
            )
            .first()
        )
        if row is None:
            return None

        # Freshness check
        gen_at = row.generated_at
        if gen_at.tzinfo is None:
            gen_at = gen_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - gen_at).total_seconds()
        if age > row.ttl_seconds:
            return None

        return {
            "content": row.content,
            "rating": row.rating,
            "source": "cache",
        }
    finally:
        db.close()


def _upsert_cache(ticker: str, section: str, content: str, rating: str, source: str) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AnalysisSectionCache)
            .filter(
                AnalysisSectionCache.ticker == ticker,
                AnalysisSectionCache.section == section,
            )
            .first()
        )
        ttl = _SECTION_TTL.get(section, 14400)
        if row:
            row.content = content
            row.rating = rating
            row.source = source
            row.generated_at = datetime.now(timezone.utc)
            row.ttl_seconds = ttl
        else:
            db.add(AnalysisSectionCache(
                ticker=ticker,
                section=section,
                content=content,
                rating=rating,
                generated_at=datetime.now(timezone.utc),
                source=source,
                ttl_seconds=ttl,
            ))
        db.commit()
    except Exception as exc:
        logger.warning("Cache upsert failed for %s/%s: %s", ticker, section, exc)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# KG → LLM synthesis (short call, no tools)
# ---------------------------------------------------------------------------


async def _synthesize_from_kg(ticker: str, section: str, kg_data: dict) -> dict[str, Any]:
    """Run a short LLM call to synthesise KG data into prose."""
    from agent.prompts import get_finalize_prompt
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_llm(role="agent", purpose="analysis")

    system_prompt = get_finalize_prompt(agent_mode=section)
    data_block = json.dumps(kg_data.get("data", kg_data), indent=2, default=str)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"Synthesise this {section} data for {ticker} into a concise "
            f"2-3 sentence analysis:\n\n{data_block}"
        )),
    ]

    result = await llm.ainvoke(messages)
    content = result.content if hasattr(result, "content") else str(result)
    rating = _extract_rating(content)

    return {"content": content, "rating": rating, "source": "kg"}


# ---------------------------------------------------------------------------
# Full mini-graph invocation
# ---------------------------------------------------------------------------


async def _run_mini_graph(ticker: str, section: str) -> dict[str, Any]:
    """Run the full agent graph with mode={section} for a single ticker."""
    from agent.graph import graph
    from langchain_core.messages import HumanMessage

    prompt_map = {
        "fundamentals": f"Provide a fundamental analysis of {ticker}.",
        "sentiment": f"Analyse the market sentiment and institutional positioning for {ticker}.",
        "technical": f"Provide a technical analysis of {ticker}.",
    }

    initial_state = {
        "messages": [HumanMessage(content=prompt_map.get(section, f"Analyze {ticker}"))],
        "intent": "",
        "tickers_mentioned": [ticker],
        "context_refs": [ticker],
        "injected_context": "",
        "ticker_reports": {},
        "session_id": f"analysis-{ticker}-{section}",
        "current_query": "",
        "active_skills": [],
        "tool_call_count": 0,
        "external_call_count": 0,
        "tool_results": [],
        "agent_mode": resolve_requested_mode(None, section),
        "start_time_utc": datetime.now(timezone.utc).isoformat(),
        "capabilities": {},
    }

    # Collect the final response
    full_response = ""
    async for event in graph.astream_events(initial_state, version="v2"):
        evt = event.get("event", "")
        if evt == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                content = chunk.content
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                elif not isinstance(content, str):
                    content = str(content)
                full_response += content

    # Remove <think> blocks
    import re as _re
    full_response = _re.sub(r"<think>.*?</think>", "", full_response, flags=_re.DOTALL).strip()

    rating = _extract_rating(full_response)
    return {"content": full_response, "rating": rating, "source": "llm"}


# ---------------------------------------------------------------------------
# SSE streaming endpoint
# ---------------------------------------------------------------------------


async def _stream_analysis(ticker: str) -> AsyncGenerator[str, None]:
    """Generate SSE events for progressive analysis."""
    yield _sse({"type": "status", "message": f"Starting analysis for {ticker}"})

    section_ratings: list[str] = []
    kg_readers = {
        "fundamentals": get_kg_fundamentals,
        "sentiment": get_kg_sentiment,
        "technical": get_kg_technical,
    }

    for section in _SECTION_NAMES:
        try:
            # 1. Check cache
            cached = _check_cache(ticker, section)
            if cached:
                logger.info("Analysis %s/%s: cache hit", ticker, section)
                yield _sse({
                    "type": "section_ready",
                    "section": section,
                    "content": cached["content"],
                    "rating": cached["rating"],
                    "source": "cache",
                })
                section_ratings.append(cached["rating"])
                continue

            # 2. Check KG
            kg_data = kg_readers[section](ticker)
            if kg_data is not None:
                logger.info("Analysis %s/%s: KG hit, synthesising", ticker, section)
                yield _sse({"type": "status", "message": f"Synthesising {section} from cached data"})

                async with ollama_analysis_slot(timeout=120) as status:
                    if status == "queued":
                        yield _sse({"type": "status", "message": f"Queued — waiting for active chat"})

                    result = await _synthesize_from_kg(ticker, section, kg_data)

                _upsert_cache(ticker, section, result["content"], result["rating"], "kg")
                yield _sse({
                    "type": "section_ready",
                    "section": section,
                    "content": result["content"],
                    "rating": result["rating"],
                    "source": "kg",
                })
                section_ratings.append(result["rating"])
                continue

            # 3. Full graph invocation
            logger.info("Analysis %s/%s: running mini-graph", ticker, section)
            yield _sse({"type": "status", "message": f"Running {section} analysis"})

            async with ollama_analysis_slot(timeout=120) as status:
                if status == "queued":
                    yield _sse({"type": "status", "message": f"Queued — waiting for active chat"})

                result = await asyncio.wait_for(
                    _run_mini_graph(ticker, section),
                    timeout=120.0,
                )

            _upsert_cache(ticker, section, result["content"], result["rating"], "llm")
            yield _sse({
                "type": "section_ready",
                "section": section,
                "content": result["content"],
                "rating": result["rating"],
                "source": result["source"],
            })
            section_ratings.append(result["rating"])

        except asyncio.TimeoutError:
            logger.error("Analysis %s/%s timed out", ticker, section)
            yield _sse({
                "type": "section_ready",
                "section": section,
                "content": f"{section.capitalize()} analysis timed out.",
                "rating": "",
                "source": "error",
            })
        except Exception as exc:
            logger.error("Analysis %s/%s error: %s", ticker, section, exc, exc_info=True)
            yield _sse({
                "type": "section_ready",
                "section": section,
                "content": f"{section.capitalize()} analysis failed: {exc}",
                "rating": "",
                "source": "error",
            })

    # Overall rating
    overall = _derive_overall(section_ratings)
    yield _sse({"type": "overall_rating", "rating": overall})
    yield _sse({"type": "done"})


@router.post("/analysis/{ticker}")
async def analysis_endpoint(ticker: str):
    """POST /api/analysis/{ticker} — Stream progressive analysis sections."""
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        return StreamingResponse(
            iter([_sse({"type": "error", "message": f"Invalid ticker: {ticker}"})]),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _stream_analysis(ticker),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
