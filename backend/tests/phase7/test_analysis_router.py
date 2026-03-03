"""Tests for analysis router orchestration and SSE event ordering."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routers.analysis import router as analysis_router


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.strip().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(analysis_router, prefix="/api")
    return app


async def _post_analysis(app: FastAPI, ticker: str = "AAPL") -> tuple[int, str]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/analysis/{ticker}", timeout=30)
        return resp.status_code, resp.text


@pytest.mark.asyncio
async def test_cache_hit_all_sections_emits_section_ready_overall_and_done(monkeypatch):
    cache_map = {
        "fundamentals": {"content": "F cached", "rating": "Strong", "source": "cache"},
        "sentiment": {"content": "S cached", "rating": "Neutral", "source": "cache"},
        "technical": {"content": "T cached", "rating": "Weak", "source": "cache"},
    }

    monkeypatch.setattr("routers.analysis._check_cache", lambda _ticker, section: cache_map[section])

    run_mini_graph = AsyncMock(side_effect=AssertionError("mini-graph should not run on cache hit"))
    monkeypatch.setattr("routers.analysis._run_mini_graph", run_mini_graph)

    app = _build_app()
    status, body = await _post_analysis(app)

    assert status == 200
    events = _parse_sse(body)

    assert events[0]["type"] == "status"
    section_events = [e for e in events if e.get("type") == "section_ready"]
    assert [e["section"] for e in section_events] == ["fundamentals", "sentiment", "technical"]
    assert all(e["source"] == "cache" for e in section_events)

    overall_idx = next(i for i, e in enumerate(events) if e.get("type") == "overall_rating")
    done_idx = next(i for i, e in enumerate(events) if e.get("type") == "done")
    assert overall_idx < done_idx
    assert events[-1]["type"] == "done"

    run_mini_graph.assert_not_called()


@pytest.mark.asyncio
async def test_kg_hit_uses_synthesis_and_skips_mini_graph(monkeypatch):
    monkeypatch.setattr("routers.analysis._check_cache", lambda _ticker, _section: None)

    monkeypatch.setattr("routers.analysis.get_kg_fundamentals", lambda _ticker: {"data": {"pe": 24}})
    monkeypatch.setattr("routers.analysis.get_kg_sentiment", lambda _ticker: {"data": {"sentiment": "mixed"}})
    monkeypatch.setattr("routers.analysis.get_kg_technical", lambda _ticker: {"data": {"rsi": 55}})

    synth = AsyncMock(side_effect=lambda ticker, section, _kg: {
        "content": f"{section} from kg for {ticker}",
        "rating": "Neutral",
        "source": "kg",
    })
    monkeypatch.setattr("routers.analysis._synthesize_from_kg", synth)

    run_mini_graph = AsyncMock(side_effect=AssertionError("mini-graph should not run when KG is available"))
    monkeypatch.setattr("routers.analysis._run_mini_graph", run_mini_graph)

    monkeypatch.setattr("routers.analysis._upsert_cache", lambda *args, **kwargs: None)

    app = _build_app()
    status, body = await _post_analysis(app)

    assert status == 200
    events = _parse_sse(body)
    section_events = [e for e in events if e.get("type") == "section_ready"]

    fundamentals_event = next(e for e in section_events if e["section"] == "fundamentals")
    assert fundamentals_event["source"] == "kg"

    synth_sections = [call.args[1] for call in synth.await_args_list]
    assert synth_sections == ["fundamentals", "sentiment", "technical"]
    run_mini_graph.assert_not_called()
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_mini_graph_timeout_for_one_section_still_finishes_with_done(monkeypatch):
    monkeypatch.setattr("routers.analysis._check_cache", lambda _ticker, _section: None)
    monkeypatch.setattr("routers.analysis.get_kg_fundamentals", lambda _ticker: None)
    monkeypatch.setattr("routers.analysis.get_kg_sentiment", lambda _ticker: None)
    monkeypatch.setattr("routers.analysis.get_kg_technical", lambda _ticker: None)
    monkeypatch.setattr("routers.analysis._upsert_cache", lambda *args, **kwargs: None)

    async def _run_mini_graph(_ticker: str, section: str):
        if section == "sentiment":
            raise asyncio.TimeoutError()
        return {
            "content": f"{section} ok",
            "rating": "Neutral",
            "source": "llm",
        }

    monkeypatch.setattr("routers.analysis._run_mini_graph", _run_mini_graph)

    app = _build_app()
    status, body = await _post_analysis(app)

    assert status == 200
    events = _parse_sse(body)

    section_events = [e for e in events if e.get("type") == "section_ready"]
    assert len(section_events) == 3

    sentiment_event = next(e for e in section_events if e["section"] == "sentiment")
    assert sentiment_event["source"] == "error"
    assert "timed out" in sentiment_event["content"].lower()

    overall_idx = next(i for i, e in enumerate(events) if e.get("type") == "overall_rating")
    done_idx = next(i for i, e in enumerate(events) if e.get("type") == "done")
    assert overall_idx < done_idx
    assert events[-1]["type"] == "done"
