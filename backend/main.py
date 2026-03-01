import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env before anything else. In packaged builds, Electron can point
# OPEN_FIN_ENV_PATH at a user-writable location.
load_dotenv(dotenv_path=os.getenv("OPEN_FIN_ENV_PATH"), override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, inspect, select, text

from database import SessionLocal, engine
from models import Base, KGNode
from routers.portfolio import sync_alpaca_portfolio
from routers import alerts, chat, graph, loadouts, llm, portfolio, settings, ticker, trade, watchlist
from agent.llm import ensure_default_settings
from migrations import run_migrations, get_current_version, set_version, CURRENT_SCHEMA_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup status — populated during lifespan, read by /api/health
# ---------------------------------------------------------------------------

@dataclass
class StartupStatus:
    migration_ok: bool = True
    migration_error: str | None = None
    needs_wipe: bool = False


_startup_status: StartupStatus = StartupStatus()
_faiss_ready: bool = False


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _faiss_ready, _startup_status

    # ------------------------------------------------------------------ #
    # Step 1: Create any NEW tables (additive — does not touch existing)   #
    # ------------------------------------------------------------------ #
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------ #
    # Step 2: Detect fresh vs. legacy DB, then run migrations              #
    # ------------------------------------------------------------------ #
    current_version = get_current_version(engine)

    if current_version == 0:
        # schema_version table was just created by create_all — its row count
        # is always 0 here.  Use schema introspection to distinguish a legacy
        # database (llm_settings table already exists) from a fresh install
        # (no user-data tables yet).  Row-count heuristics are unreliable
        # because a new install may already have settings rows.
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())

        if "llm_settings" in existing_tables:
            # Existing database — run all migrations from v0 (they are
            # idempotent and safe to re-apply).
            logger.info(
                "Legacy database detected (llm_settings exists, no schema_version row). "
                "Running all migrations from version 0."
            )
            set_version(engine, 0)
        else:
            # Truly fresh install — Base.metadata.create_all just created
            # everything at the latest schema; no migration needed.
            logger.info(
                "Fresh database — setting schema to current version %d.",
                CURRENT_SCHEMA_VERSION,
            )
            set_version(engine, CURRENT_SCHEMA_VERSION)
            current_version = CURRENT_SCHEMA_VERSION

    success, error = run_migrations(engine)
    if not success:
        _startup_status.migration_ok = False
        _startup_status.migration_error = error
        _startup_status.needs_wipe = True
        logger.error(
            "Schema migration failed: %s — starting in degraded mode so the "
            "frontend can show a recovery prompt.",
            error,
        )
        # Yield early so /api/health is reachable; skip all further startup.
        yield
        return

    # ------------------------------------------------------------------ #
    # Step 3: Ensure default LLM settings                                  #
    # ------------------------------------------------------------------ #
    ensure_default_settings()

    # ------------------------------------------------------------------ #
    # Step 4: Alpaca portfolio sync (non-fatal)                            #
    # ------------------------------------------------------------------ #
    logger.info("Syncing Alpaca portfolio...")
    db = SessionLocal()
    try:
        sync_alpaca_portfolio(db)
    except Exception:
        logger.exception("Alpaca portfolio sync failed (non-fatal, continuing startup).")
    finally:
        db.close()

    # ------------------------------------------------------------------ #
    # Step 5: Initialize FAISS vector store (non-fatal)                   #
    # ------------------------------------------------------------------ #
    logger.info("Initialising FAISS vector store (fastembed may download model)...")
    from agent.vector_store import FaissManager
    import agent.knowledge_graph as kg_module
    from routers import graph as graph_router

    faiss_mgr = FaissManager()
    db = SessionLocal()
    try:
        await asyncio.to_thread(faiss_mgr.load_or_build, db)
        _faiss_ready = True
    except Exception:
        logger.exception("FAISS initialisation failed (non-fatal — vector search disabled).")
    finally:
        db.close()

    # Wire the shared FaissManager into the knowledge-graph module and router
    # (even if init failed — search will return empty results gracefully)
    kg_module.set_faiss_manager(faiss_mgr)
    graph_router.set_faiss_manager(faiss_mgr)

    # ------------------------------------------------------------------ #
    # Step 6: Start single-writer FAISS task                               #
    # ------------------------------------------------------------------ #
    write_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    kg_module.set_write_queue(write_queue)

    upsert_count = 0

    async def faiss_writer_loop() -> None:
        """Drain the write queue and apply FAISS updates serially.

        Messages are tuples of:
          ("upsert", node_ids: list[int], texts: list[str])
          ("rebuild", None, None)
          (None, None, None)   — shutdown sentinel

        After every 50 upserts the soft-delete ratio is checked; if it
        exceeds 10 % a full rebuild is triggered automatically.
        """
        nonlocal upsert_count
        while True:
            try:
                msg = await write_queue.get()
                op, node_ids, texts = msg

                if op is None:
                    logger.info("FAISS writer received shutdown sentinel.")
                    break

                if op == "upsert" and node_ids:
                    faiss_mgr.upsert_vectors(node_ids, texts)
                    upsert_count += 1

                    if upsert_count % 50 == 0:
                        def _check_rebuild() -> None:
                            _db = SessionLocal()
                            try:
                                total = _db.scalar(
                                    select(func.count()).select_from(KGNode)
                                ) or 0
                                deleted = _db.scalar(
                                    select(func.count())
                                    .select_from(KGNode)
                                    .where(KGNode.is_deleted == True)
                                ) or 0
                                faiss_mgr.maybe_rebuild(_db, deleted, total)
                            finally:
                                _db.close()

                        await asyncio.to_thread(_check_rebuild)

                elif op == "rebuild":
                    _db = SessionLocal()
                    try:
                        faiss_mgr._rebuild_from_db(_db)
                    finally:
                        _db.close()

            except asyncio.CancelledError:
                logger.info("FAISS writer task cancelled.")
                break
            except Exception:
                logger.exception("Unhandled error in FAISS writer task.")

    writer_task = asyncio.create_task(faiss_writer_loop())
    logger.info("FAISS writer task started.")

    # ------------------------------------------------------------------ #
    # Step 7: Start anomaly worker task                                    #
    # ------------------------------------------------------------------ #
    from tools.anomaly_worker import anomaly_worker_loop

    anomaly_task = asyncio.create_task(anomaly_worker_loop())
    logger.info("Anomaly worker task started.")

    yield  # ← App runs here

    # ------------------------------------------------------------------ #
    # Shutdown                                                             #
    # ------------------------------------------------------------------ #
    logger.info("Shutting down anomaly worker...")
    anomaly_task.cancel()
    try:
        await asyncio.wait_for(anomaly_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    logger.info("Shutting down FAISS writer...")
    try:
        await asyncio.wait_for(
            write_queue.put((None, None, None)),
            timeout=5.0,
        )
        await asyncio.wait_for(writer_task, timeout=10.0)
    except (asyncio.TimeoutError, Exception):
        writer_task.cancel()

    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="Open-Fin API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "app://.",               # Electron production
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

app.include_router(portfolio.router, prefix="/api")
app.include_router(ticker.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(trade.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(loadouts.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(settings.router, prefix="/api")

from routers import admin as admin_router
app.include_router(admin_router.router, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "status": "ok" if _startup_status.migration_ok else "degraded",
        "faiss_ready": _faiss_ready,
        "migration_ok": _startup_status.migration_ok,
        "migration_error": _startup_status.migration_error,
        "needs_wipe": _startup_status.needs_wipe,
    }
