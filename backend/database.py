import os
from pathlib import Path

from sqlalchemy import create_engine
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

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
