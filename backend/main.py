import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env before anything else. In packaged builds, Electron can point
# OPEN_FIN_ENV_PATH at a user-writable location.
load_dotenv(dotenv_path=os.getenv("OPEN_FIN_ENV_PATH"), override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from database import SessionLocal, engine
from models import Base, KGNode
from routers.portfolio import sync_alpaca_portfolio
from routers import alerts, chat, graph, loadouts, llm, portfolio, settings, ticker, trade, watchlist
from agent.llm import ensure_default_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ------------------------------------------------------------------ #
    # Startup                                                              #
    # ------------------------------------------------------------------ #

    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    ensure_default_settings()

    logger.info("Syncing Alpaca portfolio...")
    db = SessionLocal()
    try:
        sync_alpaca_portfolio(db)
    finally:
        db.close()

    # --- Initialize FAISS vector store -----------------------------------
    logger.info("Initialising FAISS vector store (fastembed may download model)...")
    from agent.vector_store import FaissManager
    import agent.knowledge_graph as kg_module
    from routers import graph as graph_router

    faiss_mgr = FaissManager()
    db = SessionLocal()
    try:
        await asyncio.to_thread(faiss_mgr.load_or_build, db)
    finally:
        db.close()

    # Wire the shared FaissManager into the knowledge-graph module and router
    kg_module.set_faiss_manager(faiss_mgr)
    graph_router.set_faiss_manager(faiss_mgr)

    # --- Start single-writer FAISS task ----------------------------------
    # All FAISS write operations go through this task so that file-level
    # locking is acquired serially, preventing index corruption.
    write_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    kg_module.set_write_queue(write_queue)

    upsert_count = 0  # mutable via closure cell

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
                    # Shutdown sentinel
                    logger.info("FAISS writer received shutdown sentinel.")
                    break

                if op == "upsert" and node_ids:
                    faiss_mgr.upsert_vectors(node_ids, texts)
                    upsert_count += 1

                    # Periodically check soft-delete ratio
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

    # --- Start anomaly worker task -----------------------------------
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
            write_queue.put((None, None, None)),  # shutdown sentinel
            timeout=5.0,
        )
        await asyncio.wait_for(writer_task, timeout=10.0)
    except (asyncio.TimeoutError, Exception):
        writer_task.cancel()

    logger.info("Shutdown complete.")


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


@app.get("/api/health")
def health():
    return {"status": "ok"}
