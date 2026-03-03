import type {
  TickerInfo,
  PortfolioPosition,
  TradeOrder,
  TradeResult,
  LlmSettings,
  LlmProvider,
  WatchlistItem,
  GraphSummary,
  SubgraphData,
  PaginatedNodes,
  PaginatedEdges,
  NodeQueryParams,
  EdgeQueryParams,
  Loadout,
  LoadoutCreate,
  LoadoutUpdate,
  PaginatedExecutions,
  WorkerStatusInfo,
  StrategyInfo,
  SettingSchema,
  SettingsValues,
  AnalysisSectionName,
  AgentMode,
  DashboardMetrics,
  TickerEventsResponse,
  TickerNote,
  PaginatedTickerNotes,
  GraphConnectionsSummary,
} from "./types";

// Mutable base URL — updated by initApiBase() before the React tree renders.
// Falls back to port 8000 when running outside of Electron (plain browser, tests).
let API = "http://localhost:8000";

/**
 * Resolve the actual backend port from the Electron main process and update
 * the module-level API base URL.  Must be called (and awaited) once in
 * main.tsx before ReactDOM renders the application.
 *
 * In non-Electron environments (plain browser, unit tests) this is a no-op.
 */
export async function initApiBase(): Promise<void> {
  try {
    const port = await window.electronAPI?.getBackendPort?.();
    if (port && port !== 8000) {
      API = `http://localhost:${port}`;
    }
  } catch {
    // Not in Electron context or IPC unavailable — keep default.
  }
}

export async function fetchHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export interface HealthResponse {
  status: string;
  faiss_ready: boolean;
  migration_ok: boolean;
  migration_error: string | null;
  needs_wipe: boolean;
}

export async function fetchHealthDetailed(): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`${API}/api/health`);
    if (!res.ok) return null;
    return res.json() as Promise<HealthResponse>;
  } catch {
    return null;
  }
}

export async function wipeData(scope: "all" | "db" | "faiss" = "all"): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/admin/wipe?scope=${scope}`, { method: "POST" });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchTicker(symbol: string): Promise<TickerInfo> {
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error(`Ticker fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchDashboardMetrics(): Promise<DashboardMetrics> {
  const res = await fetch(`${API}/api/dashboard/metrics`);
  if (!res.ok) throw new Error(`Dashboard metrics fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchTickerEvents(symbol: string): Promise<TickerEventsResponse> {
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}/events`);
  if (!res.ok) throw new Error(`Ticker events fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchTickerNotes(
  symbol: string,
  offset = 0,
  limit = 50,
): Promise<PaginatedTickerNotes> {
  const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}/notes?${qs}`);
  if (!res.ok) throw new Error(`Ticker notes fetch failed: ${res.status}`);
  return res.json();
}

