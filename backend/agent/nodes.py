from __future__ import annotations
import re
import logging
from datetime import datetime, timedelta
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from .state import ChatState
from .llm import get_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker stopwords — uppercase sequences the regex would false-positive on
# ---------------------------------------------------------------------------
_TICKER_STOPWORDS: frozenset[str] = frozenset({
    "I", "A", "AN", "AM", "IS", "BE", "DO", "GO",
    "IT", "NO", "OK", "OR", "SO", "US", "TO", "IN",
    "ON", "AT", "BY", "MY", "ME", "HE", "WE", "IF",
    "UP", "AS", "OF", "PM", "TV", "AI",
    "HR", "PR", "UK", "EU", "FY", "QE",
    "YOY", "QOQ", "MOM", "IPO", "ETF",
    "CEO", "CFO", "CTO", "COO", "CMO",
    "SEC", "IRS", "FED", "GDP", "CPI", "PPI", "NFP",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU",
    "ALL", "CAN", "HER", "WAS", "ONE", "OUR", "OUT",
    "DAY", "GET", "HAS", "HIM", "HIS", "HOW", "ITS",
    "MAY", "NEW", "NOW", "OLD", "OWN", "SAY", "SHE",
    "TWO", "WAY", "WHO", "DID", "INC", "LTD", "LLC",
})

_TICKER_RE = re.compile(r'\b[A-Z]{1,5}\b')

# ---------------------------------------------------------------------------
# Intent and context keyword sets
# ---------------------------------------------------------------------------
_TRADE_KEYWORDS = frozenset({
    "buy", "sell", "trade", "recommend", "purchase", "short", "cover", "long",
})
_DEEP_DIVE_KEYWORDS = frozenset({
    "analysis", "deep-dive", "deep dive", "research", "fundamentals",
    "analyze", "breakdown", "outlook", "report", "evaluate",
})
_PORTFOLIO_KEYWORDS = frozenset({
    "portfolio", "holdings", "positions", "my stocks", "my holdings", "my shares",
})


# ---------------------------------------------------------------------------
# Node 1: IntentRouter (rule-based, no LLM call)
# ---------------------------------------------------------------------------
async def intent_router(state: ChatState) -> dict:
    """Classify intent and extract tickers + context refs from the user message."""
    user_text = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            user_text = msg.content
            break

    lower = user_text.lower()

    # --- Classify intent ---
    if any(kw in lower for kw in _TRADE_KEYWORDS):
        intent = "trade_recommendation"
    elif any(kw in lower for kw in _DEEP_DIVE_KEYWORDS):
        intent = "ticker_deep_dive"
    else:
        intent = "general_chat"

    # --- Extract tickers ---
    raw = _TICKER_RE.findall(user_text)
    seen: set[str] = set()
    tickers: list[str] = []
    for t in raw:
        if t not in _TICKER_STOPWORDS and t not in seen:
            seen.add(t)
            tickers.append(t)

    # --- Context refs (merge with any already supplied by the API caller) ---
    context_refs: list[str] = list(state.get("context_refs", []))
    if any(kw in lower for kw in _PORTFOLIO_KEYWORDS):
        if "user_portfolio" not in context_refs:
            context_refs.append("user_portfolio")

    logger.info(
        "IntentRouter: intent=%s tickers=%s context_refs=%s",
        intent, tickers, context_refs,
    )
    return {
        "intent": intent,
        "tickers_mentioned": tickers,
        "context_refs": context_refs,
    }


# ---------------------------------------------------------------------------
# Node 2: ContextInjector
# ---------------------------------------------------------------------------
async def context_injector(state: ChatState) -> dict:
    """Fetch named context from the DB and format it for the system prompt."""
    if "user_portfolio" not in state.get("context_refs", []):
        return {"injected_context": ""}

    from database import SessionLocal
    from models import UserPortfolio

    db = SessionLocal()
    try:
        positions = db.query(UserPortfolio).all()
        if not positions:
            injected = "The user currently has no open portfolio positions."
        else:
            lines = ["User's current portfolio:"]
            for p in positions:
                market_val = round(p.qty * p.current_price, 2)
                lines.append(
                    f"  - {p.symbol}: {p.qty} shares @ avg ${p.avg_entry_price:.2f}, "
                    f"current ${p.current_price:.2f}, value ${market_val:,.2f}"
                )
            injected = "\n".join(lines)
        logger.info("ContextInjector: injected %d positions", len(positions))
    except Exception as exc:
        logger.warning("ContextInjector DB error: %s", exc)
        injected = ""
    finally:
        db.close()

    return {"injected_context": injected}


