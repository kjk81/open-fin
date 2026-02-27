from __future__ import annotations
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from agent.graph import graph
from database import SessionLocal
from models import ChatHistory
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: str = Field(..., min_length=1, max_length=64)
    context_refs: list[str] = Field(default_factory=list)


class SystemEventRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=1000)


def _sse(data: dict) -> str:
    """Format a dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(data)}\n\n"


async def _stream_graph(request: ChatRequest) -> AsyncGenerator[str, None]:
    """
    Run the LangGraph and yield SSE-formatted strings.

    astream_events(version="v2") emits granular per-token events.
    We filter for "on_chat_model_stream" which fires for each token produced
    inside generation_node's llm.astream() call.
    """
    initial_state: dict = {
        "messages": [HumanMessage(content=request.message)],
        "intent": "",
        "tickers_mentioned": [],
        "context_refs": request.context_refs,
        "injected_context": "",
        "ticker_reports": {},
        "session_id": request.session_id,
    }

    try:
        async for event in graph.astream_events(initial_state, version="v2"):
            if event.get("event") == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield _sse({"type": "token", "content": chunk.content})

        yield _sse({"type": "done"})

    except RuntimeError as exc:
        # No LLM provider configured
        logger.error("Chat stream RuntimeError: %s", exc)
        yield _sse({"type": "error", "content": str(exc)})
    except Exception as exc:
        logger.error("Chat stream error: %s", exc, exc_info=True)
        yield _sse({"type": "error", "content": "An internal error occurred."})


@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    POST /api/chat

    Stream an AI response for a user message using Server-Sent Events.

    Request body:
        {
            "message": "What do you think of AAPL earnings?",
            "session_id": "user-abc123",
            "context_refs": ["user_portfolio"]   // optional
        }

    Response: text/event-stream
        data: {"type": "token", "content": "Apple"}
        data: {"type": "token", "content": " reported"}
        ...
        data: {"type": "done"}

    On error:
        data: {"type": "error", "content": "No LLM provider configured..."}
    """
    return StreamingResponse(
        _stream_graph(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Prevents nginx from buffering the stream
            "Connection": "keep-alive",
        },
    )


@router.post("/chat/system_event")
def chat_system_event(request: SystemEventRequest):
    db = SessionLocal()
    try:
        db.add(ChatHistory(
            session_id=request.session_id,
            role="system",
            content=request.content,
            created_at=datetime.utcnow(),
        ))
        db.commit()
        return {"ok": True}
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist system event: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to persist system event")
    finally:
        db.close()
