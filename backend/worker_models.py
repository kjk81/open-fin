from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
