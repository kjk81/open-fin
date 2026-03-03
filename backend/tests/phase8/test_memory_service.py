from __future__ import annotations

from datetime import datetime

import pytest

from memory_service import (
    MemoryValidationError,
    create_portfolio_snapshot,
    get_portfolio_snapshot_by_run_id,
    list_research_library_by_run_id,
    create_research_library_entry,
    upsert_user_preference,
    get_user_preference,
)
from models import AgentRun


def _seed_run(db_session, run_id: str = "123e4567-e89b-12d3-a456-426614174000") -> AgentRun:
    run = AgentRun(
        id=run_id,
        session_id="session-memory-test",
        mode="agent",
        status="success",
        started_at=datetime.utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    return run


def _citations() -> list[dict[str, str]]:
    return [
        {
            "title": "FMP Company Profile",
            "url": "https://example.test/fmp/aapl",
            "tool": "finance",
            "accessed_at": "2026-03-03T12:00:00Z",
        }
    ]


class TestMemoryServiceRunIdTraceability:
    def test_manual_entry_create_and_retrieve_by_run_id(self, db_session):
        run = _seed_run(db_session)

        created = create_portfolio_snapshot(
            run_id=run.id,
            category="analysis_snapshot",
            content={"symbols": ["AAPL", "MSFT"], "thesis": "quality growth"},
            citations=_citations(),
            tags=["long-term", "quality"],
            confidence=0.83,
            db=db_session,
        )

        fetched = get_portfolio_snapshot_by_run_id(run.id, db=db_session)

        assert created["run_id"] == run.id
        assert fetched is not None
        assert fetched["run_id"] == run.id
        assert fetched["category"] == "analysis_snapshot"
        assert fetched["content"]["symbols"] == ["AAPL", "MSFT"]
        assert fetched["citations"][0]["tool"] == "finance"


class TestMemoryServiceCitationsValidation:
    def test_rejects_missing_required_citation_keys(self, db_session):
        run = _seed_run(db_session, run_id="123e4567-e89b-12d3-a456-426614174001")

        with pytest.raises(MemoryValidationError):
            create_portfolio_snapshot(
                run_id=run.id,
                category="analysis_snapshot",
                content={"symbols": ["AAPL"]},
                citations=[{"title": "missing-url"}],
                db=db_session,
            )


class TestUserPreferencesAndResearchLibrary:
    def test_user_preferences_upsert_by_category(self, db_session):
        run1 = _seed_run(db_session, run_id="123e4567-e89b-12d3-a456-426614174010")
        run2 = _seed_run(db_session, run_id="123e4567-e89b-12d3-a456-426614174011")

        first = upsert_user_preference(
            run_id=run1.id,
            category="preferred_currency",
            content={"value": "USD"},
            citations=_citations(),
            tags=["settings"],
            db=db_session,
        )
        second = upsert_user_preference(
            run_id=run2.id,
            category="preferred_currency",
            content={"value": "EUR"},
            citations=_citations(),
            tags=["settings"],
            db=db_session,
        )

        fetched = get_user_preference("preferred_currency", db=db_session)

        assert first["id"] == second["id"]
        assert fetched is not None
        assert fetched["run_id"] == run2.id
        assert fetched["content"]["value"] == "EUR"

    def test_research_library_many_per_run(self, db_session):
        run = _seed_run(db_session, run_id="123e4567-e89b-12d3-a456-426614174020")

        create_research_library_entry(
            run_id=run.id,
            category="dcf",
            content={"ticker": "AAPL", "intrinsic_value": 235.2},
            citations=_citations(),
            tags=["valuation"],
            db=db_session,
        )
        create_research_library_entry(
            run_id=run.id,
            category="peers",
            content={"ticker": "AAPL", "peer_count": 6},
            citations=_citations(),
            tags=["comparables"],
            db=db_session,
        )

        rows = list_research_library_by_run_id(run.id, db=db_session)

        assert len(rows) == 2
        assert {row["category"] for row in rows} == {"dcf", "peers"}
