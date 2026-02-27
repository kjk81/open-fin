from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from langchain_core.messages import AIMessageChunk, BaseMessage

from database import SessionLocal
from models import LLMSettings

logger = logging.getLogger(__name__)


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
        provider_name = str(provider).strip().lower()
        if provider_name in PROVIDERS and provider_name not in seen:
            normalized.append(provider_name)
            seen.add(provider_name)

    # Ensure full coverage, preserving provider order for any omitted entries.
    for provider in PROVIDERS:
        if provider not in seen:
            normalized.append(provider)
            seen.add(provider)

    return normalized


def load_llm_settings() -> tuple[str, list[str]]:
    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if not row:
            return "cloud", DEFAULT_FALLBACK_ORDER.copy()

        mode = (row.mode or "cloud").lower().strip()
        if mode not in {"cloud", "ollama"}:
            mode = "cloud"

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


def validate_provider_order(order: list[str]) -> list[str]:
    normalized = _normalize_order(order)
    # Normalization always produces full coverage in PROVIDERS order.
    return normalized


def settings_payload() -> dict:
    mode, fallback_order = load_llm_settings()
    return {
        "mode": mode,
        "providers": list(PROVIDERS),
        "fallback_order": fallback_order,
    }


def persist_settings(mode: str, fallback_order: list[str]) -> dict:
    normalized_mode = (mode or "").lower().strip()
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
    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if row is None:
            db.add(
                LLMSettings(
                    mode="cloud",
                    fallback_order_json=json.dumps(DEFAULT_FALLBACK_ORDER),
                    updated_at=datetime.utcnow(),
                )
            )
            db.commit()
    except Exception as exc:
        logger.warning("Failed ensuring default LLM settings: %s", exc)
        db.rollback()
    finally:
        db.close()


@dataclass(frozen=True)
class _ProviderConfig:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


def _provider_config(provider: str) -> _ProviderConfig | None:
    provider_name = provider.lower().strip()

    if provider_name == "ollama":
        return _ProviderConfig(
            provider="ollama",
            model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )

    if provider_name == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="openrouter",
            model=os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct"),
            api_key=key,
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        )

    if provider_name == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        base_url = os.getenv("OPENAI_BASE_URL") or None
        return _ProviderConfig(
            provider="openai",
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=key,
            base_url=base_url,
        )

    if provider_name == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="groq",
            model=os.getenv("GROQ_MODEL", "llama3-8b-8192"),
            api_key=key,
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        )

    if provider_name == "huggingface":
        token = os.getenv("HF_API_TOKEN")
        base_url = os.getenv("HF_BASE_URL")
        if not token or not base_url:
            return None
        return _ProviderConfig(
            provider="huggingface",
            model=os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2"),
            api_key=token,
            base_url=base_url,
        )

    if provider_name == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="gemini",
            model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            api_key=key,
        )

    return None


def _provider_model(provider: str):
    cfg = _provider_config(provider)
    if cfg is None:
        return None

    if cfg.provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except Exception as exc:
            raise RuntimeError(
                "Ollama provider requires the 'langchain-ollama' package. "
                "Install it and try again."
            ) from exc

        logger.info("LLM provider: Ollama (model=%s)", cfg.model)
        return ChatOllama(model=cfg.model, base_url=cfg.base_url, temperature=0.2)

    if cfg.provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except Exception as exc:
            raise RuntimeError(
                "Gemini provider requires the 'langchain-google-genai' package."
            ) from exc

        logger.info("LLM provider: Gemini (model=%s)", cfg.model)
        return ChatGoogleGenerativeAI(
            model=cfg.model,
            google_api_key=cfg.api_key,
            temperature=0.2,
        )

    # OpenAI-compatible providers
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "temperature": 0.2,
        "timeout": 45,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    label = cfg.provider.capitalize()
    logger.info("LLM provider: %s (model=%s)", label, cfg.model)
    return ChatOpenAI(**kwargs)


def _effective_order(mode: str, configured_order: list[str]) -> list[str]:
    normalized_mode = (mode or "cloud").lower().strip()
    if normalized_mode == "ollama":
        return ["ollama"]
    return _normalize_order(configured_order)


class FallbackLLM:
    def __init__(self, mode: str, fallback_order: list[str]) -> None:
        self.mode = (mode or "cloud").lower().strip()
        self.fallback_order = _effective_order(self.mode, fallback_order)

    async def ainvoke(self, messages: list[BaseMessage]):
        last_error: Exception | None = None
        for provider in self.fallback_order:
            model = _provider_model(provider)
            if model is None:
                continue
            try:
                logger.info("LLM invoke provider=%s", provider)
                return await model.ainvoke(messages)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM invoke failed provider=%s error=%s", provider, exc)

        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Configure at least one provider in backend/.env (or the app settings). "
            "For HuggingFace, set both HF_API_TOKEN and HF_BASE_URL (OpenAI-compatible endpoint)."
        ) from last_error

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        last_error: Exception | None = None
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
                last_error = exc
                logger.warning("LLM stream failed provider=%s error=%s", provider, exc)
                if emitted_any:
                    raise RuntimeError(
                        f"Streaming interrupted from provider '{provider}'."
                    ) from exc
                continue

        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Configure at least one provider in backend/.env (or the app settings)."
        ) from last_error


def get_llm() -> FallbackLLM:
    mode, fallback_order = load_llm_settings()
    return FallbackLLM(mode=mode, fallback_order=fallback_order)
