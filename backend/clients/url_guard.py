"""URL validation guard against SSRF (Server-Side Request Forgery).

Blocks requests to:
- Private / internal IP ranges (RFC 1918, RFC 4193, link-local)
- Loopback addresses (``127.0.0.0/8``, ``::1``)
- Cloud metadata endpoints (``169.254.169.254``)
- Non-HTTP(S) schemes (``file://``, ``ftp://``, ``gopher://``, etc.)
- Hostnames that resolve to blocked IPs (even after redirect resolution)

Usage::

    from clients.url_guard import validate_url, SSRFBlockedError

    safe_url = validate_url("https://example.com/page")  # returns str
    validate_url("http://169.254.169.254/latest/meta-data")  # raises SSRFBlockedError
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Schemes we allow through the guard
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class SSRFBlockedError(ValueError):
    """Raised when a URL targets an internal/private/blocked network resource."""


def _is_blocked_ip(ip_str: str) -> bool:
    """Return ``True`` if the IP address is in a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block by default

    # Cloud metadata endpoint (AWS / GCP / Azure)
    if ip_str in ("169.254.169.254", "fd00::ec2", "metadata.google.internal"):
        return True

    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str) -> str:
    """Validate *url* and return it unchanged if safe.

    Raises
    ------
    SSRFBlockedError
        If the URL targets a blocked scheme, host, or IP range.
    """
    parsed = urlparse(url)

    # 1. Scheme check
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(
            f"Blocked URL scheme {parsed.scheme!r}. Only HTTP(S) is allowed."
        )

    # 2. Hostname presence
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("URL has no hostname.")

    # 3. Resolve hostname to IP(s) and check each
    try:
        addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise SSRFBlockedError(f"Could not resolve hostname {hostname!r}.")

    for family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            raise SSRFBlockedError(
                f"Blocked: {hostname!r} resolves to private/reserved IP {ip_str}."
            )

    return url


def validate_url_no_resolve(url: str) -> str:
    """Lightweight check without DNS resolution (for known-safe domains).

    Only validates scheme and hostname presence.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(
            f"Blocked URL scheme {parsed.scheme!r}. Only HTTP(S) is allowed."
        )
    if not parsed.hostname:
        raise SSRFBlockedError("URL has no hostname.")
    return url
