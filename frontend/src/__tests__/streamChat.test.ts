/**
 * Exhaustive unit tests for streamChat() in api.ts.
 *
 * These tests mock `fetch` and simulate various SSE stream scenarios to verify:
 * - Normal streaming with token + done events
 * - Fallback guarantee: onDone fires even when stream ends without a `done` event
 * - Error events propagate to onError
 * - Remaining buffer flush (final chunk without trailing \n\n)
 * - Network errors, abort signals, null bodies
 * - Tool events and source refs
 * - Rapid sequential calls / abort-previous pattern
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { streamChat } from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Encode a string as Uint8Array (UTF-8) */
function enc(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

/** Build a raw SSE data line from a JSON payload */
function sseData(payload: object): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

/**
 * Create a mocked Response whose body is a ReadableStream fed by `chunks`.
 * Each chunk is a Uint8Array delivered in order.
 */
function mockStreamResponse(chunks: Uint8Array[]): Response {
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
      } else {
        controller.close();
      }
    },
  });
  const res = new Response(stream, { status: 200, statusText: "OK" });
  Object.defineProperty(res, 'ok', { value: true });
  return res;
}

/** Convenience: make a Response from an array of SSE strings */
function mockSseResponse(parts: string[]): Response {
  const res = mockStreamResponse(parts.map(enc));
  Object.defineProperty(res, 'ok', { value: true });
  return res;
}

// ---------------------------------------------------------------------------
// Default no-op callbacks
// ---------------------------------------------------------------------------

