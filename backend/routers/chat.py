from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import HumanMessage, ToolMessage

from agent.graph import describe_graph_stage, graph
from agent.knowledge_graph import upsert_from_tool_results
from database import SessionLocal
from models import ChatHistory
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_ALLOWED_CONTEXT_REFS: frozenset[str] = frozenset({"user_portfolio"})
_MAX_CONTEXT_REFS = 20

# Timeout (seconds) for the LangGraph streaming call.
GRAPH_STREAM_TIMEOUT: float = 120.0


def _validate_session_id(v: str) -> str:
    if not _UUID_RE.match(v):
        raise ValueError("session_id must be a valid UUID")
    return v


def _validate_context_refs(v: list[str]) -> list[str]:
    if len(v) > _MAX_CONTEXT_REFS:
        raise ValueError(f"context_refs must have at most {_MAX_CONTEXT_REFS} items")
    for ref in v:
        if ref in _ALLOWED_CONTEXT_REFS:
            continue
        # Allow valid ticker-formatted refs (e.g. "AAPL", "BRK.B")
        if _TICKER_RE.match(ref.upper()):
            continue
        raise ValueError(
            f"Invalid context_ref '{ref}': must be a known keyword or a valid ticker symbol"
        )
    return v


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: str = Field(..., min_length=1, max_length=64)
    context_refs: list[str] = Field(default_factory=list)

    _check_session_id = field_validator("session_id")(_validate_session_id)
    _check_context_refs = field_validator("context_refs")(_validate_context_refs)


class SystemEventRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=1000)

    _check_session_id = field_validator("session_id")(_validate_session_id)


