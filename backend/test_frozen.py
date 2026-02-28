"""Smoke test for the frozen Open-Fin API binary.

Spawns the PyInstaller-built ``open-fin-api`` executable, waits for the
``/api/health`` endpoint to return HTTP 200, then shuts it down.

Usage:
    python test_frozen.py          # auto-detect platform
    python test_frozen.py <path>   # explicit path to executable

Exit codes:
    0 — health check passed
    1 — health check failed or binary did not start
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent
HEALTH_URL = "http://127.0.0.1:8000/api/health"
STARTUP_TIMEOUT = 60  # seconds to wait for /api/health to respond
POLL_INTERVAL = 1.0


def _default_exe() -> Path:
    ext = ".exe" if platform.system() == "Windows" else ""
    return BACKEND_DIR / "dist" / "api" / "open-fin-api" / f"open-fin-api{ext}"


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_exe()

    if not exe.exists():
        print(f"[FAIL] Binary not found: {exe}")
        return 1

    # Use a temporary directory for DB / FAISS so the test is hermetic
    with tempfile.TemporaryDirectory(prefix="openfin_smoke_") as tmpdir:
        env = {
            **os.environ,
            "OPEN_FIN_DB_PATH": str(Path(tmpdir) / "test.db"),
            "OPEN_FIN_KG_PATH": str(Path(tmpdir) / "kg.json"),
            "OPEN_FIN_FAISS_DIR": str(Path(tmpdir) / "faiss"),
            # Skip .env loading — no secrets needed for a health check
            "OPEN_FIN_ENV_PATH": str(Path(tmpdir) / ".env"),
        }

        # Create an empty .env so dotenv doesn't warn
        (Path(tmpdir) / ".env").touch()

        print(f"[INFO] Starting frozen API: {exe}")
        proc = subprocess.Popen(
            [str(exe)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    resp = httpx.get(HEALTH_URL, timeout=2.0)
                    if resp.status_code == 200:
                        print(f"[PASS] /api/health returned 200: {resp.json()}")
                        return 0
                except httpx.ConnectError:
                    pass  # server not up yet
                except httpx.ReadTimeout:
                    pass

                # Check if process crashed
                if proc.poll() is not None:
                    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                    print(f"[FAIL] Process exited early (code {proc.returncode})")
                    print(out[-2000:])
                    return 1

                time.sleep(POLL_INTERVAL)

            print(f"[FAIL] Timed out after {STARTUP_TIMEOUT}s waiting for {HEALTH_URL}")
            return 1

        finally:
            # Graceful shutdown
            if proc.poll() is None:
                if platform.system() == "Windows":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            print("[INFO] Frozen API process stopped.")


if __name__ == "__main__":
    sys.exit(main())
