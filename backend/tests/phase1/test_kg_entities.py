"""Phase 1 — Tests for schemas/kg_entities.py round-trip (schema ↔ KGNode)."""

from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from schemas.kg_entities import (
    Company,
    FilingMetadata,
    MetricObservation,
    Security,
    WebDocument,
)


def _mock_node(metadata_json: str, name: str = "test") -> MagicMock:
    """Create a mock KGNode with the given metadata_json."""
    node = MagicMock()
    node.metadata_json = metadata_json
    node.name = name
    return node


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

class TestCompany:
    def test_roundtrip(self):
        c = Company(name="Apple Inc", ticker="AAPL", sector="Technology", industry="Consumer Electronics")
        kw = c.to_kg_node_kwargs()
        assert kw["node_type"] == "company"
        assert kw["name"] == "AAPL"

        node = _mock_node(kw["metadata_json"], kw["name"])
        restored = Company.from_kg_node(node)
        assert restored.ticker == "AAPL"
        assert restored.sector == "Technology"

    def test_embedding_text_non_empty(self):
        c = Company(name="Apple Inc", ticker="AAPL")
        assert len(c.embedding_text()) > 0

    def test_no_ticker_uses_company_prefix(self):
        c = Company(name="Acme Corp")
        kw = c.to_kg_node_kwargs()
        assert kw["name"] == "company:Acme Corp"

    def test_corrupt_metadata_returns_defaults(self):
        node = _mock_node("{invalid json", "AAPL")
        # Company has required field 'name', will raise if no name key
        # But the try/except sets data={} which will fail validation
        with pytest.raises(Exception):
            Company.from_kg_node(node)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_roundtrip(self):
        s = Security(ticker="MSFT", exchange="NASDAQ", security_type="equity", company_name="Microsoft")
        kw = s.to_kg_node_kwargs()
        assert kw["name"] == "MSFT"

        node = _mock_node(kw["metadata_json"], kw["name"])
        restored = Security.from_kg_node(node)
        assert restored.exchange == "NASDAQ"

    def test_embedding_text(self):
        s = Security(ticker="MSFT", company_name="Microsoft")
        text = s.embedding_text()
        assert "MSFT" in text
        assert "Microsoft" in text


# ---------------------------------------------------------------------------
# FilingMetadata
# ---------------------------------------------------------------------------

class TestFilingMetadata:
    def test_roundtrip(self):
        f = FilingMetadata(
            filing_type="10-K",
            filed_date=date(2024, 12, 1),
            company_ticker="AAPL",
            url="https://sec.gov/filing/123",
        )
        kw = f.to_kg_node_kwargs()
        assert "filing:" in kw["name"]

        node = _mock_node(kw["metadata_json"], kw["name"])
        restored = FilingMetadata.from_kg_node(node)
        assert restored.filing_type == "10-K"
        assert restored.filed_date == date(2024, 12, 1)

    def test_embedding_text(self):
        f = FilingMetadata(
            filing_type="10-Q",
            filed_date=date(2024, 6, 15),
            company_ticker="NVDA",
        )
        text = f.embedding_text()
        assert "NVDA" in text
        assert "10-Q" in text


# ---------------------------------------------------------------------------
# WebDocument
# ---------------------------------------------------------------------------

class TestWebDocument:
    def test_roundtrip(self):
        d = WebDocument(
            url="https://example.com/article",
            title="Great Article",
            snippet="Some snippet",
            fetched_at=datetime(2025, 1, 1),
        )
        kw = d.to_kg_node_kwargs()
        assert kw["node_type"] == "web_document"

        node = _mock_node(kw["metadata_json"], kw["name"])
        restored = WebDocument.from_kg_node(node)
        assert restored.title == "Great Article"

    def test_embedding_text(self):
        d = WebDocument(
            url="https://example.com",
            title="Title",
            snippet="Snippet text",
            fetched_at=datetime(2025, 1, 1),
        )
        text = d.embedding_text()
        assert "Title" in text


# ---------------------------------------------------------------------------
# MetricObservation
# ---------------------------------------------------------------------------

class TestMetricObservation:
    def test_roundtrip(self):
        m = MetricObservation(
            metric_name="revenue",
            value=394_328_000_000,
            unit="USD",
            observed_at=date(2024, 9, 28),
            source_ticker="AAPL",
        )
        kw = m.to_kg_node_kwargs()
        assert "metric:" in kw["name"]

        node = _mock_node(kw["metadata_json"], kw["name"])
        restored = MetricObservation.from_kg_node(node)
        assert restored.value == 394_328_000_000
        assert restored.unit == "USD"

    def test_embedding_text_includes_unit(self):
        m = MetricObservation(
            metric_name="eps",
            value=6.42,
            unit="USD",
            observed_at=date(2024, 9, 28),
            source_ticker="AAPL",
        )
        text = m.embedding_text()
        assert "USD" in text
        assert "eps" in text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_metadata_json(self):
        node = _mock_node("", "test")
        # Each entity should handle empty string gracefully (fallback to {})
        with pytest.raises(Exception):
            Company.from_kg_node(node)

    def test_null_metadata_json(self):
        node = _mock_node(None, "test")
        # metadata_json=None should also yield {}
        with pytest.raises(Exception):
            Company.from_kg_node(node)
