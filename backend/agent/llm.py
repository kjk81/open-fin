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

# Role-specific model defaults: (provider, role) -> default model name.
# Falls back to the provider's global default when no entry matches.
_ROLE_DEFAULTS: dict[tuple[str, str], str] = {
    ("ollama", "agent"): "qwen3:4b",
    ("ollama", "subagent"): "qwen3:8b",
    ("openrouter", "subagent"): "arcee-ai/trinity-large-preview",
    ("groq", "subagent"): "openai/gpt-oss-120b",
}


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


def load_llm_settings() -> tuple[str, list[str], list[str] | None]:
    """Load mode, agent fallback order, and optional subagent fallback order from DB.

    Returns:
        (mode, agent_fallback_order, subagent_fallback_order)
        ``subagent_fallback_order`` is ``None`` when no separate order has been
        configured — the agent order should be used for both roles in that case.
    """
    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if not row:
            return "cloud", DEFAULT_FALLBACK_ORDER.copy(), None

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
        agent_order = _normalize_order(parsed_order or DEFAULT_FALLBACK_ORDER.copy())

        # Parse optional subagent fallback order
        subagent_order: list[str] | None = None
        sub_json = getattr(row, "subagent_fallback_order_json", None)
        if sub_json:
            try:
                sub_parsed = json.loads(sub_json)
            except json.JSONDecodeError:
                sub_parsed = []
            if isinstance(sub_parsed, list) and sub_parsed:
                subagent_order = _normalize_order([str(item) for item in sub_parsed])

        return mode, agent_order, subagent_order
    finally:
        db.close()


def validate_provider_order(order: list[str]) -> list[str]:
    normalized = _normalize_order(order)
    # Normalization always produces full coverage in PROVIDERS order.
    return normalized


def settings_payload() -> dict:
    mode, fallback_order, subagent_order = load_llm_settings()
    payload: dict = {
        "mode": mode,
        "providers": list(PROVIDERS),
        "fallback_order": fallback_order,
    }
    if subagent_order is not None:
        payload["subagent_fallback_order"] = subagent_order

    # Report which provider/model each role resolves to (informational)
    for role in ("agent", "subagent"):
        order = _effective_order_for_role(
            mode, fallback_order, role=role, subagent_order=subagent_order
        )
        for provider in order:
            cfg = _provider_config(provider, role=role)
            if cfg is not None:
                payload[f"{role}_provider"] = cfg.provider
                payload[f"{role}_model"] = cfg.model
                break
    return payload


def persist_settings(
    mode: str,
    fallback_order: list[str],
    subagent_fallback_order: list[str] | None = None,
) -> dict:
    normalized_mode = (mode or "").lower().strip()
    if normalized_mode not in {"cloud", "ollama"}:
        raise ValueError("mode must be one of: cloud, ollama")

    normalized_order = validate_provider_order(fallback_order)
    normalized_sub = validate_provider_order(subagent_fallback_order) if subagent_fallback_order else None

    db = SessionLocal()
    try:
        row: LLMSettings | None = db.query(LLMSettings).first()
        if row is None:
            row = LLMSettings(
                mode=normalized_mode,
                fallback_order_json=json.dumps(normalized_order),
                subagent_fallback_order_json=json.dumps(normalized_sub) if normalized_sub else None,
                updated_at=datetime.utcnow(),
            )
            db.add(row)
        else:
            row.mode = normalized_mode
            row.fallback_order_json = json.dumps(normalized_order)
            row.subagent_fallback_order_json = json.dumps(normalized_sub) if normalized_sub else None
            row.updated_at = datetime.utcnow()

        db.commit()
        result: dict = {
            "mode": normalized_mode,
            "providers": list(PROVIDERS),
            "fallback_order": normalized_order,
        }
        if normalized_sub:
            result["subagent_fallback_order"] = normalized_sub
        return result
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


