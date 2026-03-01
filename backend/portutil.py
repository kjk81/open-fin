"""Port availability utilities for the Open-Fin API server.

Usage
-----
    from portutil import find_free_port

    port = find_free_port(preferred=8000)  # returns 8000 or next available port
"""

from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

_DEFAULT_PREFERRED = 8000
_DEFAULT_MAX_ATTEMPTS = 10


def _is_port_free(host: str, port: int) -> bool:
    """Return True if *port* on *host* can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(
    preferred: int = _DEFAULT_PREFERRED,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    host: str = "127.0.0.1",
) -> int:
    """Return the first free TCP port starting from *preferred*.

    Tries *preferred*, *preferred+1*, ..., up to *max_attempts* candidates.
    Raises ``SystemExit(1)`` with an actionable message if none are available.
    """
    for candidate in range(preferred, preferred + max_attempts):
        if _is_port_free(host, candidate):
            if candidate != preferred:
                logger.warning(
                    "Port %d is in use; using port %d instead.", preferred, candidate
                )
            return candidate

    msg = (
        f"All ports {preferred}–{preferred + max_attempts - 1} are in use. "
        "Kill any leftover Open-Fin processes and restart, or set "
        "OPEN_FIN_PREFERRED_PORT to an available port."
    )
    logger.error(msg)
    raise SystemExit(1)
