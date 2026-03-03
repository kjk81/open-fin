"""Phase 2 — Tests for tools/web.py (web_search providers)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestWebSearchTavily:
    @patch("tools.web.validate_url", side_effect=lambda u: u)
    async def test_tavily_default_provider(self, _guard):
        """web_search with provider='tavily' calls _search_tavily."""
        mock_tavily_response = {
            "results": [
                {"title": "Result 1", "url": "https://example.com/1", "content": "Snippet 1", "score": 0.9},
                {"title": "Result 2", "url": "https://example.com/2", "content": "Snippet 2", "score": 0.8},
            ]
        }

        with patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}), \
             patch("tools.web._search_tavily") as mock_tavily:
            from schemas.tool_contracts import SearchHit
            mock_tavily.return_value = [
                SearchHit(title="Result 1", url="https://example.com/1", snippet="Snippet 1", score=0.9),
                SearchHit(title="Result 2", url="https://example.com/2", snippet="Snippet 2", score=0.8),
            ]

            from tools.web import web_search
            result = await web_search("test query", provider="tavily", max_results=5)

        assert result.success is True
        assert result.data.provider == "tavily"
        assert len(result.data.hits) == 2

    async def test_unknown_provider_returns_error(self):
        from tools.web import web_search
        result = await web_search("test", provider="nonexistent")
        assert result.success is False
        assert "Unknown provider" in (result.error or "")


class TestWebSearchExa:
    async def test_exa_selection(self):
        """web_search with provider='exa' calls _search_exa."""
        with patch("tools.web._search_exa") as mock_exa:
            from schemas.tool_contracts import SearchHit
            mock_exa.return_value = [
                SearchHit(title="Exa Result", url="https://exa.ai/r", snippet="S", score=1.0),
            ]

            from tools.web import web_search
            result = await web_search("AI research", provider="exa")

        assert result.success is True
        assert result.data.provider == "exa"
        assert len(result.data.hits) == 1


class TestWebSearchEmptyResults:
    async def test_empty_results_still_success(self):
        with patch("tools.web._search_tavily") as mock_tavily:
            mock_tavily.return_value = []

            from tools.web import web_search
            result = await web_search("obscure query", provider="tavily")

        assert result.success is True
        assert result.data.hits == []


class TestWebSearchErrors:
    async def test_api_error_returns_failure(self):
        with patch("tools.web._search_tavily", side_effect=RuntimeError("API down")):
            from tools.web import web_search
            result = await web_search("test", provider="tavily")

        assert result.success is False
        assert "API down" in (result.error or "")

    async def test_fallback_provider_success_when_primary_fails(self):
        from schemas.tool_contracts import SearchHit

        with patch("tools.web._search_tavily", side_effect=RuntimeError("tavily unavailable")), \
             patch("tools.web._search_exa") as mock_exa:
            mock_exa.return_value = [
                SearchHit(title="Fallback Result", url="https://exa.ai/fallback", snippet="ok", score=0.7),
            ]

            from tools.web import web_search
            result = await web_search("test", provider="tavily")

        assert result.success is True
        assert result.data.provider == "exa"
        assert len(result.data.hits) == 1

    async def test_both_providers_fail_returns_combined_error(self):
        with patch("tools.web._search_tavily", side_effect=RuntimeError("tavily failed")), \
             patch("tools.web._search_exa", side_effect=RuntimeError("exa failed")):
            from tools.web import web_search
            result = await web_search("test", provider="tavily")

        assert result.success is False
        assert "tavily" in (result.error or "")
        assert "exa" in (result.error or "")
