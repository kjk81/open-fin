"""Phase 2 — Tests for tools/web.py (web_fetch)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import httpx
import pytest

from schemas.kg_entities import WebDocument
from schemas.tool_contracts import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html>
<head><title>Test Page</title></head>
<body>
<nav>Navigation</nav>
<h1>Hello World</h1>
<p>This is a test paragraph.</p>
<script>alert("xss")</script>
<footer>Footer</footer>
</body>
</html>
"""

_BROKEN_HTML = """
<html><body>
<div><div><div><p>Deeply nested
<script>evil()</script>
<p>More content
</body>
"""


def _make_cache_row(*, url: str, html: str, ttl: int = 900, age_minutes: int = 0):
    """Create a mock HttpCache row."""
    row = MagicMock()
    row.url = url
    row.response_text = html
    row.ttl_seconds = ttl
    row.fetched_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return row


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------

class TestWebFetchCache:
    @patch("tools.web.SessionLocal")
    async def test_cache_hit_returns_markdown(self, mock_session_cls):
        """On cache hit, web_fetch should convert HTML to markdown, not return raw HTML."""
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        # Simulate cache hit
        cache_row = _make_cache_row(url="https://example.com", html=_SAMPLE_HTML, age_minutes=5)
        mock_db.query.return_value.filter.return_value.first.return_value = cache_row

        # Patch validate_url to skip DNS resolution in tests
        with patch("tools.web.validate_url", side_effect=lambda u: u):
            from tools.web import web_fetch
            result = await web_fetch("https://example.com", extract_mode="markdown")

        assert result.success is True
        # Should NOT contain raw HTML tags — it should be markdown
        assert "<html>" not in result.data.snippet
        assert "<nav>" not in result.data.snippet
        assert "<script>" not in result.data.snippet

    @patch("tools.web.SessionLocal")
    async def test_cache_hit_text_mode(self, mock_session_cls):
        """Cache hit with extract_mode='text' should return plain text."""
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        cache_row = _make_cache_row(url="https://example.com", html=_SAMPLE_HTML, age_minutes=1)
        mock_db.query.return_value.filter.return_value.first.return_value = cache_row

        with patch("tools.web.validate_url", side_effect=lambda u: u):
            from tools.web import web_fetch
            result = await web_fetch("https://example.com", extract_mode="text")

        assert result.success is True
        assert "Hello World" in result.data.snippet
        # Should not have script or nav content
        assert "alert" not in result.data.snippet
        assert "Navigation" not in result.data.snippet

    @patch("tools.web.SessionLocal")
    async def test_cache_miss_expired(self, mock_session_cls):
        """Expired cache entry = cache miss → should attempt HTTP fetch."""
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        # Cache row exists but is expired
        cache_row = _make_cache_row(url="https://example.com", html=_SAMPLE_HTML, age_minutes=20)
        mock_db.query.return_value.filter.return_value.first.return_value = cache_row

        # Mock the HTTP client
        mock_response = MagicMock()
        mock_response.text = _SAMPLE_HTML

        with patch("tools.web.validate_url", side_effect=lambda u: u), \
             patch("tools.web.HttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from tools.web import web_fetch
            result = await web_fetch("https://example.com")

        assert result.success is True


# ---------------------------------------------------------------------------
# Broken HTML handling
# ---------------------------------------------------------------------------

class TestWebFetchBrokenHTML:
    @patch("tools.web._cache_put")
    @patch("tools.web._cache_get", return_value=None)
    @patch("tools.web.validate_url", side_effect=lambda u: u)
    async def test_broken_html_parsed_gracefully(self, _guard, _cg, _cp):
        """BeautifulSoup should handle deeply nested / broken HTML gracefully."""
        mock_response = MagicMock()
        mock_response.text = _BROKEN_HTML

        with patch("tools.web.HttpClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            from tools.web import web_fetch
            result = await web_fetch("https://broken.example.com")

        assert result.success is True
        assert "<script>" not in result.data.snippet


# ---------------------------------------------------------------------------
# SSRF blocking
# ---------------------------------------------------------------------------

class TestWebFetchSSRF:
    async def test_blocks_private_url(self):
        """web_fetch should block SSRF attempts to private IPs."""
        from tools.web import web_fetch
        result = await web_fetch("http://127.0.0.1/admin")
        assert result.success is False
        assert "Blocked" in (result.error or "") or "private" in (result.error or "").lower() or "reserved" in (result.error or "").lower()

    async def test_blocks_metadata_endpoint(self):
        from tools.web import web_fetch
        result = await web_fetch("http://169.254.169.254/latest/meta-data")
        assert result.success is False


# ---------------------------------------------------------------------------
# Timeout / network error
# ---------------------------------------------------------------------------

class TestWebFetchErrors:
    @patch("tools.web._cache_get", return_value=None)
    @patch("tools.web.validate_url", side_effect=lambda u: u)
    async def test_timeout_returns_failure(self, _guard, _cg):
        """Timeout during fetch should return success=False."""
        with patch("tools.web.HttpClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            from tools.web import web_fetch
            result = await web_fetch("https://slow.example.com")

        assert result.success is False
        assert "timeout" in (result.error or "").lower()
