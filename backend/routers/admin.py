"""Admin endpoints for data recovery and maintenance."""

import os
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from database import engine, Base
from migrations import set_version, CURRENT_SCHEMA_VERSION

router = APIRouter(tags=["admin"])


class WipeResponse(BaseModel):
    status: str
    scope: str


@router.post("/admin/wipe", response_model=WipeResponse)
def wipe_data(scope: Literal["all", "db", "faiss"] = "all") -> WipeResponse:
    """Delete user data and recreate a clean schema.

    Parameters
    ----------
    scope:
        ``"all"``   — wipe both the SQLite database and the FAISS index.
        ``"db"``    — drop and recreate all SQLite tables only.
        ``"faiss"`` — delete the FAISS index directory only.

    This endpoint is called by the frontend when the user confirms they want
    to reset legacy data that cannot be auto-migrated.  It does NOT touch
    the ``.env`` file or Alpaca API keys.
    """
    if scope in ("all", "db"):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        set_version(engine, CURRENT_SCHEMA_VERSION)

    if scope in ("all", "faiss"):
        faiss_dir = os.getenv("OPEN_FIN_FAISS_DIR")
        if faiss_dir:
            faiss_path = Path(faiss_dir).expanduser().resolve()
            if faiss_path.exists():
                shutil.rmtree(faiss_path)
            faiss_path.mkdir(parents=True, exist_ok=True)

    return WipeResponse(status="wiped", scope=scope)