def _provider_config(provider: str, role: str | None = None) -> _ProviderConfig | None:
    """Return provider configuration, optionally overriding the model for a role.

    When *role* is ``"agent"`` or ``"subagent"``, the function checks for
    role-prefixed env vars first (e.g. ``AGENT_OPENROUTER_MODEL``) and falls
    back to the global var (e.g. ``OPENROUTER_MODEL``).  If no env var is set,
    a role-specific default from ``_ROLE_DEFAULTS`` is used before the
    provider-level default.  API keys and base URLs are always shared.
    """
    provider_name = provider.lower().strip()
    role_prefix = f"{role.upper()}_" if role else ""

    def _model(role_var: str, global_var: str, default: str) -> str:
        # Check generic {ROLE}_MODEL first (e.g. AGENT_MODEL / SUBAGENT_MODEL),
        # then the per-provider role override (e.g. AGENT_OPENROUTER_MODEL),
        # then the global per-provider var, then the role-specific (or global) default.
        role_default = _ROLE_DEFAULTS.get((provider_name, role), default) if role else default
        return (
            os.getenv(f"{role_prefix}MODEL")
            or os.getenv(f"{role_prefix}{role_var}")
            or os.getenv(global_var, role_default)
        )

    if provider_name == "ollama":
        return _ProviderConfig(
            provider="ollama",
            model=_model("OLLAMA_MODEL", "OLLAMA_MODEL", "llama3.1:8b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )

    if provider_name == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="openrouter",
            model=_model("OPENROUTER_MODEL", "OPENROUTER_MODEL", "mistralai/mistral-7b-instruct"),
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
            model=_model("OPENAI_MODEL", "OPENAI_MODEL", "gpt-4o-mini"),
            api_key=key,
            base_url=base_url,
        )

    if provider_name == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="groq",
            model=_model("GROQ_MODEL", "GROQ_MODEL", "llama3-8b-8192"),
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
            model=_model("HF_MODEL", "HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.2"),
            api_key=token,
            base_url=base_url,
        )

    if provider_name == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        return _ProviderConfig(
            provider="gemini",
            model=_model("GEMINI_MODEL", "GEMINI_MODEL", "gemini-1.5-flash"),
            api_key=key,
        )

    return None


def _provider_model(provider: str, role: str | None = None):
    """Instantiate a LangChain chat model for *provider*, optionally for a *role*.

    The ``role`` parameter is forwarded to :func:`_provider_config` to select
    role-specific model names.  Subagent calls use a lower temperature (0.1)
    for more deterministic tool-call JSON; all other roles use 0.2.
    """
    cfg = _provider_config(provider, role=role)
    if cfg is None:
        return None

    temperature = 0.1 if role == "subagent" else 0.2

    if cfg.provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except Exception as exc:
            raise RuntimeError(
                "Ollama provider requires the 'langchain-ollama' package. "
                "Install it and try again."
            ) from exc

        logger.info("LLM provider: Ollama (model=%s role=%s)", cfg.model, role)
        return ChatOllama(model=cfg.model, base_url=cfg.base_url, temperature=temperature)

    if cfg.provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except Exception as exc:
            raise RuntimeError(
                "Gemini provider requires the 'langchain-google-genai' package."
            ) from exc

        logger.info("LLM provider: Gemini (model=%s role=%s)", cfg.model, role)
        return ChatGoogleGenerativeAI(
            model=cfg.model,
            google_api_key=cfg.api_key,
            temperature=temperature,
        )

    # OpenAI-compatible providers
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "temperature": temperature,
        "timeout": 45,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    label = cfg.provider.capitalize()
    logger.info("LLM provider: %s (model=%s role=%s)", label, cfg.model, role)
    return ChatOpenAI(**kwargs)


def _effective_order(mode: str, configured_order: list[str]) -> list[str]:
    normalized_mode = (mode or "cloud").lower().strip()
    if normalized_mode == "ollama":
        return ["ollama"]
    return _normalize_order(configured_order)


def _effective_order_for_role(
    mode: str,
    configured_order: list[str],
    role: str | None = None,
    subagent_order: list[str] | None = None,
) -> list[str]:
    """Like :func:`_effective_order` but respects ``{ROLE}_PROVIDER`` env overrides
    and the optional per-role subagent fallback order.

    Priority (highest to lowest):
    1. ``{ROLE}_PROVIDER`` env var — collapses to a single provider
    2. ``subagent_order`` (when role == "subagent" and order is set)
    3. Shared ``configured_order``
    """
    if role:
        forced = os.getenv(f"{role.upper()}_PROVIDER", "").strip().lower()
        if forced and forced in PROVIDERS:
            logger.info("Role '%s' forced to provider '%s'", role, forced)
            return [forced]

    if role == "subagent" and subagent_order:
        return _effective_order(mode, subagent_order)

    return _effective_order(mode, configured_order)