export async function createTickerNote(symbol: string, content: string): Promise<TickerNote> {
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Ticker note create failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteTickerNote(symbol: string, noteId: number): Promise<void> {
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}/notes/${noteId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Ticker note delete failed: ${res.status}`);
  }
}

export async function fetchPortfolio(): Promise<PortfolioPosition[]> {
  const res = await fetch(`${API}/api/portfolio`);
  if (!res.ok) throw new Error(`Portfolio fetch failed: ${res.status}`);
  return res.json();
}

export async function syncPortfolio(): Promise<void> {
  const res = await fetch(`${API}/api/sync-portfolio`, { method: "POST" });
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
}

export async function executeTrade(order: TradeOrder): Promise<TradeResult> {
  const res = await fetch(`${API}/api/execute_trade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(order),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Trade failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchLlmSettings(): Promise<LlmSettings> {
  const res = await fetch(`${API}/api/llm/settings`);
  if (!res.ok) throw new Error(`LLM settings fetch failed: ${res.status}`);
  return res.json();
}

export async function updateLlmSettings(
  mode: "cloud" | "ollama",
  fallbackOrder: LlmProvider[],
  subagentFallbackOrder?: LlmProvider[] | null,
): Promise<LlmSettings> {
  const body: Record<string, unknown> = {
    mode,
    fallback_order: fallbackOrder,
  };
  if (subagentFallbackOrder && subagentFallbackOrder.length > 0) {
    body.subagent_fallback_order = subagentFallbackOrder;
  }
  const res = await fetch(`${API}/api/llm/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `LLM settings update failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchWatchlist(): Promise<WatchlistItem[]> {
  const res = await fetch(`${API}/api/watchlist`);
  if (!res.ok) throw new Error(`Watchlist fetch failed: ${res.status}`);
  return res.json();
}

export async function addToWatchlist(ticker: string): Promise<WatchlistItem> {
  const res = await fetch(`${API}/api/watchlist/${encodeURIComponent(ticker)}`, { method: "POST" });
  if (!res.ok) throw new Error(`Add to watchlist failed: ${res.status}`);
  return res.json();
}

export async function removeFromWatchlist(ticker: string): Promise<void> {
  const res = await fetch(`${API}/api/watchlist/${encodeURIComponent(ticker)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`Remove from watchlist failed: ${res.status}`);
}

export async function postSystemEvent(sessionId: string, content: string): Promise<void> {
  const res = await fetch(`${API}/api/chat/system_event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, content }),
  });
  if (!res.ok) throw new Error(`System event failed: ${res.status}`);
}

// ── Knowledge Graph ──────────────────────────────────────────────────────────

export async function fetchGraphSummary(): Promise<GraphSummary> {
  const res = await fetch(`${API}/api/graph/summary`);
  if (!res.ok) throw new Error(`Graph summary fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchGraphEgo(ticker: string, depth = 2): Promise<SubgraphData> {
  const res = await fetch(
    `${API}/api/graph/ego?ticker=${encodeURIComponent(ticker)}&depth=${depth}`,
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Ego fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchGraphConnections(ticker: string): Promise<GraphConnectionsSummary> {
  const qs = new URLSearchParams({ ticker });
  const res = await fetch(`${API}/api/graph/connections?${qs}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Graph connections fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchGraphNodes(params: NodeQueryParams = {}): Promise<PaginatedNodes> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set("kind", params.kind);
  if (params.search) qs.set("search", params.search);
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_dir) qs.set("sort_dir", params.sort_dir);
  if (params.min_degree != null) qs.set("min_degree", String(params.min_degree));
  if (params.offset != null) qs.set("offset", String(params.offset));
  if (params.limit != null) qs.set("limit", String(params.limit));
  const res = await fetch(`${API}/api/graph/nodes?${qs}`);
  if (!res.ok) throw new Error(`Graph nodes fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchGraphEdges(params: EdgeQueryParams = {}): Promise<PaginatedEdges> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set("kind", params.kind);
  if (params.source) qs.set("source", params.source);
  if (params.offset != null) qs.set("offset", String(params.offset));
  if (params.limit != null) qs.set("limit", String(params.limit));
  const res = await fetch(`${API}/api/graph/edges?${qs}`);
  if (!res.ok) throw new Error(`Graph edges fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchLoadouts(): Promise<Loadout[]> {
  const res = await fetch(`${API}/api/loadouts`);
  if (!res.ok) throw new Error(`Loadouts fetch failed: ${res.status}`);
  return res.json();
}

export async function createLoadout(payload: LoadoutCreate): Promise<Loadout> {
  const res = await fetch(`${API}/api/loadouts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Create loadout failed: ${res.status}`);
  }
  return res.json();
}

export async function updateLoadout(id: number, payload: LoadoutUpdate): Promise<Loadout> {
  const res = await fetch(`${API}/api/loadouts/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Update loadout failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteLoadout(id: number): Promise<void> {
  const res = await fetch(`${API}/api/loadouts/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`Delete loadout failed: ${res.status}`);
}

export async function fetchExecutions(
  loadoutId: number,
  offset = 0,
  limit = 25,
): Promise<PaginatedExecutions> {
  const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  const res = await fetch(`${API}/api/loadouts/${loadoutId}/executions?${qs}`);
  if (!res.ok) throw new Error(`Executions fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchWorkerStatus(): Promise<WorkerStatusInfo> {
  const res = await fetch(`${API}/api/worker/status`);
  if (!res.ok) throw new Error(`Worker status fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchStrategies(): Promise<StrategyInfo[]> {
  const res = await fetch(`${API}/api/strategies`);
  if (!res.ok) throw new Error(`Strategies fetch failed: ${res.status}`);
  return res.json();
}

// ── Settings ─────────────────────────────────────────────────────────────────

export async function fetchSettingsSchema(): Promise<SettingSchema[]> {
  const res = await fetch(`${API}/api/settings/schema`);
  if (!res.ok) throw new Error(`Settings schema fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchSettings(): Promise<SettingsValues> {
  const res = await fetch(`${API}/api/settings`);
  if (!res.ok) throw new Error(`Settings fetch failed: ${res.status}`);
  return res.json();
}

export async function saveSettings(values: Record<string, string | null>): Promise<void> {
  const res = await fetch(`${API}/api/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Settings save failed: ${res.status}`);
  }
}

export async function streamChat(
  message: string,
  sessionId: string,
  contextRefs: string[],
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (err: string, detail?: string) => void,
  signal?: AbortSignal,
  onToolEvent?: (event: import("./types").ToolEvent) => void,
  onSources?: (sources: import("./types").SourceRef[]) => void,
  onKgUpdate?: (nodesCreated: number, edgesCreated: number, error?: string) => void,
  onProgressEvent?: (event: import("./types").AgentProgressEvent) => void,
  agentMode?: AgentMode,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        context_refs: contextRefs,
        agent_mode: agentMode ?? "genie",
      }),
      signal,
    });
    if (!res.ok) {
      let errDetail = `HTTP ${res.status}`;
      try {
        const text = await res.text();
        if (text) {
          try {
            const body = JSON.parse(text);
            if (body.detail) {
              if (Array.isArray(body.detail)) {
                // FastAPI/Pydantic validation errors: [{type, loc, msg, ...}, ...]
                errDetail = body.detail
                  .map((e: unknown) =>
                    e && typeof e === "object" && "msg" in e
                      ? String((e as Record<string, unknown>).msg)
                      : String(e),
                  )
                  .join("; ");
              } else if (typeof body.detail === "string") {
                errDetail = body.detail;
              } else {
                errDetail = JSON.stringify(body.detail);
              }
            } else {
              errDetail = text;
            }
          } catch {
            errDetail = text;
          }
        }
      } catch {
        // failed to read text
      }
      onError(errDetail);
      return;
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    onError(String(e));
    return;
  }

  if (!res.body) {
    onError("No response body");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Track whether onDone or onError was called so we can guarantee one fires.
  let settled = false;

  const parseAndDispatchSseEvent = (part: string) => {
    const match = part.match(/^data:\s*(.+)$/m);
    if (!match) return;
    try {
      const event = JSON.parse(match[1]);
      if (event.type === "token") {
        // Defensive guard: backend normalizes chunk.content to string, but
        // add a second line of defense here in case a non-string slips through.
        const raw = event.content;
        const content =
          typeof raw === "string"
            ? raw
            : raw == null
              ? ""
              : typeof raw === "object"
                ? JSON.stringify(raw)
                : String(raw);
        if (content) onToken(content);
      } else if (event.type === "done") {
        settled = true;
        onDone();
      } else if (event.type === "error") {
        settled = true;
        const rawErr = event.content;
        const errContent =
          typeof rawErr === "string"
            ? rawErr
            : rawErr == null
              ? "Unknown error"
              : JSON.stringify(rawErr);
        onError(errContent, event.detail);
      } else if (event.type === "tool_start" && onToolEvent) {
        onToolEvent({ tool: event.tool, status: "running", args: event.args });
      } else if (event.type === "tool_end" && onToolEvent) {
        onToolEvent({
          tool: event.tool,
          status: event.success === false ? "error" : "done",
          durationMs: event.duration_ms,
        });
      } else if (event.type === "sources" && onSources) {
        onSources(event.sources ?? []);
      } else if (event.type === "kg_update" && onKgUpdate) {
        onKgUpdate(event.nodes_created ?? 0, event.edges_created ?? 0, event.error);
      } else if (event.type === "step" && onProgressEvent) {
        const rawState = event.state;
        const state =
          rawState === "done" || rawState === "error" || rawState === "running"
            ? rawState
            : "running";
        onProgressEvent({
          seq: typeof event.seq === "number" ? event.seq : -1,
          eventType: event.type,
          state,
          message: typeof event.message === "string" ? event.message : "Agent update",
          stepId: typeof event.step_id === "string" ? event.step_id : undefined,
          category: event.category === "tool" || event.category === "stage" ? event.category : undefined,
          tool: typeof event.tool === "string" ? event.tool : undefined,
          durationMs: typeof event.duration_ms === "number" ? event.duration_ms : undefined,
          phase: typeof event.phase === "string" ? event.phase : undefined,
          verbose: Boolean(event.verbose),
        });
      }
    } catch {
      // ignore malformed JSON
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE messages are separated by "\n\n"
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        parseAndDispatchSseEvent(part);
      }
    }

    // Flush any remaining content in buffer that arrived without a trailing "\n\n"
    if (buffer.trim()) {
      parseAndDispatchSseEvent(buffer);
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      settled = true;
      onError(String(e));
    }
  } finally {
    reader.releaseLock();
    // Safety net: if the stream ended without emitting done or error
    // (e.g. network drop, server crash), resolve the loading state.
    if (!settled) onDone();
  }
}

// ── Analysis SSE ────────────────────────────────────────────────────────────

export async function streamAnalysis(
  ticker: string,
  onSection: (section: AnalysisSectionName, content: string, rating: string, source: string) => void,
  onOverallRating: (rating: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
  signal?: AbortSignal,
  onStatus?: (message: string) => void,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API}/api/analysis/${encodeURIComponent(ticker)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      onError(text || `HTTP ${res.status}`);
      return;
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    onError(String(e));
    return;
  }

  if (!res.body) {
    onError("No response body");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let settled = false;

  const dispatch = (part: string) => {
    const match = part.match(/^data:\s*(.+)$/m);
    if (!match) return;
    try {
      const event = JSON.parse(match[1]);
      if (event.type === "section_ready") {
        onSection(
          event.section as AnalysisSectionName,
          typeof event.content === "string" ? event.content : "",
          typeof event.rating === "string" ? event.rating : "",
          typeof event.source === "string" ? event.source : "llm",
        );
      } else if (event.type === "overall_rating") {
        onOverallRating(typeof event.rating === "string" ? event.rating : "Neutral");
      } else if (event.type === "done") {
        settled = true;
        onDone();
      } else if (event.type === "error") {
        settled = true;
        onError(typeof event.message === "string" ? event.message : "Analysis failed");
      } else if (event.type === "status" && onStatus) {
        onStatus(typeof event.message === "string" ? event.message : "");
      }
    } catch {
      // ignore malformed JSON
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) dispatch(part);
    }
    if (buffer.trim()) dispatch(buffer);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      settled = true;
      onError(String(e));
    }
  } finally {
    reader.releaseLock();
    if (!settled) onDone();
  }
}
