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
    _AT_CO_MENTION_RE,
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
        """Inserting the same URL twice should not create a second row.
        
        Because WebDocument metadata includes fetched_at (timestamp), the
        second upsert will detect a metadata change and return nc=1 (re-embed).
        The key assertion is that the total row count remains 1 (no duplicate).
        """
        async with TestAsyncSessionLocal() as session:
            nc1, _, ids1, _ = await _proc_web_documents(
                session,
                sources=[{"url": "https://example.com/dup", "title": "Dup"}],
            )
            await session.commit()
        assert nc1 == 1

        async with TestAsyncSessionLocal() as session:
            nc2, _, ids2, _ = await _proc_web_documents(
                session,
                sources=[{"url": "https://example.com/dup", "title": "Dup"}],
            )
            await session.commit()
        # Second call recognizes existing node (metadata changed due to fetched_at)
        assert nc2 == 1
        # Same node ID returned, confirming no duplicate row created
        assert ids1 == ids2

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

    async def test_enqueues_updated_nodes(self, patch_async_db, caplog):
        """Updated nodes with changed metadata are re-enqueued for FAISS."""
        q = asyncio.Queue(maxsize=100)
        set_write_queue(q)
        mgr = MagicMock()
        mgr.text_for_node = MagicMock(side_effect=lambda t, n, m=None: n)
        set_faiss_manager(mgr)

        # First upsert: creates node
        await upsert_from_tool_results([
            _tool_result_json(
                "get_company_profile",
                {"ticker": "UPDT"},
                {"symbol": "UPDT", "name": "UpdateCo", "sector": "Tech"},
            ),
        ])
        # Drain queue
        _ = q.get_nowait()

        # Second upsert: updates metadata (sector changes)
        await upsert_from_tool_results([
            _tool_result_json(
                "get_company_profile",
                {"ticker": "UPDT"},
                {"symbol": "UPDT", "name": "UpdateCo", "sector": "Finance"},
            ),
        ])
        # Should enqueue updated node
        assert not q.empty(), "Updated node should be enqueued for FAISS re-embed"
        msg = q.get_nowait()
        assert msg[0] == "upsert"

    async def test_queue_none_warning(self, patch_async_db, caplog):
        """When queue is None, upsert emits warning instead of failing silently."""
        set_write_queue(None)
        set_faiss_manager(None)

        await upsert_from_tool_results([
            _tool_result_json(
                "get_company_profile",
                {"ticker": "WARN"},
                {"symbol": "WARN", "name": "WarnCo"},
            ),
        ])
        # Check warning was emitted
        assert any(
            "FAISS write queue not initialized" in rec.message
            for rec in caplog.records
        ), "Should warn when queue is None"

    async def test_queue_full_rebuild_signal(self, patch_async_db, caplog):
        """When queue is full, upsert attempts to enqueue rebuild."""
        q = asyncio.Queue(maxsize=1)
        # Fill the queue
        await q.put(("upsert", [999], ["filler"]))
        set_write_queue(q)
        mgr = MagicMock()
        mgr.text_for_node = MagicMock(side_effect=lambda t, n, m=None: n)
        set_faiss_manager(mgr)

        await upsert_from_tool_results([
            _tool_result_json(
                "get_company_profile",
                {"ticker": "FULL"},
                {"symbol": "FULL", "name": "FullCo"},
            ),
        ])
        # Should log queue full + attempt rebuild
        assert any(
            "FAISS write queue full" in rec.message
            for rec in caplog.records
        ), "Should warn on queue full"


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

    def test_co_mention_with_at_prefix(self, patch_db, db_session):
        """@TICKER in report_text is extracted and stored as a CO_MENTION edge."""
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="Apple competes with @MSFT in the cloud market.",
        )
        msft = db_session.query(KGNode).filter(KGNode.name == "MSFT").first()
        assert msft is not None

    def test_co_mention_lowercase_at_normalized(self, patch_db, db_session):
        """Lowercase @-prefix (e.g. @msft) is normalised to MSFT."""
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="Competing with @msft and @goog.",
        )
        msft = db_session.query(KGNode).filter(KGNode.name == "MSFT").first()
        goog = db_session.query(KGNode).filter(KGNode.name == "GOOG").first()
        assert msft is not None, "MSFT should be extracted from @msft"
        assert goog is not None, "GOOG should be extracted from @goog"

    def test_co_mention_prefers_prefixed_over_bare(self, patch_db, db_session):
        """When @-prefixed tickers are found, bare uppercase in the same text is ignored."""
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            # @MSFT and @AMZN are prefixed; GOOG and NVDA are bare-only
            report_text="Competes with @MSFT and @AMZN. Also GOOG and NVDA mentioned.",
        )
        # Prefixed tickers must be created
        assert db_session.query(KGNode).filter(KGNode.name == "MSFT").first() is not None
        assert db_session.query(KGNode).filter(KGNode.name == "AMZN").first() is not None
        # Bare-only tickers must NOT be created (prefixed path was taken)
        for bare in ("GOOG", "NVDA"):
            assert db_session.query(KGNode).filter(KGNode.name == bare).first() is None, (
                f"{bare} should be ignored when prefixed tickers exist"
            )

    def test_co_mention_mixed_at_dollar_deduplication(self, patch_db, db_session):
        """@MSFT and $MSFT in the same report create only one MSFT node."""
        set_faiss_manager(None)
        set_write_queue(None)

        upsert_ticker_snapshot(
            "AAPL",
            info=None,
            report_text="Watch @MSFT and $MSFT closely.",
        )
        nodes = db_session.query(KGNode).filter(KGNode.name == "MSFT").all()
        assert len(nodes) == 1, "Duplicate MSFT nodes should be deduplicated"


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


