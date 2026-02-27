import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env before anything else. In packaged builds, Electron can point
# OPEN_FIN_ENV_PATH at a user-writable location.
load_dotenv(dotenv_path=os.getenv("OPEN_FIN_ENV_PATH"), override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, SessionLocal
from models import Base
from routers.portfolio import sync_alpaca_portfolio
from routers import portfolio, ticker, chat, trade, llm, watchlist, graph
from agent.llm import ensure_default_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    ensure_default_settings()

    logger.info("Syncing Alpaca portfolio...")
    db = SessionLocal()
    try:
        sync_alpaca_portfolio(db)
    finally:
        db.close()

    yield  # App runs here

    # Shutdown (nothing to clean up in Phase 1)
    logger.info("Shutting down.")


app = FastAPI(title="Open-Fin API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "app://.",                  # Electron production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio.router, prefix="/api")
app.include_router(ticker.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(trade.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(graph.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
