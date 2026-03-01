"""Phase 5 — Tests for agent/llm.py (LLM provider fallback chain)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestNormalizeOrder:
    def test_preserves_valid_order(self):
        from agent.llm import _normalize_order, PROVIDERS
        order = ["openai", "groq", "gemini"]
        result = _normalize_order(order)
        # first 3 should match input, the rest should fill in
        assert result[:3] == ["openai", "groq", "gemini"]
        assert set(result) == set(PROVIDERS)

    def test_deduplication(self):
        from agent.llm import _normalize_order
        result = _normalize_order(["openai", "openai", "openai"])
        assert result.count("openai") == 1

    def test_unknown_providers_ignored(self):
        from agent.llm import _normalize_order, PROVIDERS
        result = _normalize_order(["unknown_provider", "openai"])
        assert "unknown_provider" not in result
        assert set(result) == set(PROVIDERS)

    def test_empty_input_returns_full(self):
        from agent.llm import _normalize_order, PROVIDERS
        result = _normalize_order([])
        assert set(result) == set(PROVIDERS)


class TestEffectiveOrder:
    def test_ollama_mode(self):
        from agent.llm import _effective_order
        result = _effective_order("ollama", ["openai", "groq"])
        assert result == ["ollama"]

    def test_cloud_mode(self):
        from agent.llm import _effective_order
        result = _effective_order("cloud", ["openai", "groq"])
        assert result[0] == "openai"
        assert result[1] == "groq"


class TestLoadLlmSettings:
    def test_default_when_no_rows(self):
        mock_db = MagicMock()
        mock_db.query.return_value.first.return_value = None

        with patch("agent.llm.SessionLocal", return_value=mock_db):
            from agent.llm import load_llm_settings
            mode, order = load_llm_settings()

        assert mode == "cloud"
        assert len(order) > 0

    def test_reads_from_db(self):
        import json
        mock_row = MagicMock()
        mock_row.mode = "ollama"
        mock_row.fallback_order_json = json.dumps(["ollama", "openai"])

        mock_db = MagicMock()
        mock_db.query.return_value.first.return_value = mock_row

        with patch("agent.llm.SessionLocal", return_value=mock_db):
            from agent.llm import load_llm_settings
            mode, order = load_llm_settings()

        assert mode == "ollama"


class TestValidateProviderOrder:
    def test_ensures_full_coverage(self):
        from agent.llm import validate_provider_order, PROVIDERS
        result = validate_provider_order(["groq"])
        assert result[0] == "groq"
        assert set(result) == set(PROVIDERS)


class TestFallbackLLM:
    async def test_ainvoke_tries_fallback(self):
        """If first provider fails, should try the next."""
        from agent.llm import FallbackLLM

        call_log = []

        def mock_provider_model(provider: str, role: str | None = None):
            model = AsyncMock()
            if provider == "openai":
                model.ainvoke = AsyncMock(side_effect=RuntimeError("OpenAI down"))
            elif provider == "groq":
                response = MagicMock()
                response.content = "Hello from Groq"
                model.ainvoke = AsyncMock(return_value=response)
            else:
                return None  # skip other providers
            call_log.append(provider)
            return model

        with patch("agent.llm._provider_model", side_effect=mock_provider_model):
            llm = FallbackLLM(mode="cloud", fallback_order=["openai", "groq"])
            result = await llm.ainvoke([])

        assert result.content == "Hello from Groq"

    async def test_ainvoke_all_fail_raises(self):
        """If ALL providers fail, should raise RuntimeError."""
        from agent.llm import FallbackLLM

        with patch("agent.llm._provider_model", return_value=None):
            llm = FallbackLLM(mode="cloud", fallback_order=["openai"])
            with pytest.raises(RuntimeError, match="No LLM provider"):
                await llm.ainvoke([])


class TestSettingsPayload:
    def test_returns_expected_keys(self):
        mock_db = MagicMock()
        mock_db.query.return_value.first.return_value = None

        with patch("agent.llm.SessionLocal", return_value=mock_db):
            from agent.llm import settings_payload
            result = settings_payload()

        assert "mode" in result
        assert "providers" in result
        assert "fallback_order" in result