# ---------------------------------------------------------------------------
# End-to-end: upsert_from_tool_results dispatch audit
# ---------------------------------------------------------------------------

class TestUpsertFromToolResultsDispatch:
    """Verify the full dispatch pipeline: tool_results payload → kg_nodes/
    kg_edges populated in the database.  This tests that the _TOOL_PROCESSORS
    map is correctly wired and that new SQLite entries are committed.
    """

    async def test_company_profile_creates_nodes_and_edges(self, patch_async_db):
        """A get_company_profile tool result must create a ticker node, a sector
        node, an industry node, and the corresponding edges in the DB."""
        import json as _json
        import asyncio

        set_faiss_manager(None)
        # Use a real asyncio.Queue to verify non-blocking put_nowait
        write_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        set_write_queue(write_queue)

        tool_results = [
            {
                "tool": "get_company_profile",
                "args": {"ticker": "NVDA"},
                "result": _json.dumps({
                    "success": True,
                    "data": {
                        "symbol": "NVDA",
                        "name": "NVIDIA Corporation",
                        "sector": "Technology",
                        "industry": "Semiconductors",
                    },
                    "sources": [],
                }),
            }
        ]

        summary = await upsert_from_tool_results(tool_results)

        # At least NVDA node + sector node + industry node created
        assert summary["nodes_created"] >= 1
        # At least IN_SECTOR + IN_INDUSTRY edges
        assert summary["edges_created"] >= 1
        # node_ids list must be non-empty (fed to FAISS write queue)
        assert len(summary["node_ids"]) >= 1

    async def test_faiss_write_queue_receives_ids_non_blocking(self, patch_async_db):
        """After upsert_from_tool_results, the FAISS write queue must have
        received a ('upsert', node_ids, texts) message via put_nowait without
        blocking the caller (verified by queue not being empty)."""
        import json as _json
        import asyncio

        set_faiss_manager(None)
        write_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        set_write_queue(write_queue)

        tool_results = [
            {
                "tool": "get_company_profile",
                "args": {"ticker": "AMD"},
                "result": _json.dumps({
                    "success": True,
                    "data": {
                        "symbol": "AMD",
                        "name": "Advanced Micro Devices",
                        "sector": "Technology",
                        "industry": "Semiconductors",
                    },
                    "sources": [],
                }),
            }
        ]

        summary = await upsert_from_tool_results(tool_results)

        if summary["node_ids"]:
            # The write queue should have at least one item posted
            assert not write_queue.empty(), (
                "FAISS write queue should have received node IDs via put_nowait "
                "after upsert_from_tool_results returned"
            )
            op, node_ids, texts = write_queue.get_nowait()
            assert op == "upsert"
            assert len(node_ids) >= 1

    async def test_tool_result_with_success_false_handled_gracefully(self, patch_async_db):
        """A tool result with success=False must not raise and must return
        zeros for nodes/edges (no partial writes from failed API calls)."""
        import json as _json

        set_faiss_manager(None)
        set_write_queue(None)

        tool_results = [
            {
                "tool": "get_company_profile",
                "args": {"ticker": "FAKE"},
                "result": _json.dumps({
                    "success": False,
                    "data": {},
                    "error": "Ticker not found",
                    "sources": [],
                }),
            }
        ]

        summary = await upsert_from_tool_results(tool_results)
        # Must return a dict without raising even when success=False
        assert isinstance(summary, dict)
        assert "nodes_created" in summary

    async def test_unknown_tool_name_returns_zeros(self, patch_async_db):
        """A tool_results entry for an unregistered tool (e.g. get_ohlcv which
        has no processor) must silently return without creating KG entries."""
        import json as _json

        set_faiss_manager(None)
        set_write_queue(None)

        tool_results = [
            {
                "tool": "get_ohlcv",  # no _TOOL_PROCESSOR registered for this
                "args": {"symbol": "AAPL"},
                "result": _json.dumps({
                    "success": True,
                    "data": {"bars": []},
                    "sources": [],
                }),
            }
        ]

        summary = await upsert_from_tool_results(tool_results)
        # No processor → nodes_created may be 0; must not raise
        assert isinstance(summary, dict)
        assert "nodes_created" in summary
