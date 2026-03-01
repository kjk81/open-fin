"""Settings router — CRUD for .env configuration values.

Reads / writes the dotenv file pointed to by ``OPEN_FIN_ENV_PATH`` (or the
project-level ``.env`` as fallback).  Sensitive values (API keys / tokens) are
returned in masked form so they never leak to the frontend in plain text.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, set_key, unset_key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# ---------------------------------------------------------------------------
# Settings schema — single source of truth for all known env vars
# ---------------------------------------------------------------------------

SETTINGS_SCHEMA: list[dict[str, Any]] = [
    # ── Brokerage ────────────────────────────────────────────────────────
    {
        "key": "ALPACA_API_KEY",
        "label": "Alpaca API Key",
        "description": "API key for Alpaca paper / live trading.",
        "type": "secret",
        "category": "Brokerage",
    },
    {
        "key": "ALPACA_API_SECRET",
        "label": "Alpaca API Secret",
        "description": "API secret for Alpaca paper / live trading.",
        "type": "secret",
        "category": "Brokerage",
    },
    {
        "key": "ALPACA_BASE_URL",
        "label": "Alpaca Base URL",
        "description": "Base URL for Alpaca API (paper: https://paper-api.alpaca.markets).",
        "type": "string",
        "category": "Brokerage",
    },
    {
        "key": "ALPACA_WORKER_API_KEY",
        "label": "Worker Alpaca API Key",
        "description": "Separate Alpaca key for the background worker (falls back to main key).",
        "type": "secret",
        "category": "Brokerage",
    },
    {
        "key": "ALPACA_WORKER_API_SECRET",
        "label": "Worker Alpaca API Secret",
        "description": "Separate Alpaca secret for the background worker.",
        "type": "secret",
        "category": "Brokerage",
    },
    # ── LLM Providers ────────────────────────────────────────────────────
    {
        "key": "OLLAMA_MODEL",
        "label": "Ollama Model",
        "description": "Model name for local Ollama inference (e.g. llama3.1:8b).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "OLLAMA_BASE_URL",
        "label": "Ollama Base URL",
        "description": "Base URL for the Ollama server (default: http://localhost:11434).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter API Key",
        "description": "API key for OpenRouter cloud inference.",
        "type": "secret",
        "category": "LLM Providers",
    },
    {
        "key": "OPENROUTER_MODEL",
        "label": "OpenRouter Model",
        "description": "Model identifier on OpenRouter (e.g. mistralai/mistral-7b-instruct).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "GEMINI_API_KEY",
        "label": "Gemini API Key",
        "description": "API key for Google Gemini.",
        "type": "secret",
        "category": "LLM Providers",
    },
    {
        "key": "GEMINI_MODEL",
        "label": "Gemini Model",
        "description": "Gemini model name (e.g. gemini-1.5-flash).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "OPENAI_API_KEY",
        "label": "OpenAI API Key",
        "description": "API key for OpenAI.",
        "type": "secret",
        "category": "LLM Providers",
    },
    {
        "key": "OPENAI_MODEL",
        "label": "OpenAI Model",
        "description": "OpenAI model name (e.g. gpt-4o-mini).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "OPENAI_BASE_URL",
        "label": "OpenAI Base URL",
        "description": "Custom base URL for OpenAI-compatible endpoints.",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "GROQ_API_KEY",
        "label": "Groq API Key",
        "description": "API key for Groq cloud inference.",
        "type": "secret",
        "category": "LLM Providers",
    },
    {
        "key": "GROQ_MODEL",
        "label": "Groq Model",
        "description": "Model name on Groq (e.g. llama3-8b-8192).",
        "type": "string",
        "category": "LLM Providers",
    },
    {
        "key": "HF_API_TOKEN",
        "label": "Hugging Face Token",
        "description": "API token for Hugging Face Inference API.",
        "type": "secret",
        "category": "LLM Providers",
    },
    {
        "key": "HF_MODEL",
        "label": "Hugging Face Model",
        "description": "Model ID on Hugging Face (e.g. mistralai/Mistral-7B-Instruct-v0.2).",
        "type": "string",
        "category": "LLM Providers",
    },
    # ── Data Sources ─────────────────────────────────────────────────────
    {
        "key": "FMP_API_KEY",
        "label": "FMP API Key",
        "description": "API key for Financial Modeling Prep.",
        "type": "secret",
        "category": "Data Sources",
    },
    {
        "key": "TAVILY_API_KEY",
        "label": "Tavily API Key",
        "description": "API key for Tavily web search.",
        "type": "secret",
        "category": "Data Sources",
    },
    {
        "key": "EXA_API_KEY",
        "label": "Exa API Key",
        "description": "API key for Exa search.",
        "type": "secret",
        "category": "Data Sources",
    },
    # ── Anomaly Worker ───────────────────────────────────────────────────
    {
        "key": "ANOMALY_INTERVAL_MINUTES",
        "label": "Check Interval (min)",
        "description": "How often the anomaly worker scans for events, in minutes.",
        "type": "number",
        "category": "Anomaly Worker",
    },
    {
        "key": "ANOMALY_PRICE_DROP",
        "label": "Price Drop Threshold",
        "description": "Fractional price drop to flag as anomaly (e.g. 0.05 = 5 %).",
        "type": "number",
        "category": "Anomaly Worker",
    },
    {
        "key": "ANOMALY_VOLUME_SPIKE",
        "label": "Volume Spike Multiplier",
        "description": "Volume multiplier vs. average to flag as anomaly (e.g. 2.0 = 2×).",
        "type": "number",
        "category": "Anomaly Worker",
    },
    {
        "key": "ANOMALY_GAP_DOWN",
        "label": "Gap-Down Threshold",
        "description": "Fractional gap-down from prior close to flag (e.g. 0.03 = 3 %).",
        "type": "number",
        "category": "Anomaly Worker",
    },
]

# Pre-compute allow-list of valid keys and set of secret-type keys
_ALLOWED_KEYS: set[str] = {s["key"] for s in SETTINGS_SCHEMA}
_SECRET_KEYS: set[str] = {s["key"] for s in SETTINGS_SCHEMA if s["type"] == "secret"}


def _env_path() -> Path:
    """Resolve the .env file path."""
    explicit = os.getenv("OPEN_FIN_ENV_PATH")
    if explicit:
        return Path(explicit)
    # Fallback: project-level .env next to main.py
    return Path(__file__).resolve().parent.parent / ".env"


def _mask(value: str) -> str:
    """Return a masked preview of a secret value."""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _read_env() -> dict[str, str | None]:
    """Read raw key-value pairs from the .env file."""
    p = _env_path()
    if not p.exists():
        return {}
    return dotenv_values(p)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SettingsUpdateRequest(BaseModel):
    values: dict[str, str | None] = Field(
        ..., description="Map of setting key → new value (or null to unset)."
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/settings/schema")
def get_settings_schema() -> list[dict[str, Any]]:
    """Return the full settings schema so the frontend can render dynamically."""
    return SETTINGS_SCHEMA


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Return current values. Secrets are masked; non-secrets returned as-is."""
    raw = _read_env()
    result: dict[str, Any] = {}
    for schema_item in SETTINGS_SCHEMA:
        key = schema_item["key"]
        value = raw.get(key) or os.getenv(key)
        if value is None or value == "":
            result[key] = {"is_set": False, "preview": "", "value": ""}
        elif key in _SECRET_KEYS:
            result[key] = {"is_set": True, "preview": _mask(value), "value": ""}
        else:
            result[key] = {"is_set": True, "preview": value, "value": value}
    return result


@router.put("/settings")
def update_settings(request: SettingsUpdateRequest) -> dict[str, str]:
    """Write settings to the .env file.

    Only keys in the allow-list are accepted.  Empty-string values are treated
    as *unset* (the line is removed from the file).
    """
    env_file = _env_path()

    # Ensure the file exists so set_key / unset_key don't fail
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.touch()

    bad_keys = set(request.values.keys()) - _ALLOWED_KEYS
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"Unknown setting keys: {sorted(bad_keys)}")

    for key, value in request.values.items():
        if value is None or value.strip() == "":
            unset_key(str(env_file), key)
            # Also clear from environment so the process picks up the change
            os.environ.pop(key, None)
        else:
            set_key(str(env_file), key, value)
            os.environ[key] = value

    return {"status": "ok"}
