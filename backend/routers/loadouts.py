import json
from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from apscheduler.triggers.cron import CronTrigger

from database import get_db, Base, engine
from models import Loadout, LoadoutExecution, WorkerStatus
from strategies import REGISTRY

router = APIRouter()
TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")
# Cron: 5 space-separated fields, each containing digits, *, -, /, or comma.
_CRON_RE = re.compile(
    r"^[\d*/,\-]+(?:\s+[\d*/,\-]+){4}$"
)


class LoadoutCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    strategy_name: str = Field(..., min_length=1, max_length=100)
    schedule: str = Field(..., min_length=1, max_length=100)
    parameters: dict = Field(default_factory=dict)
    max_qty: int = Field(default=100, ge=1, le=1_000_000)
    dry_run: bool = True

    @field_validator("schedule")
    @classmethod
    def validate_cron_schedule(cls, v: str) -> str:
        schedule = v.strip()
        if not _CRON_RE.match(schedule):
            raise ValueError(
                f"Invalid cron schedule: expected 5 space-separated fields, got {schedule!r}"
            )
        try:
            CronTrigger.from_crontab(schedule)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid cron schedule: {exc}") from exc
        return schedule


class LoadoutUpdate(BaseModel):
    ticker: str | None = Field(default=None, min_length=1, max_length=15)
    strategy_name: str | None = Field(default=None, min_length=1, max_length=100)
    schedule: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    parameters: dict | None = None
    max_qty: int | None = Field(default=None, ge=1, le=1_000_000)
    dry_run: bool | None = None


class StrategyInfo(BaseModel):
    name: str


def _normalize_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(status_code=422, detail="Invalid ticker format")
    return ticker


def _validate_schedule(raw: str) -> str:
    schedule = raw.strip()
    if not _CRON_RE.match(schedule):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid cron schedule: expected 5 space-separated fields, got {schedule!r}",
        )
    try:
        CronTrigger.from_crontab(schedule)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid cron schedule: {exc}") from exc
    return schedule


def _serialize_loadout(loadout: Loadout) -> dict:
    try:
        params = json.loads(loadout.parameters or "{}")
    except json.JSONDecodeError:
        params = {}
    return {
        "id": loadout.id,
        "ticker": loadout.ticker,
        "strategy_name": loadout.strategy_name,
        "schedule": loadout.schedule,
        "is_active": bool(loadout.is_active),
        "parameters": params,
        "max_qty": int(loadout.max_qty),
        "dry_run": bool(loadout.dry_run),
        "created_at": loadout.created_at.isoformat() if loadout.created_at else None,
        "updated_at": loadout.updated_at.isoformat() if loadout.updated_at else None,
    }


def _serialize_execution(execution: LoadoutExecution) -> dict:
    return {
        "id": execution.id,
        "loadout_id": execution.loadout_id,
        "timestamp": execution.timestamp.isoformat() if execution.timestamp else None,
        "action": execution.action,
        "ticker": execution.ticker,
        "quantity": execution.quantity,
        "confidence": execution.confidence,
        "status": execution.status,
        "dry_run": bool(execution.dry_run),
        "error_trace": execution.error_trace,
        "order_id": execution.order_id,
    }


def _ensure_tables() -> None:
    Base.metadata.create_all(bind=engine)


@router.get("/loadouts")
def list_loadouts(db: Session = Depends(get_db)):
    _ensure_tables()
    loadouts = db.query(Loadout).order_by(Loadout.created_at.desc()).all()
    return [_serialize_loadout(loadout) for loadout in loadouts]


@router.post("/loadouts", status_code=201)
def create_loadout(payload: LoadoutCreate, db: Session = Depends(get_db)):
    _ensure_tables()
    ticker = _normalize_ticker(payload.ticker)
    schedule = _validate_schedule(payload.schedule)
    now = datetime.now(timezone.utc)

    loadout = Loadout(
        ticker=ticker,
        strategy_name=payload.strategy_name,
        schedule=schedule,
        is_active=False,
        parameters=json.dumps(payload.parameters or {}),
        max_qty=payload.max_qty,
        dry_run=bool(payload.dry_run),
        created_at=now,
        updated_at=now,
    )
    db.add(loadout)
    db.commit()
    db.refresh(loadout)
    return _serialize_loadout(loadout)