# ---------------------------------------------------------------------------
# Node 3: TickerLookupNode
# ---------------------------------------------------------------------------
async def ticker_lookup_node(state: ChatState) -> dict:
    """
    For each ticker: check ReportCache (7-day TTL).
    On miss: fetch yfinance fundamentals, synthesize via LLM, cache the result.
    """
    tickers = state.get("tickers_mentioned", [])
    if not tickers:
        return {"ticker_reports": {}}

    import yfinance as yf
    from database import SessionLocal
    from models import ReportCache

    llm = get_llm()
    db = SessionLocal()
    ticker_reports: dict[str, str] = {}
    cache_cutoff = datetime.utcnow() - timedelta(days=7)

    try:
        for symbol in tickers:
            # --- Cache lookup ---
            cached: ReportCache | None = (
                db.query(ReportCache)
                .filter(ReportCache.ticker == symbol)
                .first()
            )
            if cached and cached.generated_at > cache_cutoff:
                logger.info("TickerLookupNode: cache hit for %s", symbol)
                ticker_reports[symbol] = cached.report_text
                continue

            # --- Fetch fundamentals ---
            logger.info("TickerLookupNode: fetching yfinance data for %s", symbol)
            try:
                info = yf.Ticker(symbol).info
            except Exception as exc:
                logger.warning("yfinance error for %s: %s", symbol, exc)
                ticker_reports[symbol] = f"Could not fetch live data for {symbol}."
                continue

            fundamentals = _format_fundamentals(symbol, info)

            # --- LLM synthesis (non-streaming, awaited to completion) ---
            synthesis_prompt = (
                f"You are a financial analyst. Write a concise 2-3 sentence fundamental "
                f"analysis paragraph for {symbol} based on the following data. "
                f"Focus on valuation, growth prospects, and key risks.\n\n"
                f"{fundamentals}"
            )
            try:
                response = await llm.ainvoke([HumanMessage(content=synthesis_prompt)])
                report_text = response.content
            except Exception as exc:
                logger.warning("LLM synthesis error for %s: %s", symbol, exc)
                report_text = fundamentals  # Fallback: raw data

            # --- Upsert ReportCache ---
            try:
                if cached:
                    cached.report_text = report_text
                    cached.generated_at = datetime.utcnow()
                else:
                    db.add(ReportCache(
                        ticker=symbol,
                        report_text=report_text,
                        generated_at=datetime.utcnow(),
                    ))
                db.commit()
                logger.info("TickerLookupNode: cached new report for %s", symbol)
            except Exception as exc:
                logger.warning("Cache write error for %s: %s", symbol, exc)
                db.rollback()

            ticker_reports[symbol] = report_text

    finally:
        db.close()

    return {"ticker_reports": ticker_reports}


def _format_fundamentals(symbol: str, info: dict) -> str:
    """Format yfinance info dict into a readable string for the LLM prompt."""
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    lines = [f"Ticker: {symbol}"]

    if info.get("longName"):
        lines.append(f"Company: {info['longName']}")
    if info.get("sector"):
        lines.append(f"Sector: {info['sector']} / {info.get('industry', 'N/A')}")
    if price:
        lines.append(f"Current Price: ${price:.2f}")
    if info.get("marketCap"):
        lines.append(f"Market Cap: {_fmt_large(info['marketCap'])}")
    if info.get("trailingPE"):
        lines.append(f"P/E (TTM): {info['trailingPE']:.2f}")
    if info.get("forwardPE"):
        lines.append(f"Forward P/E: {info['forwardPE']:.2f}")
    if info.get("revenueGrowth"):
        lines.append(f"Revenue Growth (YoY): {info['revenueGrowth'] * 100:.1f}%")
    if info.get("grossMargins"):
        lines.append(f"Gross Margin: {info['grossMargins'] * 100:.1f}%")
    if info.get("beta"):
        lines.append(f"Beta: {info['beta']:.2f}")
    if info.get("fiftyTwoWeekHigh"):
        lines.append(f"52W High: ${info['fiftyTwoWeekHigh']:.2f}")
    if info.get("fiftyTwoWeekLow"):
        lines.append(f"52W Low: ${info['fiftyTwoWeekLow']:.2f}")
    if info.get("dividendYield"):
        lines.append(f"Dividend Yield: {info['dividendYield'] * 100:.2f}%")
    if info.get("shortPercentOfFloat"):
        lines.append(f"Short % of Float: {info['shortPercentOfFloat'] * 100:.1f}%")

    return "\n".join(lines)


