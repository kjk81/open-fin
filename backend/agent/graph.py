from __future__ import annotations
import logging
from langgraph.graph import StateGraph, START, END
from .state import ChatState
from .nodes import (
    intent_router,
    context_injector,
    ticker_lookup_node,
    screening_node,
    filings_node,
    generation_node,
)

logger = logging.getLogger(__name__)


def _route_after_context(state: ChatState) -> str:
    """
    Conditional edge: after context_injector, decide which processing node is needed.

    Routes to:
    - screening_node when intent is stock_screening
    - ticker_lookup_node when tickers are mentioned AND intent is not general_chat
    - generation_node otherwise (pure chitchat)
    """
    tickers = state.get("tickers_mentioned", [])
    intent = state.get("intent", "general_chat")

    # Stock screening requests always go to the screening node
    if intent == "stock_screening":
        logger.debug("Graph routing: screening_node (intent=%s)", intent)
        return "screening_node"

    if intent == "sec_filings":
        logger.debug("Graph routing: filings_node (intent=%s)", intent)
        return "filings_node"

    # If the user explicitly requested ticker context via @TICKER mentions,
    # we should honor that even for general chat.
    explicit_refs = [r for r in state.get("context_refs", []) if r and r != "user_portfolio"]
    has_explicit_ticker_ref = len(explicit_refs) > 0

    if tickers and (intent != "general_chat" or has_explicit_ticker_ref):
        logger.debug(
            "Graph routing: ticker_lookup_node (tickers=%s intent=%s explicit=%s)",
            tickers,
            intent,
            has_explicit_ticker_ref,
        )
        return "ticker_lookup_node"

    logger.debug("Graph routing: generation_node (no tickers or general chat)")
    return "generation_node"


def build_graph():
    """
    Construct and compile the Open-Fin LangGraph state graph.

    Topology:
        START → intent_router → context_injector
                                      ↓ (conditional)
                           intent == "stock_screening"?
                              Yes → screening_node → generation_node → END
                           tickers AND not general_chat?
                              Yes → ticker_lookup_node → generation_node → END
                              No  →                      generation_node → END
    """
    builder = StateGraph(ChatState)

    builder.add_node("intent_router", intent_router)
    builder.add_node("context_injector", context_injector)
    builder.add_node("ticker_lookup_node", ticker_lookup_node)
    builder.add_node("screening_node", screening_node)
    builder.add_node("filings_node", filings_node)
    builder.add_node("generation_node", generation_node)

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "context_injector")

    builder.add_conditional_edges(
        "context_injector",
        _route_after_context,
        {
            "ticker_lookup_node": "ticker_lookup_node",
            "screening_node": "screening_node",
            "filings_node": "filings_node",
            "generation_node": "generation_node",
        },
    )

    builder.add_edge("ticker_lookup_node", "generation_node")
    builder.add_edge("screening_node", "generation_node")
    builder.add_edge("filings_node", "generation_node")
    builder.add_edge("generation_node", END)

    return builder.compile()


# Compiled once at import time — imported directly by routers/chat.py
graph = build_graph()
