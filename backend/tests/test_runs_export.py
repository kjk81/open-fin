from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import (
    AgentRun,
    AgentRunEvent,
    EpisodicSummary,
    PortfolioSnapshot,
    ResearchLibrary,
    UserPreferences,
)
from routers import runs as runs_router


def _build_client() -> tuple[TestClient, sessionmaker]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(runs_router.router, prefix="/api")

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    return TestClient(app), testing_session_local


def test_run_export_includes_required_sections_and_redacts_sensitive_payloads():
    client, session_factory = _build_client()
    run_id = "11111111-1111-1111-1111-111111111111"

    with session_factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                session_id="sess-1",
                mode="analysis",
                status="success",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
        )

        tool_end_payload = {
            "tool": "web_search",
            "step_id": "tool-web_search-1",
            "success": True,
            "api_key": "secret-123",
            "nested": {
                "authorization": "Bearer abc",
                "access_token": "xyz",
                "safe": "ok",
            },
            "result_envelope": {
                "success": True,
                "data": {"artifact": "snapshot"},
                "sources": [
                    {"url": "https://example.com/a", "title": "A"},
                    {"url": "https://example.com/b", "title": "B"},
                ],
            },
            "trace": {"tool_call_id": "call-1"},
        }
        db.add(
            AgentRunEvent(
                run_id=run_id,
                seq=1,
                type="tool_end",
                payload_json=json.dumps(tool_end_payload),
            )
        )

        db.add(
            PortfolioSnapshot(
                run_id=run_id,
                category="portfolio",
                content_json=json.dumps({"positions": [{"ticker": "AAPL"}]}),
                citations_json=json.dumps([
                    {"url": "https://example.com/a", "title": "A"},
                    {"url": "https://example.com/c", "title": "C"},
                ]),
                tags_json=json.dumps(["snapshot"]),
                confidence=0.8,
            )
        )
        db.add(
            UserPreferences(
                run_id=run_id,
                category="risk",
                content_json=json.dumps({"level": "moderate"}),
                citations_json="[]",
                tags_json=json.dumps(["preference"]),
            )
        )
        db.add(
            EpisodicSummary(
                run_id=run_id,
                category="episodic",
                content_json=json.dumps({"summary": "note"}),
                citations_json="[]",
                tags_json="[]",
            )
        )
        db.add(
            ResearchLibrary(
                run_id=run_id,
                category="research",
                content_json=json.dumps({"thesis": "long"}),
                citations_json=json.dumps([
                    {"url": "https://example.com/d", "title": "D"},
                ]),
                tags_json="[]",
            )
        )
        db.commit()

    res = client.get(f"/api/runs/{run_id}/export")
    assert res.status_code == 200
    payload = res.json()

    assert payload["run_header"]["id"] == run_id
    assert isinstance(payload["event_timeline"], list)
    assert isinstance(payload["artifacts_registry"], list)
    assert isinstance(payload["citations"], list)
    assert "context_snapshots" in payload

    first_event = payload["event_timeline"][0]
    assert first_event["payload"]["api_key"] == "***REDACTED***"
    assert first_event["payload"]["nested"]["authorization"] == "***REDACTED***"
    assert first_event["payload"]["nested"]["access_token"] == "***REDACTED***"
    assert first_event["payload"]["nested"]["safe"] == "ok"

    snapshots = payload["context_snapshots"]
    assert len(snapshots["portfolio_snapshots"]) == 1
    assert len(snapshots["user_preferences"]) == 1
    assert len(snapshots["episodic_summaries"]) == 1
    assert len(snapshots["research_library"]) == 1

    artifact = payload["artifacts_registry"][0]
    assert artifact["artifact_type"] == "tool_result"
    assert artifact["tool"] == "web_search"

    citation_urls = {item.get("url") for item in payload["citations"]}
    assert "https://example.com/a" in citation_urls
    assert "https://example.com/b" in citation_urls
    assert "https://example.com/c" in citation_urls
    assert "https://example.com/d" in citation_urls


def test_run_export_returns_404_for_missing_run():
    client, _ = _build_client()
    run_id = "11111111-1111-1111-1111-111111111111"

    res = client.get(f"/api/runs/{run_id}/export")
    assert res.status_code == 404
    assert res.json()["detail"] == "Run not found"
