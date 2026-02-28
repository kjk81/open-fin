"""PyInstaller entry-point for the Open-Fin API server.

This module is the target for the ``open-fin-api`` PyInstaller spec.  It
imports the FastAPI ``app`` object from ``main`` and starts Uvicorn
programmatically (rather than via ``python -m uvicorn``, which is fragile
under PyInstaller).

The ``multiprocessing.freeze_support()`` call is required on Windows so that
child processes spawned by the application (e.g. ONNX runtime threads in
fastembed) work correctly in a frozen executable.
"""

from __future__ import annotations

import multiprocessing
import sys


def run() -> None:
    import uvicorn

    # Import the FastAPI app object — all lifespan logic (DB init, FAISS
    # startup, etc.) is handled inside main.py's lifespan context manager.
    from main import app  # noqa: F401

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
