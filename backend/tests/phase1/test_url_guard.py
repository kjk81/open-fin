"""Phase 1 — Tests for clients/url_guard.py (SSRF protection)."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from clients.url_guard import SSRFBlockedError, validate_url, validate_url_no_resolve


class TestValidateUrl:
    def test_allows_public_url(self):
        url = validate_url("https://www.google.com")
        assert url == "https://www.google.com"

    def test_blocks_file_scheme(self):
        with pytest.raises(SSRFBlockedError, match="scheme"):
            validate_url("file:///etc/passwd")

    def test_blocks_ftp_scheme(self):
        with pytest.raises(SSRFBlockedError, match="scheme"):
            validate_url("ftp://ftp.example.com/file")

    def test_blocks_no_hostname(self):
        with pytest.raises(SSRFBlockedError, match="hostname"):
            validate_url("http://")

    def test_blocks_localhost(self):
        with pytest.raises(SSRFBlockedError, match="private|reserved"):
            validate_url("http://localhost/admin")

    def test_blocks_127_0_0_1(self):
        with pytest.raises(SSRFBlockedError, match="private|reserved"):
            validate_url("http://127.0.0.1/secret")

    def test_blocks_metadata_endpoint(self):
        with pytest.raises(SSRFBlockedError):
            validate_url("http://169.254.169.254/latest/meta-data")

    def test_blocks_private_10_range(self):
        """Simulate hostname resolving to 10.x.x.x."""
        fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(SSRFBlockedError, match="private"):
                validate_url("http://evil.com/redirect")

    def test_blocks_private_172_range(self):
        fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("172.16.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(SSRFBlockedError, match="private"):
                validate_url("http://evil.com")

    def test_blocks_private_192_168(self):
        fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(SSRFBlockedError, match="private"):
                validate_url("http://evil.com")

    def test_blocks_ipv6_loopback(self):
        fake_addr = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
        with patch("socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(SSRFBlockedError):
                validate_url("http://evil.com")

    def test_unresolvable_host(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("fail")):
            with pytest.raises(SSRFBlockedError, match="resolve"):
                validate_url("http://nonexistent.host.invalid")


class TestValidateUrlNoResolve:
    def test_allows_valid(self):
        assert validate_url_no_resolve("https://sec.gov/file") == "https://sec.gov/file"

    def test_blocks_bad_scheme(self):
        with pytest.raises(SSRFBlockedError):
            validate_url_no_resolve("gopher://evil.com")
