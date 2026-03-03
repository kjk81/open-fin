"""Phase 2 conftest — stub out heavy transitive dependencies.

tools/__init__.py imports from sec_filings, finance, edgar etc. which pull in
langchain_core, yfinance and other heavyweight packages.  We inject MagicMock
stubs so that ``import tools.web`` (and ``tools._utils``) succeed in the test
process without those packages being installed.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

_HEAVY_MODULES = [
    "langchain_core",
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


# Provide real lightweight message classes so isinstance() works in agent.graph
class _BaseMessage:
    def __init__(self, content: str = ""):
        self.content = content


class HumanMessage(_BaseMessage):
    pass


class AIMessage(_BaseMessage):
    pass


class SystemMessage(_BaseMessage):
    pass


class AIMessageChunk(_BaseMessage):
    pass


class BaseMessage(_BaseMessage):
    pass


class ToolMessage(_BaseMessage):
    def __init__(self, content: str = "", tool_call_id: str = ""):
        super().__init__(content)
        self.tool_call_id = tool_call_id


_messages_mod = MagicMock()
_messages_mod.HumanMessage = HumanMessage
_messages_mod.AIMessage = AIMessage
_messages_mod.SystemMessage = SystemMessage
_messages_mod.AIMessageChunk = AIMessageChunk
_messages_mod.BaseMessage = BaseMessage
_messages_mod.ToolMessage = ToolMessage
sys.modules["langchain_core.messages"] = _messages_mod


# Patch langgraph.graph attributes used in agent.graph/state
import langgraph.graph as _lg  # type: ignore

_lg.StateGraph = MagicMock()
_lg.START = "START"
_lg.END = "END"

import langgraph.graph.message as _lgm  # type: ignore

_lgm.add_messages = lambda x, y: x + y
