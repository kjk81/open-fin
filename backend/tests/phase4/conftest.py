"""Phase 4 conftest — stub heavy transitive deps."""

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
    "langgraph.graph.message",
    "langgraph.prebuilt",
    "yfinance",
    "tavily",
    "exa_py",
    "alpaca_trade_api",
    "faiss",
    "fastembed",
]

for _mod in _HEAVY_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
