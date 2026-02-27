from __future__ import annotations
import os
import logging
from functools import lru_cache
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

from typing import AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessageChunk

from database import SessionLocal
from models import LLMSettings
def get_llm() -> BaseChatModel:
    """
    Return a configured chat model using the first available provider.
PROVIDERS: tuple[str, ...] = (
    "openrouter",
    "gemini",
    "openai",
    "groq",
    "huggingface",
    "ollama",
)

DEFAULT_FALLBACK_ORDER: list[str] = [
    "openrouter",
    "gemini",
    "openai",
    "groq",
    "huggingface",
    "ollama",
]


def _normalize_order(order: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for provider in order:
        provider_name = provider.strip().lower()
        if provider_name in PROVIDERS and provider_name not in seen:
            normalized.append(provider_name)
            seen.add(provider_name)
    for provider in PROVIDERS:
        if provider not in seen:
            normalized.append(provider)
    return normalized


def load_llm_settings() -> tuple[str, list[str]]:
    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if not row:
            return "cloud", DEFAULT_FALLBACK_ORDER.copy()

        mode = row.mode.lower().strip() if row.mode else "cloud"
        if mode not in {"cloud", "ollama"}:
            mode = "cloud"

        import json

        try:
            parsed = json.loads(row.fallback_order_json or "[]")
        except json.JSONDecodeError:
            parsed = []

        if not isinstance(parsed, list):
            parsed = []

        parsed_order = [str(item) for item in parsed]
        return mode, _normalize_order(parsed_order or DEFAULT_FALLBACK_ORDER.copy())
    finally:
        db.close()


def _provider_model(provider: str) -> BaseChatModel | None:
        logger.info("LLM provider: Ollama (model=%s)", ollama_model)
    provider_name = provider.lower()

    if provider_name == "ollama":
        ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        )

    # 2. OpenRouter — default online provider
            api_key="ollama",
    openrouter_model = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")
            timeout=45,
    if openrouter_key:
        logger.info("LLM provider: OpenRouter (model=%s)", openrouter_model)
    if provider_name == "openrouter":
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_key:
            return None
        openrouter_model = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")
        )

    # 3. OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            timeout=45,
    if openai_key:
        logger.info("LLM provider: OpenAI (model=%s)", openai_model)
    if provider_name == "openai":
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return None
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # 4. Groq
    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
            timeout=45,
    if groq_key:
        logger.info("LLM provider: Groq (model=%s)", groq_model)
    if provider_name == "groq":
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            return None
        groq_model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
        )

    # 5. Gemini — requires langchain-google-genai package
    gemini_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
            timeout=45,
    if gemini_key:
        logger.info("LLM provider: Gemini (model=%s)", gemini_model)
    if provider_name == "gemini":
        gemini_key = os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            return None
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        )


    # 6. HuggingFace Inference API (OpenAI-compatible)
    hf_token = os.getenv("HF_API_TOKEN")
    hf_model = os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")
            timeout=45,
    if hf_token:
        logger.info("LLM provider: HuggingFace (model=%s)", hf_model)
    if provider_name == "huggingface":
        hf_token = os.getenv("HF_API_TOKEN")
        if not hf_token:
            return None
        hf_model = os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2")
        )

    raise RuntimeError(
        "No LLM provider configured. Set one of: OLLAMA_MODEL, OPENROUTER_API_KEY, "
        "OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, or HF_API_TOKEN in backend/.env"
            timeout=45,
    )

    return None


def _effective_order(mode: str, configured_order: list[str]) -> list[str]:
    if mode == "ollama":
        return ["ollama"]
    return _normalize_order(configured_order)


class FallbackLLM:
    def __init__(self, mode: str, fallback_order: list[str]) -> None:
        self.mode = mode
        self.fallback_order = _effective_order(mode, fallback_order)

    async def ainvoke(self, messages: list[BaseMessage]):
        errors: list[str] = []
        for provider in self.fallback_order:
            model = _provider_model(provider)
            if model is None:
                continue
            try:
                logger.info("LLM invoke provider=%s", provider)
                return await model.ainvoke(messages)
            except Exception as exc:
                logger.warning("LLM invoke failed provider=%s error=%s", provider, exc)
                errors.append(f"{provider}: {exc}")
        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Check API keys, Ollama status, and fallback order settings."
        )

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        errors: list[str] = []
        for provider in self.fallback_order:
            model = _provider_model(provider)
            if model is None:
                continue

            emitted_any = False
            try:
                logger.info("LLM stream provider=%s", provider)
                async for chunk in model.astream(messages):
                    emitted_any = True
                    yield chunk
                return
            except Exception as exc:
                logger.warning("LLM stream failed provider=%s error=%s", provider, exc)
                if emitted_any:
                    raise RuntimeError(f"Streaming interrupted from provider '{provider}'.") from exc
                errors.append(f"{provider}: {exc}")
                continue

        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Check API keys, Ollama status, and fallback order settings."
        )


def get_llm() -> FallbackLLM:
    mode, fallback_order = load_llm_settings()
    return FallbackLLM(mode=mode, fallback_order=fallback_order)


def validate_provider_order(order: list[str]) -> list[str]:
    normalized = _normalize_order(order)
    if len(normalized) != len(PROVIDERS):
        raise ValueError("Provider order must include each provider exactly once.")
    return normalized


def settings_payload() -> dict:
    mode, fallback_order = load_llm_settings()
    return {
        "mode": mode,
        "providers": list(PROVIDERS),
        "fallback_order": fallback_order,
    }


def persist_settings(mode: str, fallback_order: list[str]) -> dict:
    import json
    from datetime import datetime

    normalized_mode = mode.lower().strip()
    if normalized_mode not in {"cloud", "ollama"}:
        raise ValueError("mode must be one of: cloud, ollama")

    normalized_order = validate_provider_order(fallback_order)

    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if row is None:
            row = LLMSettings(
                mode=normalized_mode,
                fallback_order_json=json.dumps(normalized_order),
                updated_at=datetime.utcnow(),
            )
            db.add(row)
        else:
            row.mode = normalized_mode
            row.fallback_order_json = json.dumps(normalized_order)
            row.updated_at = datetime.utcnow()

        db.commit()
        return {
            "mode": normalized_mode,
            "providers": list(PROVIDERS),
            "fallback_order": normalized_order,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_default_settings() -> None:
    import json
    from datetime import datetime

    db = SessionLocal()
    try:
        row = db.query(LLMSettings).first()
        if row is None:
            db.add(LLMSettings(
                mode="cloud",
                fallback_order_json=json.dumps(DEFAULT_FALLBACK_ORDER),
                updated_at=datetime.utcnow(),
            ))
            db.commit()
    except Exception as exc:
        logger.warning("Failed ensuring default LLM settings: %s", exc)
        db.rollback()
    finally:
        db.close()
