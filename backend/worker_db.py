import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def _database_url() -> str:
    override = os.getenv("OPEN_FIN_DB_PATH")
    if override:
        db_path = Path(override).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"
    return "sqlite:///./open_fin.db"


DATABASE_URL = _database_url()

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