class FallbackLLM:
    def __init__(
        self,
        mode: str,
        fallback_order: list[str],
        role: str | None = None,
        subagent_order: list[str] | None = None,
        purpose: str = "chat",
    ) -> None:
        self.mode = (mode or "cloud").lower().strip()
        self.role = role
        self._purpose = purpose
        self.fallback_order = _effective_order_for_role(
            self.mode, fallback_order, role=role, subagent_order=subagent_order
        )

    def _is_ollama(self, provider: str) -> bool:
        return provider.lower().strip() == "ollama"

    async def ainvoke(self, messages: list[BaseMessage]):
        from .ollama_queue import ollama_chat_slot, ollama_analysis_slot

        last_error: Exception | None = None
        for provider in self.fallback_order:
            model = _provider_model(provider, role=self.role)
            if model is None:
                continue
            try:
                logger.info("LLM invoke provider=%s role=%s purpose=%s", provider, self.role, self._purpose)
                if self._is_ollama(provider) and self._purpose == "analysis":
                    async with ollama_analysis_slot():
                        return await model.ainvoke(messages)
                else:
                    return await model.ainvoke(messages)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM invoke failed provider=%s role=%s error=%s", provider, self.role, exc)

        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Configure at least one provider in backend/.env (or the app settings). "
            "For HuggingFace, set both HF_API_TOKEN and HF_BASE_URL (OpenAI-compatible endpoint)."
        ) from last_error

    async def ainvoke_structured(self, messages: list[BaseMessage], schema: type):
        """Invoke the first healthy provider with structured output enabled."""
        from .ollama_queue import ollama_chat_slot, ollama_analysis_slot

        last_error: Exception | None = None
        for provider in self.fallback_order:
            model = _provider_model(provider, role=self.role)
            if model is None:
                continue
            try:
                logger.info(
                    "LLM structured invoke provider=%s role=%s schema=%s",
                    provider, self.role, getattr(schema, "__name__", str(schema)),
                )
                structured_model = model.with_structured_output(schema)
                if self._is_ollama(provider) and self._purpose == "analysis":
                    async with ollama_analysis_slot():
                        return await structured_model.ainvoke(messages)
                else:
                    return await structured_model.ainvoke(messages)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM structured invoke failed provider=%s role=%s error=%s", provider, self.role, exc)

        raise RuntimeError(
            "No LLM provider available or all providers failed for structured output. "
            "Configure at least one provider in backend/.env (or the app settings)."
        ) from last_error

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        from .ollama_queue import ollama_analysis_slot

        last_error: Exception | None = None
        for provider in self.fallback_order:
            model = _provider_model(provider, role=self.role)
            if model is None:
                continue

            emitted_any = False
            try:
                logger.info("LLM stream provider=%s role=%s purpose=%s", provider, self.role, self._purpose)
                if self._is_ollama(provider) and self._purpose == "analysis":
                    async with ollama_analysis_slot():
                        async for chunk in model.astream(messages):
                            emitted_any = True
                            yield chunk
                else:
                    async for chunk in model.astream(messages):
                        emitted_any = True
                        yield chunk
                return
            except Exception as exc:
                last_error = exc
                logger.warning("LLM stream failed provider=%s role=%s error=%s", provider, self.role, exc)
                if emitted_any:
                    raise RuntimeError(
                        f"Streaming interrupted from provider '{provider}'."
                    ) from exc
                continue

        raise RuntimeError(
            "No LLM provider available or all providers failed. "
            "Configure at least one provider in backend/.env (or the app settings)."
        ) from last_error


def get_llm(role: str | None = None, purpose: str = "chat") -> FallbackLLM:
    """Return a :class:`FallbackLLM` configured for *role*.

    Args:
        role: ``"agent"`` (prose synthesis, cheap/fast model) or
              ``"subagent"`` (tool calling, high-reasoning model).
              ``None`` preserves the original behaviour — global settings only.
        purpose: ``"chat"`` (default) or ``"analysis"`` — controls Ollama
                 queue slot selection.
    """
    mode, fallback_order, subagent_order = load_llm_settings()
    return FallbackLLM(
        mode=mode,
        fallback_order=fallback_order,
        role=role,
        subagent_order=subagent_order,
        purpose=purpose,
    )
