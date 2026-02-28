"""PyInstaller entry-point for the Open-Fin background worker.

This module is the target for the ``open-fin-worker`` PyInstaller spec.
It delegates to :func:`worker.main` which runs the APScheduler blocking
loop and the ``ProcessPoolExecutor`` for strategy execution.

``multiprocessing.freeze_support()`` is **required** on Windows because
``worker.py`` uses ``ProcessPoolExecutor(max_workers=2)`` for strategy
subprocesses — without it the frozen child processes would re-execute
the entry-point script instead of running the submitted callable.
"""

from __future__ import annotations

import multiprocessing
import sys


def run() -> int:
    from worker import main

    return main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(run())
