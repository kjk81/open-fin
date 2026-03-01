"""Admin endpoints for data recovery and maintenance."""

import os
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from database import engine, Base
from migrations import set_version, CURRENT_SCHEMA_VERSION

router = APIRouter(tags=["admin"])

# ---------------------------------------------------------------------------
# Security gate
# ---------------------------------------------------------------------------
# Wipe is a destructive operation.  Guard it with two independent checks:
#
# 1. Caller must be the local machine (Electron renderer always uses localhost).
# 2. Env var OPEN_FIN_ADMIN_WIPE_ENABLED must not be explicitly set to "false".
#    It defaults to enabled so dev and Electron flows work out-of-the-box;
#    operators can disable it on shared/server deployments.

_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_WIPE_ENABLED: bool = (
    os.getenv("OPEN_FIN_ADMIN_WIPE_ENABLED", "true").strip().lower() != "false"
)


class WipeResponse(BaseModel):
    status: str
    scope: str


@router.post("/admin/wipe", response_model=WipeResponse)
def wipe_data(
    request: Request,
    scope: Literal["all", "db", "faiss"] = "all",
) -> WipeResponse:
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

    Access is restricted to local connections only (``127.0.0.1`` / ``::1``).
    Set ``OPEN_FIN_ADMIN_WIPE_ENABLED=false`` in the environment to disable
    this endpoint entirely on shared deployments.
    """
    # Security check 1: env-gate
    if not _WIPE_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Admin wipe is disabled on this deployment (OPEN_FIN_ADMIN_WIPE_ENABLED=false).",
        )

    # Security check 2: local-only
    client_host = (request.client.host if request.client else "").strip()
    if client_host not in _LOCALHOST_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=f"Wipe endpoint is only accessible from localhost (got '{client_host}').",
        )

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
