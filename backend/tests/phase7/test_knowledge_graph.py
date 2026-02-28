"""Tests for agent/knowledge_graph.py — processors, co-mention, dispatch."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    TestAsyncSessionLocal,
    make_kg_node,
    make_tool_result,
)

# Import after conftest stubs heavy modules
from agent.knowledge_graph import (
    _aupsert_edge,
    _aupsert_node,
    _CO_MENTION_STOPWORDS,
    _DOLLAR_TICKER_RE,
    _BARE_TICKER_RE,
    _proc_company_profile,
    _proc_peers,
    _proc_financial_statements,
    _proc_balance_sheet,
    _proc_technical_snapshot,
    _proc_filings_metadata,
    _proc_web_documents,
    _proc_screen_stocks,
    _parse_date,
    upsert_from_tool_results,
    upsert_ticker_snapshot,
    set_faiss_manager,
    set_write_queue,
)
from models import KGNode, KGEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_result_json(tool: str, args: dict, data, *, success: bool = True, sources=None):
    """Build a tool_results entry dict matching what chat.py accumulates."""
    return {
        "tool": tool,
        "args": args,
        "result": json.dumps({
            "success": success,
            "data": data,
            "sources": sources or [],
        }),
    }


# ---------------------------------------------------------------------------
# Async processor tests (use real async DB session)
# ---------------------------------------------------------------------------

class TestProcCompanyProfile:
    async def test_creates_company_and_sector_nodes(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_company_profile(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "sector": "Technology",
                    "industry": "Consumer Electronics",
                },
            )
            await session.commit()

        assert nc >= 1  # at least the company node
        assert ec >= 1  # at least IN_SECTOR or IN_INDUSTRY
        assert len(ids) >= 1
        assert any("AAPL" in t or "Apple" in t for t in texts)

    async def test_creates_industry_edge(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_company_profile(
                session,
                args={},
                data={
                    "symbol": "MSFT",
                    "name": "Microsoft",
                    "sector": "Technology",
                    "industry": "Software",
                },
            )
            await session.commit()
        assert ec >= 2  # IN_SECTOR + IN_INDUSTRY

    async def test_empty_symbol_returns_zeros(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_company_profile(session, args={}, data={})
        assert nc == 0
        assert ec == 0

    async def test_no_sector_skips_sector_node(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_company_profile(
                session,
                args={"ticker": "XYZ"},
                data={"symbol": "XYZ", "name": "XYZ Corp"},
            )
            await session.commit()
        # No sector → 0 sector edges
        assert ec == 0


class TestProcPeers:
    async def test_creates_peer_edges(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_peers(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "peers": ["MSFT", "GOOG", "META"],
                    "sector": "Technology",
                },
            )
            await session.commit()
        assert ec == 3  # 3 PEER_OF edges
        assert nc >= 4  # primary + 3 peers

    async def test_empty_primary_returns_zeros(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_peers(session, args={}, data={})
        assert nc == 0

    async def test_empty_peers_list(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_peers(
                session,
                args={},
                data={"symbol": "AAPL", "peers": []},
            )
            await session.commit()
        assert ec == 0


class TestProcFinancialStatements:
    async def test_creates_metric_nodes(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_financial_statements(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "period": "2025-01-01",
                    "revenue": 100000000,
                    "net_income": 25000000,
                    "eps": 6.5,
                },
            )
            await session.commit()
        assert nc >= 3  # revenue, net_income, eps metric nodes
        assert ec >= 3  # OBSERVED_FOR edges

    async def test_non_numeric_metric_skipped(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_financial_statements(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "period": "2025-01-01",
                    "revenue": "N/A",
                },
            )
            await session.commit()
        # "N/A" should be skipped → 0 metric nodes for revenue
        metric_nodes = nc - 1  # minus the company node if it was new
        assert metric_nodes == 0

    async def test_list_input(self, patch_async_db):
        """Data can be a list of period rows."""
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_financial_statements(
                session,
                args={"ticker": "AAPL"},
                data=[
                    {"symbol": "AAPL", "period": "2025-01-01", "revenue": 100},
                    {"symbol": "AAPL", "period": "2024-01-01", "revenue": 90},
                ],
            )
            await session.commit()
        assert nc >= 2  # at least two revenue metric nodes


class TestProcBalanceSheet:
    async def test_creates_balance_sheet_metrics(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_balance_sheet(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "period": "2025-01-01",
                    "total_assets": 500000000,
                    "total_debt": 100000000,
                    "cash": 50000000,
                },
            )
            await session.commit()
        assert nc >= 3
        assert ec >= 3


class TestProcTechnicalSnapshot:
    async def test_creates_tech_metric_nodes(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_technical_snapshot(
                session,
                args={"ticker": "AAPL"},
                data={
                    "symbol": "AAPL",
                    "price": 185.50,
                    "rsi_14": 55.3,
                    "sma_20": 180.0,
                },
            )
            await session.commit()
        assert nc >= 3
        assert ec >= 3

    async def test_empty_symbol(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_technical_snapshot(
                session, args={}, data={},
            )
        assert nc == 0


class TestProcFilingsMetadata:
    async def test_creates_filing_nodes(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_filings_metadata(
                session,
                args={"ticker": "AAPL"},
                data=[
                    {
                        "company_ticker": "AAPL",
                        "filing_type": "10-K",
                        "filed_date": "2025-02-15",
                        "url": "https://sec.gov/...",
                    },
                    {
                        "company_ticker": "AAPL",
                        "filing_type": "10-Q",
                        "filed_date": "2025-01-15",
                    },
                ],
            )
            await session.commit()
        assert nc >= 2  # at least 2 filing nodes
        assert ec >= 2  # FILED_BY edges

    async def test_empty_ticker_skipped(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_filings_metadata(
                session, args={}, data=[{"filing_type": "10-K"}],
            )
        assert nc == 0


class TestProcWebDocuments:
    async def test_creates_web_nodes(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_web_documents(
                session,
                sources=[
                    {"url": "https://example.com/a", "title": "Article A"},
                    {"url": "https://example.com/b", "title": "Article B"},
                ],
            )
            await session.commit()
        assert nc == 2
        assert len(ids) == 2

    async def test_duplicate_urls_deduplicated(self, patch_async_db):
        """Inserting the same URL twice should only create one node."""
        async with TestAsyncSessionLocal() as session:
            nc1, _, _, _ = await _proc_web_documents(
                session,
                sources=[{"url": "https://example.com/dup", "title": "Dup"}],
            )
            nc2, _, _, _ = await _proc_web_documents(
                session,
                sources=[{"url": "https://example.com/dup", "title": "Dup Again"}],
            )
            await session.commit()
        assert nc1 == 1
        assert nc2 == 0  # already exists

    async def test_empty_url_skipped(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, _, _, _ = await _proc_web_documents(
                session, sources=[{"url": "", "title": "X"}],
            )
        assert nc == 0


class TestProcScreenStocks:
    async def test_creates_company_and_sector(self, patch_async_db):
        async with TestAsyncSessionLocal() as session:
            nc, ec, ids, texts = await _proc_screen_stocks(
                session,
                args={},
                data=[
                    {"symbol": "XYZ", "name": "XYZ Corp", "sector": "Health Care"},
                ],
            )
            await session.commit()
        assert nc >= 1
        # sector edge
        assert ec >= 1


# ---------------------------------------------------------------------------
# upsert_from_tool_results dispatch tests
# ---------------------------------------------------------------------------

class TestUpsertFromToolResults:
    async def test_dispatches_to_correct_processor(self, patch_async_db):
        results = [
            _tool_result_json(
                "get_company_profile",
                {"ticker": "AAPL"},
                {"symbol": "AAPL", "name": "Apple", "sector": "Technology"},
            ),
        ]
        # Patch FAISS queue to avoid side effects
        q = asyncio.Queue(maxsize=100)
        set_write_queue(q)
        set_faiss_manager(None)

        out = await upsert_from_tool_results(results)
        assert out["nodes_created"] >= 1

    async def test_unknown_tool_name_skipped(self, patch_async_db):
        results = [
            _tool_result_json("unknown_tool", {}, {"some": "data"}),
        ]
        set_write_queue(asyncio.Queue(maxsize=100))
        set_faiss_manager(None)

        out = await upsert_from_tool_results(results)
        assert out["nodes_created"] == 0

    async def test_failed_result_skipped(self, patch_async_db):
        results = [
            _tool_result_json(
                "get_company_profile",
                {"ticker": "AAPL"},
                {"symbol": "AAPL"},
                success=False,
            ),
        ]
        set_write_queue(asyncio.Queue(maxsize=100))
        set_faiss_manager(None)

        out = await upsert_from_tool_results(results)
        assert out["nodes_created"] == 0

    async def test_non_json_result_skipped(self, patch_async_db):
        results = [
            {"tool": "get_company_profile", "args": {}, "result": "not-json"},
        ]
        set_write_queue(asyncio.Queue(maxsize=100))
        set_faiss_manager(None)

        out = await upsert_from_tool_results(results)
        assert out["nodes_created"] == 0

    async def test_empty_tool_results(self, patch_async_db):
        set_write_queue(asyncio.Queue(maxsize=100))
        set_faiss_manager(None)

        out = await upsert_from_tool_results([])
        assert out == {"nodes_created": 0, "edges_created": 0, "node_ids": []}

    async def test_type_narrowing_skips_invalid_entries(self, patch_async_db):
        """Non-dict, missing 'tool' key, and non-str tool name are all skipped."""
        results = [
            None,  # type: ignore[list-item]
            {"no_tool_key": True},
            {"tool": 123},
            _tool_result_json(
                "get_company_profile",
                {"ticker": "OK"},
                {"symbol": "OK", "name": "OK Corp"},
            ),
        ]
        set_write_queue(asyncio.Queue(maxsize=100))
        set_faiss_manager(None)

        out = await upsert_from_tool_results(results)
        # Only the last valid entry should produce nodes
        assert out["nodes_created"] >= 1

    async def test_enqueues_faiss_vectors(self, patch_async_db):
        q = asyncio.Queue(maxsize=100)
        set_write_queue(q)
        mgr = MagicMock()
        mgr.text_for_node = MagicMock(side_effect=lambda t, n, m=None: n)
        set_faiss_manager(mgr)

        results = [
            _tool_result_json(
                "get_company_profile",
                {"ticker": "AAPL"},
                {"symbol": "AAPL", "name": "Apple"},
            ),
        ]
        await upsert_from_tool_results(results)

        assert not q.empty()
        msg = q.get_nowait()
        assert msg[0] == "upsert"
        assert len(msg[1]) >= 1  # node_ids


# ---------------------------------------------------------------------------
# Co-mention regex tests
# ---------------------------------------------------------------------------

class TestCoMentionExtraction:
    """Test the tightened co-mention regex patterns."""

    def test_dollar_prefix_found(self):
        matches = _DOLLAR_TICKER_RE.findall("Check $MSFT and $GOOG today")
        assert "MSFT" in matches
        assert "GOOG" in matches

    def test_bare_fallback(self):
        matches = _BARE_TICKER_RE.findall("Check MSFT and GOOG today")
        assert "MSFT" in matches
        assert "GOOG" in matches

    def test_common_words_in_stopwords(self):
        """Common English words like A, IT, ALL, THE should be in stopwords."""
        for word in ("A", "IT", "ALL", "THE", "AND", "FOR", "BUT", "YOU"):
            assert word in _CO_MENTION_STOPWORDS, f"{word} should be in stopwords"

    def test_dollar_preferred_over_bare(self):
        """When $-prefixed tickers exist, bare uppercase should not be used."""
        text = "Company THE said $AAPL is great and MSFT might follow."
        dollar = set(_DOLLAR_TICKER_RE.findall(text))
        assert dollar == {"AAPL"}
        # The actual upsert_ticker_snapshot uses dollar first, only falls back
        # to bare if none found — tested implicitly via the sync function.

    def test_bare_ticker_max_length_5(self):
        """Bare regex caps at 5 characters."""
        matches = _BARE_TICKER_RE.findall("ABCDEF should not match but ABCDE should")
        assert "ABCDE" in matches
        assert "ABCDEF" not in matches


class TestUpsertTickerSnapshot:
    """Tests for the sync legacy upsert path."""

    def test_basic_upsert(self, patch_db, db_session):
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info={"shortName": "Apple", "sector": "Technology", "industry": "Consumer Electronics"},
            report_text=None,
        )
        node = db_session.query(KGNode).filter(KGNode.name == "AAPL").first()
        assert node is not None
        assert node.node_type == "ticker"

    def test_co_mention_with_dollar_prefix(self, patch_db, db_session):
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="Apple competes with $MSFT and $GOOG in the market.",
        )
        msft = db_session.query(KGNode).filter(KGNode.name == "MSFT").first()
        assert msft is not None

    def test_stopwords_filtered(self, patch_db, db_session):
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="THE AND FOR BUT ALL are common words, not tickers.",
        )
        # None of these should have been created as ticker nodes
        for word in ("THE", "AND", "FOR", "BUT", "ALL"):
            node = db_session.query(KGNode).filter(KGNode.name == word).first()
            assert node is None, f"{word} should not be a KG node"

    def test_empty_symbol_noop(self, patch_db, db_session):
        set_faiss_manager(None)
        set_write_queue(None)
        upsert_ticker_snapshot("", info=None, report_text=None)
        assert db_session.query(KGNode).count() == 0

    def test_co_mention_bare_fallback(self, patch_db, db_session):
        """When no $-prefixed tickers are found, fall back to bare uppercase."""
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="MSFT and GOOG are peers.",
        )
        msft = db_session.query(KGNode).filter(KGNode.name == "MSFT").first()
        assert msft is not None


# ---------------------------------------------------------------------------
# _parse_date edge cases
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_string(self):
        assert _parse_date("2025-06-15") == date(2025, 6, 15)

    def test_date_object(self):
        d = date(2025, 1, 1)
        assert _parse_date(d) is d

    def test_invalid_falls_back(self):
        result = _parse_date("not-a-date")
        assert result == date.today()

    def test_long_string_truncated(self):
        assert _parse_date("2025-03-20T12:00:00Z") == date(2025, 3, 20)