def _sse(data: dict) -> str:
    """Format a dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(data)}\n\n"


async def _stream_graph(request: ChatRequest) -> AsyncGenerator[str, None]:
    """
    Run the LangGraph workflow and yield SSE-formatted strings.

    Event types emitted:
            step        — concise step-by-step progress update for chat UI
            status      — verbose execution status update for terminal UI
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
    tool_args_cache: dict[str, dict] = {}   # name -> args from on_tool_start
    event_seq = 0
    tool_step_ids: dict[str, list[str]] = defaultdict(list)
    stage_step_ids: dict[str, str] = {}

    def emit(data: dict[str, Any]) -> str:
        nonlocal event_seq
        event_seq += 1
        return _sse({"seq": event_seq, **data})

    def tool_human_label(tool_name: str) -> str:
        return tool_name.replace("_", " ")

    try:
        yield emit({
            "type": "status",
            "state": "running",
            "phase": "stream",
            "message": "Starting agent pipeline",
            "verbose": True,
        })

        event_iter = graph.astream_events(initial_state, version="v2").__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(
                    event_iter.__anext__(), timeout=GRAPH_STREAM_TIMEOUT
                )
            except StopAsyncIteration:
                break

            evt: str = event.get("event", "")
            name: str = event.get("name", "")
            data: dict = event.get("data", {})

            if evt == "on_chain_start":
                stage_message = describe_graph_stage(name, "start")
                if stage_message:
                    step_id = f"stage-{name}-{event_seq + 1}"
                    stage_step_ids[name] = step_id
                    yield emit({
                        "type": "status",
                        "step_id": step_id,
                        "state": "running",
                        "phase": name,
                        "message": stage_message,
                        "verbose": True,
                    })

            elif evt == "on_chain_end":
                # Fallback: capture tool_results from any node's on_chain_end
                # output in case on_tool_end event parsing failed or tools were
                # executed programmatically (e.g. fallback_tool_execution).
                # Previously only checked execute_tool_calls (RC6); now generic.
                chain_output = data.get("output") or {}
                if isinstance(chain_output, dict):
                    fallback_results = chain_output.get("tool_results") or []
                    if fallback_results:
                        existing_keys = {
                            (r["tool"], r.get("result", "")[:100])
                            for r in accumulated_tool_results
                        }
                        for fr in fallback_results:
                            if not isinstance(fr, dict) or not fr.get("tool"):
                                continue
                            fkey = (fr["tool"], fr.get("result", "")[:100])
                            if fkey not in existing_keys:
                                accumulated_tool_results.append(fr)
                                logger.debug(
                                    "Fallback: captured tool_result for %s from %s on_chain_end",
                                    fr["tool"], name,
                                )

                stage_message = describe_graph_stage(name, "end")
                if stage_message:
                    step_id = stage_step_ids.pop(name, f"stage-{name}-{event_seq + 1}")
                    yield emit({
                        "type": "status",
                        "step_id": step_id,
                        "state": "done",
                        "phase": name,
                        "message": stage_message,
                        "verbose": True,
                    })

            elif evt == "on_tool_start":
                tool_start_times[name] = time.monotonic()
                raw_input = data.get("input") or {}
                tool_args_cache[name] = raw_input
                # Keep args summary concise for the frontend chip
                args_preview = {k: v for k, v in list(raw_input.items())[:3]}
                step_id = f"tool-{name}-{event_seq + 1}"
                tool_step_ids[name].append(step_id)

                yield emit({
                    "type": "step",
                    "step_id": step_id,
                    "category": "tool",
                    "tool": name,
                    "state": "running",
                    "message": f"Fetching data via {tool_human_label(name)}",
                })
                yield emit({"type": "tool_start", "tool": name, "args": args_preview})

            elif evt == "on_tool_end":
                started = tool_start_times.pop(name, time.monotonic())
                duration_ms = int((time.monotonic() - started) * 1000)
                success = True
                step_id = (
                    tool_step_ids[name].pop(0)
                    if tool_step_ids.get(name)
                    else f"tool-{name}-{event_seq + 1}"
                )

                # --- Robust output extraction (RC1 fix) ---
                # LangGraph may emit output as str, ToolMessage, or other types
                raw_output = data.get("output")
                output_str: str = ""
                if isinstance(raw_output, str):
                    output_str = raw_output
                elif isinstance(raw_output, ToolMessage):
                    content = raw_output.content
                    output_str = content if isinstance(content, str) else json.dumps(content) if content else ""
                elif hasattr(raw_output, "content"):
                    # Duck-type for any message-like object
                    content = raw_output.content
                    output_str = content if isinstance(content, str) else json.dumps(content) if content else ""
                elif raw_output is not None:
                    output_str = str(raw_output)

                if output_str:
                    try:
                        parsed = json.loads(output_str)
                        # Normalize list-shaped output to dict wrapper
                        if isinstance(parsed, list):
                            parsed = {"data": parsed, "success": True}
                        # Guard against non-dict parsed values (RC2 fix)
                        if isinstance(parsed, dict):
                            success = bool(parsed.get("success", True))
                        else:
                            parsed = {"data": parsed, "success": True}
                        # Use cached args from on_tool_start (RC3 fix)
                        tool_args = tool_args_cache.pop(name, None) or data.get("input") or {}
                        # Collect for KG post-processing
                        accumulated_tool_results.append({
                            "tool": name,
                            "args": tool_args,
                            "result": json.dumps(parsed),
                        })
                        # Collect citations — deduplicated by URL
                        if isinstance(parsed, dict):
                            for src in parsed.get("sources") or []:
                                url = src.get("url") or ""
                                if url and not any(s["url"] == url for s in accumulated_sources):
                                    accumulated_sources.append({"url": url, "title": src.get("title") or url})
                    except Exception as exc:
                        # RC2 fix: catch ALL exceptions, not just JSONDecodeError
                        logger.debug("on_tool_end parse error for %s: %s", name, exc)

                yield emit({
                    "type": "step",
                    "step_id": step_id,
                    "category": "tool",
                    "tool": name,
                    "state": "done" if success else "error",
                    "duration_ms": duration_ms,
                    "message": (
                        f"Completed {tool_human_label(name)}"
                        if success
                        else f"{tool_human_label(name)} failed"
                    ),
                })
                yield emit({"type": "tool_end", "tool": name, "duration_ms": duration_ms, "success": success})

            elif evt == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    content = chunk.content
                    # LangChain AIMessageChunk.content can be a list of dicts
                    # (structured content blocks) for some providers. Normalize
                    # to a plain string before forwarding to the frontend.
                    if isinstance(content, list):
                        content = "".join(
                            item.get("text", "") if isinstance(item, dict) else str(item)
                            for item in content
                        )
                    elif not isinstance(content, str):
                        content = str(content)
                    if content:
                        yield emit({"type": "token", "content": content})

        # ── Post-graph side-effects ────────────────────────────────────────
        yield emit({
            "type": "status",
            "state": "running",
            "phase": "post_process",
            "message": "Finalizing citations and knowledge graph updates",
            "verbose": True,
        })

        kg_result: dict = {"nodes_created": 0, "edges_created": 0, "node_ids": []}
        kg_error: str | None = None
        tool_names_collected = [r.get("tool") for r in accumulated_tool_results if isinstance(r, dict)]
        logger.info(
            "KG post-processing: %d accumulated tool result(s) to process: %s",
            len(accumulated_tool_results),
            tool_names_collected or "(none)",
        )
        if accumulated_tool_results:
            try:
                kg_result = await upsert_from_tool_results(
                    accumulated_tool_results,
                    extra_sources=accumulated_sources,
                )
            except Exception as exc:
                logger.error("KG post-processing error: %s", exc, exc_info=True)
                kg_error = str(exc)

        if accumulated_sources:
            yield emit({"type": "sources", "sources": accumulated_sources})

        # RC5 fix: always emit kg_update so frontend knows post-processing
        # completed and can refresh the graph explorer, even when counts are 0.
        kg_event: dict[str, Any] = {
            "type": "kg_update",
            "nodes_created": kg_result.get("nodes_created", 0),
            "edges_created": kg_result.get("edges_created", 0),
        }
        if kg_error:
            kg_event["error"] = kg_error
        yield emit(kg_event)
        yield emit({
            "type": "status",
            "state": "done",
            "phase": "stream",
            "message": "Agent pipeline complete",
            "verbose": True,
        })
        yield emit({"type": "done"})

    except asyncio.TimeoutError:
        logger.error("Chat stream timed out after %.0fs.", GRAPH_STREAM_TIMEOUT)
        yield emit({
            "type": "status",
            "state": "error",
            "phase": "stream",
            "message": "Agent response incomplete due to timeout",
            "verbose": True,
        })
        yield emit({
            "type": "error",
            "content": "The request timed out. Please try again.",
            "detail": f"TimeoutError: Graph stream exceeded {GRAPH_STREAM_TIMEOUT}s",
        })
    except RuntimeError as exc:
        # FallbackLLM raises RuntimeError with a user-actionable message when
        # no provider is configured or all providers fail. Surface it directly.
        logger.error("Chat stream RuntimeError: %s", exc)
        yield emit({
            "type": "status",
            "state": "error",
            "phase": "stream",
            "message": "Agent response incomplete due to runtime failure",
            "verbose": True,
        })
        yield emit({
            "type": "error",
            "content": str(exc),
            "detail": f"RuntimeError: {exc}",
        })
    except Exception as exc:
        logger.error("Chat stream error: %s", exc, exc_info=True)
        yield emit({
            "type": "status",
            "state": "error",
            "phase": "stream",
            "message": "Agent response incomplete due to unexpected error",
            "verbose": True,
        })
        yield emit({
            "type": "error",
            "content": f"An error occurred ({type(exc).__name__}). Check the terminal for details.",
            "detail": f"{type(exc).__name__}: {exc}",
        })


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
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        return {"ok": True}
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist system event: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to persist system event")
    finally:
        db.close()
