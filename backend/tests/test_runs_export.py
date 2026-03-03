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


def test_run_export_redacts_nested_list_secrets_and_handles_malformed_payloads():
    client, session_factory = _build_client()
    run_id = "22222222-2222-2222-2222-222222222222"

    with session_factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                session_id="sess-2",
                mode="analysis",
                status="success",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
        )

        # Well-formed payload with nested list secrets
        payload_with_list = {
            "credentials": [
                {"api_key": "top-secret", "note": "should be redacted"},
                {"token": "another-secret"},
            ],
            "safe_list": [1, 2, 3],
        }
        db.add(
            AgentRunEvent(
                run_id=run_id,
                seq=1,
                type="tool_end",
                payload_json=json.dumps(payload_with_list),
            )
        )

        # Malformed JSON payload should not break export
        db.add(
            AgentRunEvent(
                run_id=run_id,
                seq=2,
                type="tool_end",
                payload_json="not-json",
            )
        )
        db.commit()

    res = client.get(f"/api/runs/{run_id}/export")
    assert res.status_code == 200
    bundle = res.json()

    assert len(bundle["event_timeline"]) == 2
    first, second = bundle["event_timeline"]

    # First event is parsed and redacted, including secrets in nested lists
    creds = first["payload"]["credentials"]
    assert creds[0]["api_key"] == "***REDACTED***"
    assert creds[0]["note"] == "should be redacted"
    assert creds[1]["token"] == "***REDACTED***"
    assert first["payload"]["safe_list"] == [1, 2, 3]

    # Second event had malformed JSON; payload should be None
    assert second["payload"] is None


def test_run_export_orders_events_by_seq_then_id_independent_of_insertion():
    client, session_factory = _build_client()
    run_id = "33333333-3333-3333-3333-333333333333"

    with session_factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                session_id="sess-3",
                mode="analysis",
                status="success",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        # Insert events out of sequence and with duplicate seq to exercise ordering
        e1 = AgentRunEvent(
            run_id=run_id,
            seq=2,
            type="tool_end",
            payload_json=json.dumps({"step": "second"}),
        )
        db.add(e1)
        db.commit()

        e2 = AgentRunEvent(
            run_id=run_id,
            seq=1,
            type="tool_end",
            payload_json=json.dumps({"step": "first"}),
        )
        db.add(e2)
        db.commit()

        # Same seq, different ids: order should fall back to id asc
        e3 = AgentRunEvent(
            run_id=run_id,
            seq=2,
            type="tool_end",
            payload_json=json.dumps({"step": "second-b"}),
        )
        db.add(e3)
        db.commit()

        first_id, second_id, third_id = e1.id, e2.id, e3.id
        assert first_id != second_id != third_id

    res = client.get(f"/api/runs/{run_id}/export")
    assert res.status_code == 200
    bundle = res.json()

    steps = [evt["payload"]["step"] for evt in bundle["event_timeline"]]

    # Ordered by seq asc, then id asc within the same seq
    assert steps[0] == "first"  # seq=1
    # For seq=2 events, the one with smaller id should come first
    seq2_steps = [evt["payload"]["step"] for evt in bundle["event_timeline"] if evt["seq"] == 2]
    assert seq2_steps == sorted(seq2_steps, key=lambda s: ["second", "second-b"].index(s))


def test_run_bundle_replay_reconstructs_tool_sequence():
    client, session_factory = _build_client()
    run_id = "44444444-4444-4444-4444-444444444444"

    with session_factory() as db:
        db.add(
            AgentRun(
                id=run_id,
                session_id="sess-4",
                mode="analysis",
                status="success",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
        )

        # Simulate a simple run with two tool calls (search_web then get_company_profile)
        db.add_all(
            [
                AgentRunEvent(
                    run_id=run_id,
                    seq=1,
                    type="tool_start",
                    payload_json=json.dumps({"tool": "search_web", "step_id": "tool-search_web-1"}),
                ),
                AgentRunEvent(
                    run_id=run_id,
                    seq=2,
                    type="tool_end",
                    payload_json=json.dumps(
                        {
                            "tool": "search_web",
                            "step_id": "tool-search_web-1",
                            "success": True,
                            "result_envelope": {"success": True},
                        }
                    ),
                ),
                AgentRunEvent(
                    run_id=run_id,
                    seq=3,
                    type="tool_start",
                    payload_json=json.dumps(
                        {"tool": "get_company_profile", "step_id": "tool-get_company_profile-1"}
                    ),
                ),
                AgentRunEvent(
                    run_id=run_id,
                    seq=4,
                    type="tool_end",
                    payload_json=json.dumps(
                        {
                            "tool": "get_company_profile",
                            "step_id": "tool-get_company_profile-1",
                            "success": False,
                            "result_envelope": {"success": False, "error": "FMP unavailable"},
                        }
                    ),
                ),
            ]
        )
        db.commit()

    res = client.get(f"/api/runs/{run_id}/export")
    assert res.status_code == 200
    bundle = res.json()

    tool_events = [
        evt
        for evt in bundle["event_timeline"]
        if evt["type"] in {"tool_start", "tool_end"} and isinstance(evt.get("payload"), dict)
    ]
    sequence = [
        (evt["type"], evt["payload"]["tool"], evt["payload"].get("success"))
        for evt in tool_events
    ]

    # Replay view of the run: ordered tool lifecycle derived purely from the bundle
    assert sequence == [
        ("tool_start", "search_web", None),
        ("tool_end", "search_web", True),
        ("tool_start", "get_company_profile", None),
        ("tool_end", "get_company_profile", False),
    ]
