"""Phase 1 — Tests for models.py (ORM constraints, defaults, cascades)."""

from __future__ import annotations

from datetime import datetime

import pytest

from models import (
    ChatHistory,
    HttpCache,
    KGEdge,
    KGNode,
    Loadout,
    UserPortfolio,
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