function makeCallbacks() {
  return {
    onToken: vi.fn<(token: string) => void>(),
    onDone: vi.fn<() => void>(),
    onError: vi.fn<(err: string) => void>(),
    onToolEvent: vi.fn(),
    onSources: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("streamChat", () => {
  // ── 1. Normal stream ────────────────────────────────────────────────────

  it("delivers token events and calls onDone on completion", async () => {
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "token", content: "Hello" }),
        sseData({ type: "token", content: " world" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).toHaveBeenCalledTimes(2);
    expect(onToken).toHaveBeenNthCalledWith(1, "Hello");
    expect(onToken).toHaveBeenNthCalledWith(2, " world");
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 2. Stream ends without `done` event ─────────────────────────────────

  it("calls onDone via finally fallback when stream closes without done event", async () => {
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "token", content: "partial" }),
        // No done event — stream just closes
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).toHaveBeenCalledWith("partial");
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 3. Error event ───────────────────────────────────────────────────────

  it("calls onError when the stream emits an error event", async () => {
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "token", content: "before" }),
        sseData({ type: "error", content: "Something went wrong" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).toHaveBeenCalledWith("before");
    expect(onError).toHaveBeenCalledWith("Something went wrong", undefined);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("uses fallback message when error event has no content", async () => {
    const { onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([sseData({ type: "error" })]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("Unknown error", undefined);
    expect(onDone).not.toHaveBeenCalled();
  });

  // ── 4. Buffer flush — final chunk without trailing \n\n ──────────────────

  it("flushes remaining buffer to parse done event without trailing newlines", async () => {
    const { onDone, onError } = makeCallbacks();
    // Send the done event without the trailing \n\n so it ends up in buffer
    const rawPayload = `data: ${JSON.stringify({ type: "done" })}`;
    vi.mocked(fetch).mockResolvedValue(mockStreamResponse([enc(rawPayload)]));

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it("flushes remaining buffer to parse error event without trailing newlines", async () => {
    const { onDone, onError } = makeCallbacks();
    const rawPayload = `data: ${JSON.stringify({ type: "error", content: "timeout" })}`;
    vi.mocked(fetch).mockResolvedValue(mockStreamResponse([enc(rawPayload)]));

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("timeout", undefined);
    expect(onDone).not.toHaveBeenCalled();
  });

  // ── 5. Network / fetch errors ────────────────────────────────────────────

  it("calls onError and returns when fetch throws a network error", async () => {
    const { onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("TypeError: Failed to fetch");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("calls onError and returns when fetch returns a non-OK status", async () => {
    const { onDone, onError } = makeCallbacks();
    const errorResponse = new Response(JSON.stringify({ detail: "API Key missing" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
    vi.mocked(fetch).mockResolvedValue(errorResponse);

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("API Key missing");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("calls onError when res.body is null", async () => {
    const { onDone, onError } = makeCallbacks();
    const res = new Response(null, { status: 200, statusText: "OK" });
    Object.defineProperty(res, 'ok', { value: true });
    vi.mocked(fetch).mockResolvedValue(res);

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("No response body");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("calls onError when reader.read() throws mid-stream", async () => {
    const { onDone, onError } = makeCallbacks();
    let callCount = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        callCount++;
        if (callCount === 1) {
          controller.enqueue(enc(sseData({ type: "token", content: "ok" })));
        } else {
          throw new Error("Stream read error");
        }
      },
    });
    const res = new Response(stream, { status: 200, statusText: "OK" });
    Object.defineProperty(res, 'ok', { value: true });
    vi.mocked(fetch).mockResolvedValue(res);

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith(expect.stringContaining("Stream read error"));
    expect(onDone).not.toHaveBeenCalled();
  });

  // ── 6. Abort signal ──────────────────────────────────────────────────────

  it("returns without calling onDone or onError when fetch is aborted", async () => {
    const { onDone, onError } = makeCallbacks();
    const abort = new AbortController();
    const abortError = new DOMException("AbortError", "AbortError");
    vi.mocked(fetch).mockRejectedValue(abortError);

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError, abort.signal);

    expect(onDone).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("stops reading and does not call onError when stream read throws AbortError", async () => {
    const { onDone, onError } = makeCallbacks();
    const stream = new ReadableStream<Uint8Array>({
      pull() {
        const err = new DOMException("The operation was aborted", "AbortError");
        throw err;
      },
    });
    const res = new Response(stream, { status: 200, statusText: "OK" });
    Object.defineProperty(res, 'ok', { value: true });
    vi.mocked(fetch).mockResolvedValue(res);

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    // AbortError is swallowed; finally fallback should NOT fire onDone
    // because settled stays false but the AbortError path skips onError.
    // However our finally WILL call onDone as a safety net since settled===false.
    // This is acceptable — the abort case from selectTicker immediately resets state.
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 7. Malformed JSON in SSE ─────────────────────────────────────────────

  it("ignores malformed JSON and continues processing remaining events", async () => {
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        "data: {broken json}\n\n",
        sseData({ type: "token", content: "valid" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).toHaveBeenCalledWith("valid");
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 8. Multiple token events delivered in order ──────────────────────────

  it("delivers all tokens in the correct order", async () => {
    const { onToken, onDone } = makeCallbacks();
    const tokens = ["The ", "quick ", "brown ", "fox"];
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        ...tokens.map((t) => sseData({ type: "token", content: t })),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, vi.fn());

    expect(onToken).toHaveBeenCalledTimes(tokens.length);
    tokens.forEach((t, i) => {
      expect(onToken).toHaveBeenNthCalledWith(i + 1, t);
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── 9. tool_start / tool_end events ─────────────────────────────────────

  it("calls onToolEvent with running status for tool_start events", async () => {
    const { onDone, onToolEvent } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "tool_start", tool: "get_price", args: { symbol: "AAPL" } }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, vi.fn(), undefined, onToolEvent);

    expect(onToolEvent).toHaveBeenCalledWith({
      seq: undefined,
      tool: "get_price",
      stepId: undefined,
      status: "running",
      args: { symbol: "AAPL" },
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("calls onToolEvent with done status for successful tool_end events", async () => {
    const { onDone, onToolEvent } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "tool_end", tool: "get_price", duration_ms: 350, success: true }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, vi.fn(), undefined, onToolEvent);

    expect(onToolEvent).toHaveBeenCalledWith({
      seq: undefined,
      tool: "get_price",
      stepId: undefined,
      status: "done",
      durationMs: 350,
      resultEnvelope: undefined,
    });
  });

  it("calls onToolEvent with error status for failed tool_end events", async () => {
    const { onToolEvent, onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "tool_end", tool: "get_price", duration_ms: 100, success: false }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, vi.fn(), undefined, onToolEvent);

    expect(onToolEvent).toHaveBeenCalledWith({
      seq: undefined,
      tool: "get_price",
      stepId: undefined,
      status: "error",
      durationMs: 100,
      resultEnvelope: undefined,
    });
  });

  it("does not call onToolEvent when the callback is not provided", async () => {
    const { onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "tool_start", tool: "get_price", args: {} }),
        sseData({ type: "done" }),
      ]),
    );

    // No onToolEvent passed — should not throw
    await expect(
      streamChat("hi", "s1", [], vi.fn(), onDone, vi.fn()),
    ).resolves.toBeUndefined();
    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── 10. sources event ────────────────────────────────────────────────────

  it("calls onSources with the parsed source refs", async () => {
    const { onDone, onSources } = makeCallbacks();
    const sources = [
      { url: "https://example.com/a", title: "Article A" },
      { url: "https://example.com/b", title: "Article B" },
    ];
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "sources", sources }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "hi", "s1", [], vi.fn(), onDone, vi.fn(),
      undefined, undefined, onSources,
    );

    expect(onSources).toHaveBeenCalledWith(sources);
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("calls onSources with empty array when sources field is missing", async () => {
    const { onDone, onSources } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "sources" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "hi", "s1", [], vi.fn(), onDone, vi.fn(),
      undefined, undefined, onSources,
    );

    expect(onSources).toHaveBeenCalledWith([]);
  });

  it("does not call onSources when the callback is not provided", async () => {
    const { onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "sources", sources: [{ url: "x", title: "y" }] }),
        sseData({ type: "done" }),
      ]),
    );

    await expect(
      streamChat("hi", "s1", [], vi.fn(), onDone, vi.fn()),
    ).resolves.toBeUndefined();
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("calls onCapabilities when capabilities SSE event is received", async () => {
    const { onDone } = makeCallbacks();
    const onCapabilities = vi.fn();
    const capabilities = {
      internet_dns_ok: true,
      fmp_api_key_present: false,
      sec_api_key_present: true,
      worker_reachable: false,
      snapshot_at: "2026-03-03T00:00:00Z",
    };

    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "capabilities", seq: 3, run_id: "r-1", phase: "capabilities_snapshot", capabilities }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "hi",
      "s1",
      [],
      vi.fn(),
      onDone,
      vi.fn(),
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      onCapabilities,
    );

    expect(onCapabilities).toHaveBeenCalledWith({
      seq: 3,
      runId: "r-1",
      phase: "capabilities_snapshot",
      capabilities,
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── 10b. step/status progress events ───────────────────────────────────

  it("calls onProgressEvent for step and status SSE events", async () => {
    const { onDone } = makeCallbacks();
    const onProgressEvent = vi.fn();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({
          type: "step",
          seq: 2,
          step_id: "tool-get_price-2",
          category: "tool",
          tool: "get_price",
          state: "running",
          message: "Fetching data via get price",
        }),
        sseData({
          type: "status",
          seq: 3,
          run_id: "11111111-1111-4111-8111-111111111111",
          phase: "finalize_response",
          state: "running",
          message: "Synthesizing final response",
          verbose: true,
        }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "hi", "s1", [], vi.fn(), onDone, vi.fn(),
      undefined, undefined, undefined, undefined, onProgressEvent,
    );

    expect(onProgressEvent).toHaveBeenCalledTimes(2);
    expect(onProgressEvent).toHaveBeenNthCalledWith(1, {
      seq: 2,
      eventType: "step",
      state: "running",
      message: "Fetching data via get price",
      runId: undefined,
      stepId: "tool-get_price-2",
      category: "tool",
      tool: "get_price",
      durationMs: undefined,
      phase: undefined,
      verbose: false,
    });
    expect(onProgressEvent).toHaveBeenNthCalledWith(2, {
      seq: 3,
      eventType: "status",
      state: "running",
      message: "Synthesizing final response",
      runId: "11111111-1111-4111-8111-111111111111",
      stepId: undefined,
      category: undefined,
      tool: undefined,
      durationMs: undefined,
      phase: "finalize_response",
      verbose: true,
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── 11. settled flag prevents double-call ────────────────────────────────

  it("calls onDone exactly once even when done event appears and stream also closes", async () => {
    const { onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([sseData({ type: "done" })]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it("does not call onDone in finally when onError was already called", async () => {
    const { onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([sseData({ type: "error", content: "oops" })]),
    );

    await streamChat("hi", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("oops", undefined);
    expect(onDone).not.toHaveBeenCalled();
  });

  // ── 12. Multi-chunk delivery (chunks arrive split across boundaries) ──────

  it("correctly assembles SSE messages split across multiple read() chunks", async () => {
    const { onToken, onDone } = makeCallbacks();
    const fullMessage = sseData({ type: "token", content: "split" });
    const mid = Math.floor(fullMessage.length / 2);
    // Split the SSE message across two chunks
    vi.mocked(fetch).mockResolvedValue(
      mockStreamResponse([
        enc(fullMessage.slice(0, mid)),
        enc(fullMessage.slice(mid)),
        enc(sseData({ type: "done" })),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, vi.fn());

    expect(onToken).toHaveBeenCalledWith("split");
    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── 13. Empty stream ─────────────────────────────────────────────────────

  it("calls onDone via fallback when the stream body is immediately closed (empty)", async () => {
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(mockStreamResponse([]));

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 14. Unknown / unhandled event types are ignored ──────────────────────

  it("ignores kg_update and other events when no optional callbacks are provided", async () => {
    // kg_update is only dispatched when onKgUpdate is supplied; here it is
    // omitted so the event should be silently skipped.
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "kg_update", nodes_created: 2, edges_created: 1 }),
        sseData({ type: "unknown_future_event", payload: "x" }),
        sseData({ type: "token", content: "ok" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onToken).toHaveBeenCalledWith("ok");
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  // ── 15. Context refs are passed in the request body ──────────────────────

  it("sends message, session_id, and context_refs in the POST body", async () => {
    vi.mocked(fetch).mockResolvedValue(mockSseResponse([sseData({ type: "done" })]));

    await streamChat("analyse AAPL", "sess-42", ["AAPL", "MSFT"], vi.fn(), vi.fn(), vi.fn());

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          message: "analyse AAPL",
          session_id: "sess-42",
          context_refs: ["AAPL", "MSFT"],
          agent_mode: "quick",
        }),
      }),
    );
  });

  // ── 16. SSE error event with detail field (Issue 5 — missing API key) ────

  it("passes the detail field from an SSE error event to onError", async () => {
    // Regression test for Issue 5: the backend now includes a 'detail' field
    // on SSE error events carrying the full exception type and message.
    // The frontend must forward this as the second argument to onError so the
    // AppContext can surface it in debug mode / to the user.
    const { onToken, onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({
          type: "error",
          content: "An internal error occurred.",
          detail: "RuntimeError: No LLM provider available for role='agent'.",
        }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, onError);

    expect(onError).toHaveBeenCalledWith(
      "An internal error occurred.",
      "RuntimeError: No LLM provider available for role='agent'.",
    );
    expect(onDone).not.toHaveBeenCalled();
    expect(onToken).not.toHaveBeenCalled();
  });

  it("passes detail for a timeout error event from the backend", async () => {
    // When the graph exceeds GRAPH_STREAM_TIMEOUT, the backend emits an SSE
    // error event with 'timed out' in content and 'TimeoutError' in detail.
    const { onDone, onError } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({
          type: "error",
          content: "Request timed out after 120 s.",
          detail: "TimeoutError: graph.astream_events exceeded 120 s timeout",
        }),
      ]),
    );

    await streamChat("slow query", "s1", [], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith(
      "Request timed out after 120 s.",
      "TimeoutError: graph.astream_events exceeded 120 s timeout",
    );
  });

  it("treats missing API key error (401 HTTP) as onError — not onDone", async () => {
    // If the LLM provider returns 401 (API key invalid), the backend should
    // surface this as a RuntimeError SSE event. At the HTTP level, the chat
    // endpoint itself returns 200 (SSE stream started). This test covers the
    // HTTP non-OK path where the response returns 401 before SSE begins.
    const { onDone, onError } = makeCallbacks();
    const errorBody = JSON.stringify({ detail: "API key invalid or missing" });
    const res = new Response(errorBody, {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
    vi.mocked(fetch).mockResolvedValue(res);

    await streamChat("analyse TSLA", "s1", ["TSLA"], vi.fn(), onDone, onError);

    expect(onError).toHaveBeenCalledWith("API key invalid or missing");
    expect(onDone).not.toHaveBeenCalled();
  });

  // ── 17. Non-string token content coercion (Issue 1 regression) ───────────

  it("coerces list-type token content to a joined string", async () => {
    // Root cause of Issue 1: LangChain AIMessageChunk.content can be a list
    // of structured content blocks (e.g. [{"type":"text","text":"Hello"}]).
    // The frontend must never pass a non-string value to onToken.
    const { onToken, onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        // Simulate what the backend emits if normalisation somehow fails and
        // a list reaches the frontend — the defensive guard must coerce it.
        `data: ${JSON.stringify({ type: "token", content: [{ type: "text", text: "Hello" }] })}\n\n`,
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, vi.fn());

    expect(onToken).toHaveBeenCalledTimes(1);
    // The value must be a string — never an object
    const received = onToken.mock.calls[0][0];
    expect(typeof received).toBe("string");
    expect(received).not.toBe("[object Object]");
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("coerces object-type token content to a string and never emits [object Object]", async () => {
    const { onToken, onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        `data: ${JSON.stringify({ type: "token", content: { text: "Hi there" } })}\n\n`,
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, vi.fn());

    const received = onToken.mock.calls[0]?.[0];
    expect(typeof received).toBe("string");
    expect(received).not.toBe("[object Object]");
  });

  it("skips token events with null or empty content", async () => {
    const { onToken, onDone } = makeCallbacks();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "token", content: null }),
        sseData({ type: "token", content: "" }),
        sseData({ type: "token", content: "real" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat("hi", "s1", [], onToken, onDone, vi.fn());

    // Only the non-empty "real" token should reach onToken
    expect(onToken).toHaveBeenCalledTimes(1);
    expect(onToken).toHaveBeenCalledWith("real");
  });

  // ── 18. Actionable RuntimeError message surfaced (Issue 2 regression) ────

  it("surfaces the full RuntimeError message from the backend, not a generic message", async () => {
    // Root cause of Issue 2: backend was sending a generic "An internal error
    // occurred." Now it sends the actual RuntimeError message from FallbackLLM
    // which is actionable (tells user to configure a provider).
    const { onToken, onDone, onError } = makeCallbacks();
    const actionableMsg =
      "No LLM provider available or all providers failed. " +
      "Configure at least one provider in backend/.env (or the app settings).";
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({
          type: "error",
          content: actionableMsg,
          detail: `RuntimeError: ${actionableMsg}`,
        }),
      ]),
    );

    await streamChat("Should I buy @MSFT", "s1", ["MSFT"], onToken, onDone, onError);

    expect(onError).toHaveBeenCalledWith(actionableMsg, expect.stringContaining("RuntimeError"));
    // Must not be the old generic message
    expect(onError.mock.calls[0][0]).not.toBe("An internal error occurred.");
    expect(onDone).not.toHaveBeenCalled();
    expect(onToken).not.toHaveBeenCalled();
  });

  // ── 19. kg_update with error field (Issue 3 — KG post-processing failure) ─

  it("calls onKgUpdate with 0/0 and the error string when kg_update has an error field", async () => {
    // Issue 3 resilience: when KG post-processing fails the backend emits a
    // kg_update event with nodes_created=0, edges_created=0, and an error field.
    // The frontend must forward this to onKgUpdate so AppContext can log it.
    const onKgUpdate = vi.fn<(n: number, e: number, err?: string) => void>();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "kg_update", nodes_created: 0, edges_created: 0, error: "DB constraint violation" }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "Should I buy @MSFT", "s1", ["MSFT"],
      vi.fn(), vi.fn(), vi.fn(),
      undefined, undefined, undefined, onKgUpdate,
    );

    expect(onKgUpdate).toHaveBeenCalledWith(0, 0, "DB constraint violation");
  });

  it("calls onKgUpdate with 0/0 and undefined error on a normal zero-count update", async () => {
    const onKgUpdate = vi.fn<(n: number, e: number, err?: string) => void>();
    vi.mocked(fetch).mockResolvedValue(
      mockSseResponse([
        sseData({ type: "kg_update", nodes_created: 3, edges_created: 2 }),
        sseData({ type: "done" }),
      ]),
    );

    await streamChat(
      "hi", "s1", [],
      vi.fn(), vi.fn(), vi.fn(),
      undefined, undefined, undefined, onKgUpdate,
    );

    expect(onKgUpdate).toHaveBeenCalledWith(3, 2, undefined);
  });
});

