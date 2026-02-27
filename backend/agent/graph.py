from __future__ import annotations
import logging
from langgraph.graph import StateGraph, START, END
from .state import ChatState
from .nodes import (
    intent_router,
    context_injector,
    ticker_lookup_node,
    generation_node,
)

logger = logging.getLogger(__name__)


def _route_after_context(state: ChatState) -> str:
    """
    Conditional edge: after context_injector, decide whether ticker lookup is needed.

    Routes to ticker_lookup_node when tickers are mentioned AND intent is not
    general_chat (pure chitchat doesn't warrant fundamentals lookup).
    """
    tickers = state.get("tickers_mentioned", [])
    intent = state.get("intent", "general_chat")

    if tickers and intent != "general_chat":
        logger.debug("Graph routing: ticker_lookup_node (tickers=%s)", tickers)
        return "ticker_lookup_node"

    logger.debug("Graph routing: generation_node (no tickers or general chat)")
    return "generation_node"


def build_graph():
    """
    Construct and compile the Open-Fin LangGraph state graph.

    Topology:
        START → intent_router → context_injector
                                      ↓ (conditional)
                           tickers AND not general_chat?
                              Yes → ticker_lookup_node → generation_node → END
                              No  →                      generation_node → END
    """
    builder = StateGraph(ChatState)

    builder.add_node("intent_router", intent_router)
    builder.add_node("context_injector", context_injector)
    builder.add_node("ticker_lookup_node", ticker_lookup_node)
    builder.add_node("generation_node", generation_node)

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "context_injector")

    builder.add_conditional_edges(
        "context_injector",
        _route_after_context,
        {
            "ticker_lookup_node": "ticker_lookup_node",
            "generation_node": "generation_node",
        },
    )

    builder.add_edge("ticker_lookup_node", "generation_node")
    builder.add_edge("generation_node", END)

    return builder.compile()


# Compiled once at import time — imported directly by routers/chat.py
graph = build_graph()
