"""Phase 8 conftest — stub heavy deps like other phases, but lighter.

Build-process tests exercise pathutil, entry points, spec files, build script,
and .env.example completeness. They don't need LLM / FAISS / yfinance at
import time, so we stub them to keep tests fast and dependency-free.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

_HEAVY_MODULES = [
    "langchain_core",
    "langchain_core.messages",
    "langchain_core.tools",
    "langchain_core.language_models",
    "langchain_core.language_models.chat_models",
    "langchain_openai",
    "langchain_google_genai",
    "langchain_ollama",
    "langchain",
    "langchain.tools",
    "langgraph",
    "langgraph.graph",
    "langgraph.prebuilt",
    "langgraph.graph.message",
    "yfinance",
    "tavily",
    "exa_py",
    "alpaca_trade_api",
    "faiss",
    "fastembed",
    "apscheduler",
    "apscheduler.schedulers.blocking",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
]

for _mod in _HEAVY_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
