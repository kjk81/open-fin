import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    """Return the SQLite URL.

    In packaged desktop builds, the backend directory may be read-only. The
    Electron main process should set OPEN_FIN_DB_PATH to a writable location
    (e.g. app userData).
    """

    override = os.getenv("OPEN_FIN_DB_PATH")
    if override:
        db_path = Path(override).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"

    return "sqlite:///./open_fin.db"


DATABASE_URL = _database_url()
ASYNC_DATABASE_URL = DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
async_engine = create_async_engine(ASYNC_DATABASE_URL)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


@event.listens_for(async_engine.sync_engine, "connect")
def set_sqlite_pragma_async(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session
