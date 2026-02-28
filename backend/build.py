"""Build script for freezing the Open-Fin backend into standalone executables.

Usage:
    python build.py          # Build both API and worker
    python build.py api      # Build only the API server
    python build.py worker   # Build only the worker

Output:
    dist/api/open-fin-api/open-fin-api(.exe)
    dist/worker/open-fin-worker/open-fin-worker(.exe)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent


def run(cmd: list[str]) -> None:
    """Run a command, streaming output, and abort on failure."""
    print(f"\n{'='*60}")
    print(f"  Running: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    result = subprocess.run(cmd, cwd=str(BACKEND_DIR))
    if result.returncode != 0:
        print(f"\n*** Build failed (exit code {result.returncode}) ***")
        sys.exit(result.returncode)


def build_api() -> None:
    """Freeze the API server via PyInstaller."""
    run([
        sys.executable, "-m", "PyInstaller",
        "open-fin-api.spec",
        "--distpath", "dist/api",
        "--workpath", "build/api",
        "--clean",
        "--noconfirm",
    ])


def build_worker() -> None:
    """Freeze the background worker via PyInstaller."""
    run([
        sys.executable, "-m", "PyInstaller",
        "open-fin-worker.spec",
        "--distpath", "dist/worker",
        "--workpath", "build/worker",
        "--clean",
        "--noconfirm",
    ])


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("all", "api"):
        build_api()
    if target in ("all", "worker"):
        build_worker()

    print(f"\n{'='*60}")
    print("  Build complete!")
    if target in ("all", "api"):
        api_dir = BACKEND_DIR / "dist" / "api" / "open-fin-api"
        print(f"  API:    {api_dir}")
    if target in ("all", "worker"):
        worker_dir = BACKEND_DIR / "dist" / "worker" / "open-fin-worker"
        print(f"  Worker: {worker_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
