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
import os
import sys


def run() -> None:
    import uvicorn

    from portutil import find_free_port

    # Import the FastAPI app object — all lifespan logic (DB init, FAISS
    # startup, etc.) is handled inside main.py's lifespan context manager.
    from main import app  # noqa: F401

    preferred = int(os.getenv("OPEN_FIN_PREFERRED_PORT", "8000"))
    port = find_free_port(preferred=preferred)

    # Emit a machine-readable sentinel so the Electron host can learn which
    # port was actually bound (it may differ from the preferred port when
    # a previous instance is still holding the socket).
    print(f"OPEN_FIN_PORT={port}", flush=True)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
