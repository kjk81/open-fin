import json
import logging
import os
import signal
import socket
import sys
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Literal

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from worker_db import SessionLocal, engine
from worker_models import Base, Loadout, LoadoutExecution, WorkerStatus


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
PROCESS_POOL = ProcessPoolExecutor(max_workers=2)
SCHEDULER = BlockingScheduler(timezone="UTC")
MAX_ERROR_TRACE_CHARS = 4000


class StrategyOutput(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    ticker: str = Field(pattern=r"^[A-Z]{1,10}$")
    qty: int = Field(ge=0, le=1_000_000)
    confidence: float = Field(ge=0.0, le=1.0)


class WorkerLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.handle = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.lock_path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                self.handle.write("0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self) -> None:
        if not self.handle:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


def run_strategy_subprocess(strategy_name: str, ticker: str, params: dict) -> dict:
    from strategies import REGISTRY

    strategy = REGISTRY.get(strategy_name)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    return strategy(ticker, params)


def _loadout_params(loadout: Loadout) -> dict:
    try:
        return json.loads(loadout.parameters or "{}")
    except json.JSONDecodeError:
        return {}


def _heartbeat(status: str = "running") -> None:
    db = SessionLocal()
    try:
        row = db.query(WorkerStatus).filter(WorkerStatus.worker_id == WORKER_ID).first()
        now = datetime.utcnow()
        if row is None:
            row = WorkerStatus(
                worker_id=WORKER_ID,
                started_at=now,
                last_heartbeat=now,
                pid=os.getpid(),
                status=status,
            )
            db.add(row)
        else:
            row.last_heartbeat = now
            row.status = status
            row.pid = os.getpid()
        db.commit()
    finally:
        db.close()


def _submit_alpaca_order(action: str, ticker: str, qty: int) -> tuple[str, str]:
    api_key = os.getenv("ALPACA_WORKER_API_KEY") or os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_WORKER_API_SECRET") or os.getenv("ALPACA_API_SECRET")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not api_secret:
        raise RuntimeError("Alpaca credentials are not configured for worker")

    import alpaca_trade_api as tradeapi

    api = tradeapi.REST(key_id=api_key, secret_key=api_secret, base_url=base_url)
    order = api.submit_order(
        symbol=ticker,
        qty=qty,
        side=action.lower(),
        type="market",
        time_in_force="day",
    )
    return str(order.id), str(order.status)


def _mark_execution_failed(db, execution: LoadoutExecution | None, exc: Exception) -> None:
    if execution is None:
        return
    execution.status = "failed"
    execution.error_trace = str(exc)[:MAX_ERROR_TRACE_CHARS]
    db.commit()


def execute_loadout(loadout_id: int) -> None:
    db = SessionLocal()
    execution: LoadoutExecution | None = None
    try:
        loadout = db.query(Loadout).filter(Loadout.id == loadout_id).first()
        if loadout is None or not loadout.is_active:
            return

        execution = LoadoutExecution(
            loadout_id=loadout.id,
            timestamp=datetime.utcnow(),
            action="HOLD",
            ticker=loadout.ticker.upper(),
            quantity=0,
            confidence=0.0,
            status="pending",
            dry_run=bool(loadout.dry_run),
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)

        params = _loadout_params(loadout)
        future = PROCESS_POOL.submit(
            run_strategy_subprocess,
            loadout.strategy_name,
            loadout.ticker.upper(),
            params,
        )

        try:
            raw_result = future.result(timeout=30)
            parsed = StrategyOutput.model_validate(raw_result)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError("Strategy execution timed out after 30s")

        expected_ticker = loadout.ticker.upper()
        parsed_ticker = parsed.ticker.upper()
        if parsed_ticker != expected_ticker:
            raise ValueError(
                f"Strategy returned ticker '{parsed_ticker}' but loadout is bound to '{expected_ticker}'"
            )

        qty = min(int(parsed.qty), int(loadout.max_qty))
        if parsed.action == "HOLD":
            qty = 0

        execution.action = parsed.action
        execution.ticker = parsed_ticker
        execution.quantity = qty
        execution.confidence = float(parsed.confidence)

        if loadout.dry_run or parsed.action == "HOLD" or qty <= 0:
            execution.status = "dry_run"
            execution.dry_run = True
            db.commit()
            return

        order_id, order_status = _submit_alpaca_order(parsed.action, parsed_ticker, qty)
        execution.order_id = order_id
        execution.status = "filled" if order_status in {"filled", "accepted", "new"} else order_status
        execution.dry_run = False
        db.commit()

    except (ValidationError, TimeoutError, Exception) as exc:
        logger.exception("Loadout execution failed for id=%s", loadout_id)
        try:
            _mark_execution_failed(db, execution, exc)
        except Exception:
            db.rollback()
    finally:
        db.close()


def sync_loadout_jobs() -> None:
    db = SessionLocal()
    try:
        active_ids: set[int] = set()
        active_loadouts = db.query(Loadout).filter(Loadout.is_active.is_(True)).all()

        for loadout in active_loadouts:
            job_id = f"loadout-{loadout.id}"
            active_ids.add(loadout.id)
            try:
                trigger = CronTrigger.from_crontab(loadout.schedule)
            except ValueError:
                logger.error("Invalid cron schedule for loadout %s: %s", loadout.id, loadout.schedule)
                continue

            SCHEDULER.add_job(
                execute_loadout,
                trigger=trigger,
                id=job_id,
                args=[loadout.id],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

        for job in SCHEDULER.get_jobs():
            if not job.id.startswith("loadout-"):
                continue
            loadout_id = int(job.id.split("-", 1)[1])
            if loadout_id not in active_ids:
                SCHEDULER.remove_job(job.id)

    finally:
        db.close()


def shutdown(*_) -> None:
    logger.info("Worker shutting down")
    try:
        _heartbeat(status="stopped")
    except Exception:
        pass
    PROCESS_POOL.shutdown(wait=False, cancel_futures=True)
    if SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)


def main() -> int:
    load_dotenv(dotenv_path=os.getenv("OPEN_FIN_ENV_PATH"), override=False)

    db_override = os.getenv("OPEN_FIN_DB_PATH")
    lock_dir = Path(db_override).expanduser().resolve().parent if db_override else Path.cwd()
    lock = WorkerLock(lock_dir / "open_fin_worker.lock")

    if not lock.acquire():
        logger.info("Another worker instance is already running")
        return 0

    try:
        Base.metadata.create_all(bind=engine)
        _heartbeat(status="running")

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        SCHEDULER.add_job(_heartbeat, "interval", seconds=30, id="heartbeat", replace_existing=True)
        SCHEDULER.add_job(sync_loadout_jobs, "interval", seconds=60, id="sync-loadouts", replace_existing=True)

        sync_loadout_jobs()
        logger.info("Worker started (worker_id=%s)", WORKER_ID)
        SCHEDULER.start()
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
