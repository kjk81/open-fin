"""KG-oriented entity schemas that map cleanly to KGNode / KGEdge SQLAlchemy models.

Each entity exposes:
  - ``to_kg_node_kwargs()`` → dict ready for ``KGNode(**kwargs)`` insertion.
  - ``from_kg_node(node)``  → classmethod to deserialise a ``KGNode`` row back
                              into the typed entity.

The ``metadata_json`` field on KGNode stores the entity's dict representation
(via ``model_dump_json()``), keeping the FAISS embedding text in sync with the
structured data.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, HttpUrl

import logging

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from models import KGNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_text(*parts: str | None) -> str:
    """Join non-empty string parts into a single embedding-friendly sentence."""
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Entity models
# ---------------------------------------------------------------------------

class Company(BaseModel):
    """A legal entity / issuer."""

    name: str
    ticker: str | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None

    # -- KGNode mapping -------------------------------------------------------

    def to_kg_node_kwargs(self) -> dict[str, Any]:
        return {
            "node_type": "company",
            "name": self.ticker if self.ticker else f"company:{self.name}",
            "metadata_json": self.model_dump_json(),
        }

    @classmethod
    def from_kg_node(cls, node: "KGNode") -> "Company":
        try:
            data = json.loads(node.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            _logger.warning("Corrupt metadata_json on KGNode %s: %s", node.name, exc)
            data = {}
        return cls(**data)

    def embedding_text(self) -> str:
        return _node_text(
            self.name, self.ticker, self.sector, self.industry, self.description
        )


class Security(BaseModel):
    """A tradeable instrument (equity, ETF, option, etc.)."""

    ticker: str
    exchange: str | None = None
    security_type: str = "equity"  # equity | etf | option | future
    company_name: str | None = None

    def to_kg_node_kwargs(self) -> dict[str, Any]:
        return {
            "node_type": "security",
            "name": self.ticker,
            "metadata_json": self.model_dump_json(),
        }

    @classmethod
    def from_kg_node(cls, node: "KGNode") -> "Security":
        try:
            data = json.loads(node.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            _logger.warning("Corrupt metadata_json on KGNode %s: %s", node.name, exc)
            data = {}
        return cls(**data)

    def embedding_text(self) -> str:
        return _node_text(
            self.ticker, self.company_name, self.security_type, self.exchange
        )


class FilingMetadata(BaseModel):
    """SEC or regulatory filing record."""

    filing_type: str                  # 10-K | 10-Q | 8-K | DEF 14A …
    filed_date: date
    company_ticker: str
    period_end: date | None = None
    url: HttpUrl | None = None

    def to_kg_node_kwargs(self) -> dict[str, Any]:
        node_name = (
            f"filing:{self.company_ticker}:{self.filing_type}:{self.filed_date}"
        )
        return {
            "node_type": "filing",
            "name": node_name,
            "metadata_json": self.model_dump_json(),
        }

    @classmethod
    def from_kg_node(cls, node: "KGNode") -> "FilingMetadata":
        try:
            data = json.loads(node.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            _logger.warning("Corrupt metadata_json on KGNode %s: %s", node.name, exc)
            data = {}
        return cls(**data)

    def embedding_text(self) -> str:
        return _node_text(
            self.company_ticker,
            self.filing_type,
            str(self.filed_date),
            str(self.period_end) if self.period_end else None,
        )


class WebDocument(BaseModel):
    """A fetched web page or article."""

    url: HttpUrl
    title: str
    snippet: str | None = None
    fetched_at: datetime

    def to_kg_node_kwargs(self) -> dict[str, Any]:
        return {
            "node_type": "web_document",
            "name": f"doc:{str(self.url)}",
            "metadata_json": self.model_dump_json(),
        }

    @classmethod
    def from_kg_node(cls, node: "KGNode") -> "WebDocument":
        try:
            data = json.loads(node.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            _logger.warning("Corrupt metadata_json on KGNode %s: %s", node.name, exc)
            data = {}
        return cls(**data)

    def embedding_text(self) -> str:
        return _node_text(self.title, self.snippet)


class MetricObservation(BaseModel):
    """A single numeric measurement for a security or company."""

    metric_name: str          # revenue | pe_ratio | eps | market_cap …
    value: float
    unit: str | None = None   # USD | % | x …
    observed_at: date
    source_ticker: str | None = None

    def to_kg_node_kwargs(self) -> dict[str, Any]:
        node_name = (
            f"metric:{self.source_ticker or 'unknown'}:"
            f"{self.metric_name}:{self.observed_at}"
        )
        return {
            "node_type": "metric",
            "name": node_name,
            "metadata_json": self.model_dump_json(),
        }

    @classmethod
    def from_kg_node(cls, node: "KGNode") -> "MetricObservation":
        try:
            data = json.loads(node.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            _logger.warning("Corrupt metadata_json on KGNode %s: %s", node.name, exc)
            data = {}
        return cls(**data)

    def embedding_text(self) -> str:
        unit_str = f" {self.unit}" if self.unit else ""
        return _node_text(
            self.source_ticker,
            self.metric_name,
            f"{self.value}{unit_str}",
            str(self.observed_at),
        )
