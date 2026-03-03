from datetime import datetime
from sqlalchemy import String, Float, DateTime, Text, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    qty: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(20))  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReportCache(Base):
    __tablename__ = "report_cache"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    report_text: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TickerNote(Base):
    __tablename__ = "ticker_notes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class LLMSettings(Base):
    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="cloud")
    fallback_order_json: Mapped[str] = mapped_column(Text)
    subagent_fallback_order_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Loadout(Base):
    __tablename__ = "loadouts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str] = mapped_column(String(100))
    schedule: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")
    max_qty: Mapped[int] = mapped_column(Integer, default=100)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LoadoutExecution(Base):
    __tablename__ = "loadout_executions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    loadout_id: Mapped[int] = mapped_column(ForeignKey("loadouts.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    action: Mapped[str] = mapped_column(String(10))
    ticker: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    error_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class WorkerStatus(Base):
    __tablename__ = "worker_status"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    worker_id: Mapped[str] = mapped_column(String(64), unique=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pid: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running")


class KGNode(Base):
    """Persistent knowledge graph node; `id` doubles as the FAISS vector ID."""

    __tablename__ = "kg_nodes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    node_type: Mapped[str] = mapped_column(String(20), index=True)  # "ticker" | "sector" | "industry"
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)  # e.g. "AAPL", "sector:Technology"
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KGEdge(Base):
    """Persistent knowledge graph edge between two KGNodes."""

    __tablename__ = "kg_edges"
    __table_args__ = (
        # Prevent duplicate edges between the same pair of nodes
        # with the same relationship type.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"), index=True
    )
    relationship: Mapped[str] = mapped_column(String(30), index=True)  # "IN_SECTOR" | "IN_INDUSTRY" | "CO_MENTION"
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class HttpCache(Base):
    """HTTP response cache; keyed by URL.  TTL is advisory — callers must check
    ``fetched_at + ttl_seconds`` before trusting a cached entry."""

    __tablename__ = "http_cache"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    response_text: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=3600)


class Source(Base):
    """Provenance record written by agent tools when they fetch external data."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    url: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(String(500))
    tool: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AnomalyAlert(Base):
    """Persisted anomaly alert generated by the background anomaly worker."""

    __tablename__ = "anomaly_alerts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    signal_type: Mapped[str] = mapped_column(String(30))  # "price_drop" | "volume_spike" | "gap_down"
    magnitude: Mapped[float] = mapped_column(Float)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    researched: Mapped[bool] = mapped_column(Boolean, default=False)
    research_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class SchemaVersion(Base):
    """Single-row table tracking the applied schema migration version."""

    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=0)
    migrated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AnalysisSectionCache(Base):
    """Per-ticker, per-section cache for analysis panel data."""

    __tablename__ = "analysis_section_cache"
    __table_args__ = (
        UniqueConstraint("ticker", "section", name="uq_analysis_ticker_section"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    section: Mapped[str] = mapped_column(String(30))  # "fundamentals" | "sentiment" | "technical"
    content: Mapped[str] = mapped_column(Text)
    rating: Mapped[str] = mapped_column(String(30), default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(20), default="llm")  # "llm" | "kg" | "cache"
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=14400)  # 4h for technical, 24h for others