@router.get("/loadouts/{loadout_id}")
def get_loadout(loadout_id: int, db: Session = Depends(get_db)):
    _ensure_tables()
    loadout = db.query(Loadout).filter(Loadout.id == loadout_id).first()
    if not loadout:
        raise HTTPException(status_code=404, detail="Loadout not found")
    return _serialize_loadout(loadout)


@router.patch("/loadouts/{loadout_id}")
def update_loadout(loadout_id: int, payload: LoadoutUpdate, db: Session = Depends(get_db)):
    _ensure_tables()
    loadout = db.query(Loadout).filter(Loadout.id == loadout_id).first()
    if not loadout:
        raise HTTPException(status_code=404, detail="Loadout not found")

    updates = payload.model_dump(exclude_unset=True)
    if "ticker" in updates:
        loadout.ticker = _normalize_ticker(str(updates["ticker"]))
    if "strategy_name" in updates:
        loadout.strategy_name = str(updates["strategy_name"])
    if "schedule" in updates:
        loadout.schedule = _validate_schedule(str(updates["schedule"]))
    if "is_active" in updates:
        loadout.is_active = bool(updates["is_active"])
    if "parameters" in updates:
        loadout.parameters = json.dumps(updates["parameters"] or {})
    if "max_qty" in updates:
        loadout.max_qty = int(updates["max_qty"])
    if "dry_run" in updates:
        loadout.dry_run = bool(updates["dry_run"])

    loadout.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(loadout)
    return _serialize_loadout(loadout)


@router.delete("/loadouts/{loadout_id}", status_code=204)
def delete_loadout(loadout_id: int, db: Session = Depends(get_db)):
    _ensure_tables()
    loadout = db.query(Loadout).filter(Loadout.id == loadout_id).first()
    if not loadout:
        raise HTTPException(status_code=404, detail="Loadout not found")

    db.query(LoadoutExecution).filter(LoadoutExecution.loadout_id == loadout_id).delete()
    db.delete(loadout)
    db.commit()


@router.get("/loadouts/{loadout_id}/executions")
def list_loadout_executions(
    loadout_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _ensure_tables()
    loadout = db.query(Loadout).filter(Loadout.id == loadout_id).first()
    if not loadout:
        raise HTTPException(status_code=404, detail="Loadout not found")

    query = db.query(LoadoutExecution).filter(LoadoutExecution.loadout_id == loadout_id)
    total = query.count()
    items = query.order_by(LoadoutExecution.timestamp.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [_serialize_execution(item) for item in items],
    }


@router.get("/worker/status")
def get_worker_status(db: Session = Depends(get_db)):
    _ensure_tables()
    try:
        row = db.query(WorkerStatus).order_by(WorkerStatus.last_heartbeat.desc()).first()
    except OperationalError:
        return {"online": False, "status": "offline", "stale": True, "last_heartbeat": None, "worker_id": None}
    if not row:
        return {"online": False, "status": "offline", "stale": True, "last_heartbeat": None, "worker_id": None}

    heartbeat = row.last_heartbeat
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)

    stale = (datetime.now(timezone.utc) - heartbeat).total_seconds() > 90
    online = row.status == "running" and not stale
    return {
        "online": online,
        "status": row.status,
        "stale": stale,
        "last_heartbeat": row.last_heartbeat.isoformat() if row.last_heartbeat else None,
        "worker_id": row.worker_id,
        "pid": row.pid,
    }


@router.get("/strategies", response_model=list[StrategyInfo])
def list_strategies():
    return [{"name": name} for name in sorted(REGISTRY.keys())]
