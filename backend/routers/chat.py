from __future__ import annotations
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from agent.graph import graph
from agent.knowledge_graph import upsert_from_tool_results
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
    Run the LangGraph workflow and yield SSE-formatted strings.

    Event types emitted:
      tool_start  — a tool call has begun
      tool_end    — a tool call completed (with duration + success flag)
      token       — a single LLM output token
      sources     — aggregated SourceRef list after graph completes
      kg_update   — KG nodes/edges created during post-processing
      done        — stream finished
      error       — unrecoverable error
    """
    initial_state: dict = {
        "messages": [HumanMessage(content=request.message)],
        "intent": "",
        "tickers_mentioned": [],
        "context_refs": request.context_refs,
        "injected_context": "",
        "ticker_reports": {},
        "session_id": request.session_id,
        # AgentState fields for the finance tool loop
        "current_query": "",
        "active_skills": [],
        "tool_call_count": 0,
        "tool_results": [],
    }

    # Accumulators filled during streaming
    accumulated_tool_results: list[dict] = []
    accumulated_sources: list[dict] = []
    tool_start_times: dict[str, float] = {}

    try:
        async for event in graph.astream_events(initial_state, version="v2"):
            evt: str = event.get("event", "")
            name: str = event.get("name", "")
            data: dict = event.get("data", {})

            if evt == "on_tool_start":
                tool_start_times[name] = time.monotonic()
                raw_input = data.get("input") or {}
                # Keep args summary concise for the frontend chip
                args_preview = {k: v for k, v in list(raw_input.items())[:3]}
                yield _sse({"type": "tool_start", "tool": name, "args": args_preview})

            elif evt == "on_tool_end":
                started = tool_start_times.pop(name, time.monotonic())
                duration_ms = int((time.monotonic() - started) * 1000)
                output = data.get("output") or ""
                success = True

                if isinstance(output, str) and output:
                    try:
                        parsed = json.loads(output)
                        success = bool(parsed.get("success", True))
                        # Collect for KG post-processing
                        accumulated_tool_results.append({
                            "tool": name,
                            "args": data.get("input") or {},
                            "result": output,
                        })
                        # Collect citations — deduplicated by URL
                        for src in parsed.get("sources") or []:
                            url = src.get("url") or ""
                            if url and not any(s["url"] == url for s in accumulated_sources):
                                accumulated_sources.append({"url": url, "title": src.get("title") or url})
                    except json.JSONDecodeError:
                        pass

                yield _sse({"type": "tool_end", "tool": name, "duration_ms": duration_ms, "success": success})

            elif evt == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield _sse({"type": "token", "content": chunk.content})

        # ── Post-graph side-effects ────────────────────────────────────────
        kg_result: dict = {"nodes_created": 0, "edges_created": 0, "node_ids": []}
        if accumulated_tool_results:
            try:
                kg_result = await upsert_from_tool_results(
                    accumulated_tool_results,
                    extra_sources=accumulated_sources,
                )
            except Exception as exc:
                logger.error("KG post-processing error: %s", exc, exc_info=True)

        if accumulated_sources:
            yield _sse({"type": "sources", "sources": accumulated_sources})

        if kg_result.get("nodes_created", 0) > 0 or kg_result.get("edges_created", 0) > 0:
            yield _sse({
                "type": "kg_update",
                "nodes_created": kg_result["nodes_created"],
                "edges_created": kg_result["edges_created"],
            })

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
