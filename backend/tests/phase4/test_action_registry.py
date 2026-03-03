"""Phase 4 — Rollback hint coverage tests for action_registry."""

from __future__ import annotations

from agent.action_registry import build_rollback_hint


class TestRollbackHintsKG:
    def test_confirm_memory_write_confirm(self):
        hint = build_rollback_hint(
            "confirm_memory_write",
            {"proposal_id": "abc123", "decision": "confirm"},
        )
        assert "discard" in hint
        assert "abc123" in hint

    def test_add_kg_node(self):
        hint = build_rollback_hint("add_kg_node", {"name": "Test Entity"})
        assert "delete_kg_node" in hint
        assert "Test Entity" in hint


class TestRollbackHintsPortfolio:
    def test_execute_trade_buy(self):
        hint = build_rollback_hint(
            "execute_trade",
            {"action": "BUY", "ticker": "AAPL", "qty": 10},
        )
        # Should suggest either cancelling or reversing the trade.
        assert "cancel_order" in hint or "execute_trade" in hint
        assert "AAPL" in hint

    def test_add_to_portfolio(self):
        hint = build_rollback_hint(
            "add_to_portfolio",
            {"ticker": "MSFT"},
        )
        assert "remove_from_portfolio" in hint
        assert "MSFT" in hint


class TestRollbackHintsAdminAndStrategy:
    def test_reset_session(self):
        hint = build_rollback_hint("reset_session", {})
        assert "No automated rollback" in hint

    def test_clear_memory(self):
        hint = build_rollback_hint("clear_memory", {})
        assert "manual database restore" in hint

