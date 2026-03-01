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
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

# Ensure stdout can handle any Unicode — Windows CI runners default to cp1252
# which cannot encode replacement characters (\ufffd) that appear when the
# subprocess emits bytes that aren't valid UTF-8.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8000
STARTUP_TIMEOUT = 90   # seconds to wait for /api/health to respond
POLL_INTERVAL = 2.0    # seconds between health-check attempts
PORT_SENTINEL_TIMEOUT = 15  # seconds to wait for OPEN_FIN_PORT= line


def _default_exe() -> Path:
    ext = ".exe" if platform.system() == "Windows" else ""
    return BACKEND_DIR / "dist" / "api" / "open-fin-api" / f"open-fin-api{ext}"


def _stdout_reader(
    proc: subprocess.Popen,
    line_queue: queue.Queue,
    log_lines: list[str],
) -> None:
    """Read stdout lines in a background thread.

    Draining the pipe in a separate thread prevents the subprocess from
    blocking on a write() call when the OS pipe buffer fills up — a
    classic deadlock when stdout=PIPE is used without continuous reads.
    """
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode(errors="replace").rstrip("\n")
            log_lines.append(line)
            line_queue.put(line)
    except Exception:
        pass
    finally:
        line_queue.put(None)  # sentinel — reader is done


def _detect_port(line_queue: queue.Queue, deadline: float) -> int:
    """Scan stdout lines for the ``OPEN_FIN_PORT=<n>`` sentinel.

    Returns the port number emitted by entry_api.py, falling back to
    DEFAULT_PORT if the sentinel is not seen before *deadline*.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"[WARN] OPEN_FIN_PORT sentinel not seen — assuming port {DEFAULT_PORT}")
            return DEFAULT_PORT
        try:
            line = line_queue.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if line is None:
            print(f"[WARN] stdout closed before port sentinel — assuming port {DEFAULT_PORT}")
            return DEFAULT_PORT
        print(f"[OUT] {line}")
        if line.startswith("OPEN_FIN_PORT="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                pass


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

        # Captured log lines — populated by the reader thread
        log_lines: list[str] = []
        line_queue: queue.Queue = queue.Queue()

        reader = threading.Thread(
            target=_stdout_reader,
            args=(proc, line_queue, log_lines),
            daemon=True,
        )
        reader.start()

        try:
            # ----------------------------------------------------------------
            # Phase 1: detect the actual port from the stdout sentinel
            # ----------------------------------------------------------------
            port_deadline = time.monotonic() + PORT_SENTINEL_TIMEOUT
            port = _detect_port(line_queue, port_deadline)
            health_url = f"http://127.0.0.1:{port}/api/health"
            print(f"[INFO] Polling {health_url} (timeout={STARTUP_TIMEOUT}s)")

            # ----------------------------------------------------------------
            # Phase 2: poll the health endpoint until 200 or timeout
            # ----------------------------------------------------------------
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while time.monotonic() < deadline:
                # Drain any new stdout lines
                while True:
                    try:
                        line = line_queue.get_nowait()
                        if line is not None:
                            print(f"[OUT] {line}")
                    except queue.Empty:
                        break

                try:
                    resp = httpx.get(health_url, timeout=2.0)
                    if resp.status_code == 200:
                        print(f"[PASS] /api/health returned 200: {resp.json()}")
                        return 0
                except httpx.TimeoutException:
                    pass  # server not ready yet — keep polling
                except httpx.ConnectError:
                    pass  # connection refused — keep polling

                # Check if process crashed before the deadline expired
                if proc.poll() is not None:
                    reader.join(timeout=5)
                    print(f"[FAIL] Process exited early (code {proc.returncode})")
                    print("[FAIL] Full process output:")
                    print("\n".join(log_lines[-200:]))
                    return 1

                time.sleep(POLL_INTERVAL)

            # Timeout reached — dump captured output for debugging
            reader.join(timeout=5)
            print(f"[FAIL] Timed out after {STARTUP_TIMEOUT}s waiting for {health_url}")
            print("[FAIL] Process output captured during test:")
            print("\n".join(log_lines[-200:]))
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
