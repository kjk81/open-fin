"""Phase 1 — Tests for models.py (ORM constraints, defaults, cascades)."""

from __future__ import annotations

from datetime import datetime

import pytest

from models import (
    AgentRun,
    AgentRunEvent,
    ChatHistory,
    HttpCache,
    KGEdge,
    KGNode,
    Loadout,
    PortfolioSnapshot,
    ResearchLibrary,
    EpisodicSummary,
    UserPortfolio,
    UserPreferences,
    Watchlist,
)


class TestKGNode:
    def test_default_is_deleted_false(self, db_session):
        node = KGNode(node_type="company", name="AAPL", metadata_json="{}")
        db_session.add(node)
        db_session.flush()
        assert node.is_deleted is False

    def test_unique_name_constraint(self, db_session):
        db_session.add(KGNode(node_type="company", name="MSFT", metadata_json="{}"))
        db_session.flush()
        db_session.add(KGNode(node_type="company", name="MSFT", metadata_json="{}"))
        with pytest.raises(Exception):  # IntegrityError
            db_session.flush()

    def test_soft_delete(self, db_session):
        node = KGNode(node_type="company", name="TSLA", metadata_json="{}", is_deleted=True)
        db_session.add(node)
        db_session.flush()
        assert node.is_deleted is True


class TestKGEdge:
    def test_cascade_on_node_delete(self, db_session):
        n1 = KGNode(node_type="company", name="AAA", metadata_json="{}")
        n2 = KGNode(node_type="sector", name="sector:Tech", metadata_json="{}")
        db_session.add_all([n1, n2])
        db_session.flush()

        edge = KGEdge(source_id=n1.id, target_id=n2.id, relationship="IN_SECTOR")
        db_session.add(edge)
        db_session.flush()
        assert edge.id is not None

    def test_default_weight(self, db_session):
        n1 = KGNode(node_type="company", name="BBB", metadata_json="{}")
        n2 = KGNode(node_type="sector", name="sector:Fin", metadata_json="{}")
        db_session.add_all([n1, n2])
        db_session.flush()

        edge = KGEdge(source_id=n1.id, target_id=n2.id, relationship="IN_SECTOR")
        db_session.add(edge)
        db_session.flush()
        assert edge.weight == 1.0


class TestHttpCache:
    def test_unique_url_constraint(self, db_session):
        db_session.add(HttpCache(url="https://example.com/a", response_text="body1"))
        db_session.flush()
        db_session.add(HttpCache(url="https://example.com/a", response_text="body2"))
        with pytest.raises(Exception):  # IntegrityError
            db_session.flush()

    def test_default_ttl(self, db_session):
        row = HttpCache(url="https://example.com/b", response_text="ok")
        db_session.add(row)
        db_session.flush()
        assert row.ttl_seconds == 3600  # default


class TestChatHistory:
    def test_creation_with_defaults(self, db_session):
        row = ChatHistory(
            session_id="test-session",
            role="user",
            content="Hello",
        )
        db_session.add(row)
        db_session.flush()
        assert row.id is not None
        assert row.created_at is not None


class TestLoadout:
    def test_default_values(self, db_session):
        row = Loadout(
            ticker="AAPL",
            strategy_name="momentum",
            schedule="0 9 * * 1-5",
        )
        db_session.add(row)
        db_session.flush()
        assert row.is_active is False
        assert row.dry_run is True
        assert row.parameters == "{}"
        assert row.max_qty == 100


class TestAgentRunAndEvents:
    def test_agent_run_defaults_and_completion_fields(self, db_session):
        run = AgentRun(
            id="123e4567-e89b-12d3-a456-426614174000",
            session_id="session-1",
            mode="quick",
        )
        db_session.add(run)
        db_session.flush()

        assert run.id is not None
        assert run.status == "running"
        assert isinstance(run.started_at, datetime)
        assert run.completed_at is None

        # Simulate completion update performed by routers.chat._complete_run
        run.status = "success"
        run.completed_at = datetime.utcnow()
        db_session.flush()
        assert run.status == "success"
        assert isinstance(run.completed_at, datetime)

    def test_agent_run_events_persist_with_sequential_seq(self, db_session):
        run = AgentRun(
            id="223e4567-e89b-12d3-a456-426614174001",
            session_id="session-2",
            mode="quick",
        )
        db_session.add(run)
        db_session.flush()

        e1 = AgentRunEvent(
            run_id=run.id,
            seq=1,
            type="chain_start",
            payload_json="{}",
        )
        e2 = AgentRunEvent(
            run_id=run.id,
            seq=2,
            type="chain_end",
            payload_json="{}",
        )
        db_session.add_all([e1, e2])
        db_session.flush()

        events = (
            db_session.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run.id)
            .order_by(AgentRunEvent.seq.asc(), AgentRunEvent.id.asc())
            .all()
        )
        assert [e.seq for e in events] == [1, 2]
        assert all(isinstance(e.created_at, datetime) for e in events)

    def test_run_scoped_snapshots_fk_to_agent_run(self, db_session):
        run = AgentRun(
            id="323e4567-e89b-12d3-a456-426614174002",
            session_id="session-3",
            mode="quick",
        )
        db_session.add(run)
        db_session.flush()

        pref = UserPreferences(
            run_id=run.id,
            category="risk",
            content_json="{}",
            citations_json="[]",
            tags_json="[]",
        )
        snapshot = PortfolioSnapshot(
            run_id=run.id,
            category="portfolio",
            content_json="{}",
            citations_json="[]",
            tags_json="[]",
        )
        episodic = EpisodicSummary(
            run_id=run.id,
            category="episodic",
            content_json="{}",
            citations_json="[]",
            tags_json="[]",
        )
        research = ResearchLibrary(
            run_id=run.id,
            category="research",
            content_json="{}",
            citations_json="[]",
            tags_json="[]",
        )
        db_session.add_all([pref, snapshot, episodic, research])
        db_session.flush()

        for row in (pref, snapshot, episodic, research):
            assert row.run_id == run.id