def _fmt_large(n) -> str:
    if n is None:
        return "N/A"
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"


# ---------------------------------------------------------------------------
# Node 4: GenerationNode
# ---------------------------------------------------------------------------
async def generation_node(state: ChatState) -> dict:
    """
    Build the final system prompt, load chat history, call the LLM with
    streaming (captured by astream_events at the graph level), and persist
    the exchange to ChatHistory.
    """
    from database import SessionLocal
    from models import ChatHistory

    session_id = state["session_id"]
    intent = state.get("intent", "general_chat")
    injected_context = state.get("injected_context", "")
    ticker_reports = state.get("ticker_reports", {})

    # --- Build system prompt ---
    system_parts = [
        "You are Open-Fin, an expert financial AI co-pilot running as a desktop application. "
        "You provide accurate, data-driven financial analysis and trading insights. "
        "Be concise, precise, and professional. "
        "Always clarify that your responses are informational and not financial advice.",
    ]

    if injected_context:
        system_parts.append(f"\n\nCURRENT USER PORTFOLIO:\n{injected_context}")

    if ticker_reports:
        report_lines = ["\n\nTICKER ANALYSIS:"]
        for sym, report in ticker_reports.items():
            report_lines.append(f"\n[{sym}]\n{report}")
        system_parts.append("".join(report_lines))

    if intent == "trade_recommendation":
        system_parts.append(
            '\n\nWhen recommending trades, format each recommendation as: '
            '[TRADE: {"action": "BUY", "ticker": "AAPL", "qty": 10}]'
        )

    system_prompt = "".join(system_parts)

    # --- Load recent chat history for this session ---
    db = SessionLocal()
    history_messages: list = [SystemMessage(content=system_prompt)]
    try:
        rows = (
            db.query(ChatHistory)
            .filter(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.created_at.asc())
            .limit(10)
            .all()
        )
        for row in rows:
            if row.role == "user":
                history_messages.append(HumanMessage(content=row.content))
            elif row.role == "assistant":
                history_messages.append(AIMessage(content=row.content))
            elif row.role == "system":
                history_messages.append(SystemMessage(content=row.content))
    except Exception as exc:
        logger.warning("Failed to load chat history for session %s: %s", session_id, exc)
    finally:
        db.close()

    # Current user message is the last HumanMessage in state
    current_user_text = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            current_user_text = msg.content
            break
    history_messages.append(HumanMessage(content=current_user_text))

    # --- Streaming LLM call ---
    # Tokens are captured by astream_events("on_chat_model_stream") in the SSE endpoint
    llm = get_llm()
    full_response = ""
    async for chunk in llm.astream(history_messages):
        if chunk.content:
            full_response += chunk.content

    # --- Persist exchange to ChatHistory ---
    db = SessionLocal()
    try:
        db.add(ChatHistory(
            session_id=session_id,
            role="user",
            content=current_user_text,
            created_at=datetime.utcnow(),
        ))
        db.add(ChatHistory(
            session_id=session_id,
            role="assistant",
            content=full_response,
            created_at=datetime.utcnow(),
        ))
        db.commit()
    except Exception as exc:
        logger.warning("Failed to save chat history: %s", exc)
        db.rollback()
    finally:
        db.close()

    return {"messages": [AIMessage(content=full_response)]}
