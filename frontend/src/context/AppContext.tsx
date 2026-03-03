import {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from "react";
import type {
  AgentRunEvent,
  AgentMode,
  AgentProgressEvent,
  AgentStep,
  AnalysisSectionName,
  BackendStatus,
  ChatMessage,
  PortfolioPosition,
  SourceRef,
  TerminalLogEntry,
  TerminalLogLevel,
  TerminalLogType,
  TickerAnalysis,
  TickerInfo,
  ToolCardMessage,
  ToolResultEnvelope,
  ToolEvent,
  SystemStatusSnapshot,
  WatchlistItem,
} from "../types";
import {
  fetchHealthDetailed,
  fetchPortfolio,
  fetchRunEvents,
  fetchTicker,
  postSystemEvent,
  streamAnalysis,
  streamChat,
  fetchWatchlist,
  addToWatchlist,
  removeFromWatchlist,
  fetchWorkerStatus,
} from "../api";

// ── State ────────────────────────────────────────────────────────────────────

interface AppState {
  backendStatus: BackendStatus;
  migrationError: string | null;
  workerOnline: boolean;
  portfolio: PortfolioPosition[];
  watchlist: WatchlistItem[];
  activeTicker: TickerInfo | null;
  activeTickerLoading: boolean;
  activeTickerError: string | null;
  chatMessages: ChatMessage[];
  chatStreaming: boolean;
  tickerAnalysis: TickerAnalysis;
  selectedSymbol: string | null;
  viewMode: "dashboard" | "ticker";
  terminalLogs: TerminalLogEntry[];
  terminalOpen: boolean;
  kgLastUpdated: number;
  kgLastTicker: string | null;
  debugMode: boolean;
  agentMode: AgentMode;
  systemStatus: SystemStatusSnapshot;
  activeRunToolCalls: number;
  activeRunElapsedSeconds: number;
  activeRunStartedAt: number | null;
}

const _EMPTY_ANALYSIS: TickerAnalysis = {
  loading: false,
  error: null,
  overallRating: null,
  sections: {},
};

const initialState: AppState = {
  backendStatus: "connecting",
  migrationError: null,
  workerOnline: false,
  portfolio: [],
  watchlist: [],
  activeTicker: null,
  activeTickerLoading: false,
  activeTickerError: null,
  chatMessages: [],
  chatStreaming: false,
  tickerAnalysis: { ..._EMPTY_ANALYSIS },
  selectedSymbol: null,
  viewMode: "dashboard",
  terminalLogs: [],
  terminalOpen: false,
  kgLastUpdated: 0,
  kgLastTicker: null,
  debugMode: typeof localStorage !== "undefined" && localStorage.getItem("open-fin-debug-mode") === "true",
  agentMode: "quick",
  systemStatus: {
    web: "unknown",
    core: "unknown",
    worker: "unknown",
    capabilities: {},
    updatedAt: 0,
  },
  activeRunToolCalls: 0,
  activeRunElapsedSeconds: 0,
  activeRunStartedAt: null,
};

// ── Actions ──────────────────────────────────────────────────────────────────

type Action =
  | { type: "SET_BACKEND_STATUS"; status: BackendStatus }
  | { type: "SET_MIGRATION_ERROR"; error: string | null }
  | { type: "SET_WORKER_ONLINE"; online: boolean }
  | { type: "SET_PORTFOLIO"; positions: PortfolioPosition[] }
  | { type: "SET_WATCHLIST"; items: WatchlistItem[] }
  | { type: "SET_ACTIVE_TICKER"; ticker: TickerInfo | null }
  | { type: "SET_ACTIVE_TICKER_LOADING"; loading: boolean }
  | { type: "SET_ACTIVE_TICKER_ERROR"; error: string | null }
  | { type: "SET_SELECTED_SYMBOL"; symbol: string | null }
  | { type: "SET_VIEW_MODE"; viewMode: "dashboard" | "ticker" }
  | { type: "ADD_CHAT_MESSAGE"; message: ChatMessage }
  | { type: "APPEND_TO_LAST_MESSAGE"; content: string }
  | { type: "SET_CHAT_STREAMING"; streaming: boolean }
  | { type: "UPDATE_TOOL_EVENT"; event: ToolEvent }
  | { type: "UPSERT_TOOL_CARD"; card: ToolCardMessage }
  | { type: "HYDRATE_TOOL_CARDS_FROM_RUN"; cards: ToolCardMessage[] }
  | { type: "UPDATE_AGENT_STEP"; step: AgentStep }
  | { type: "SET_LAST_ASSISTANT_RUN_ID"; runId: string }
  | { type: "SET_LAST_ASSISTANT_STATUS"; status: "streaming" | "complete" | "incomplete" }
  | { type: "FINALIZE_RUNNING_STEPS" }
  | { type: "SET_LAST_MESSAGE_SOURCES"; sources: SourceRef[] }
  | { type: "SET_TICKER_ANALYSIS_LOADING"; loading: boolean }
  | { type: "SET_TICKER_ANALYSIS_SECTION"; section: AnalysisSectionName; content: string; rating: string; source: string }
  | { type: "SET_TICKER_ANALYSIS_OVERALL"; rating: string }
  | { type: "SET_TICKER_ANALYSIS_ERROR"; error: string | null }
  | { type: "CLEAR_TICKER_ANALYSIS" }
  | { type: "SET_AGENT_MODE"; mode: AgentMode }
  | { type: "SET_SYSTEM_STATUS"; snapshot: SystemStatusSnapshot }
  | { type: "START_CHAT_RUN"; startedAt: number }
  | { type: "INCREMENT_TOOL_CALL_COUNT" }
  | { type: "TICK_CHAT_RUN_SECONDS"; now: number }
  | { type: "STOP_CHAT_RUN" }
  | { type: "MARK_LAST_ASSISTANT_QUICK_BLOCKED" }
  | { type: "APPEND_TERMINAL_LOG"; entry: TerminalLogEntry }
  | { type: "TOGGLE_TERMINAL" }
  | { type: "CLEAR_TERMINAL_LOGS" }
  | { type: "KG_UPDATED"; ticker?: string }
  | { type: "SET_DEBUG_MODE"; enabled: boolean };

function toToolCardId(card: Pick<ToolCardMessage, "seq" | "tool" | "stepId">): string {
  if (Number.isFinite(card.seq) && card.seq >= 0) {
    return `tool-card-seq-${card.seq}`;
  }
  if (card.stepId) {
    return `tool-card-${card.stepId}`;
  }
  return `tool-card-${card.tool}-${crypto.randomUUID()}`;
}

function upsertToolCardInMessage(message: ChatMessage, card: ToolCardMessage): ChatMessage {
  if (message.role !== "assistant") return message;
  const cards = [...(message.toolCards ?? [])];
  const idx = cards.findIndex((existing) => existing.id === card.id || (existing.seq >= 0 && existing.seq === card.seq));
  if (idx >= 0) {
    cards[idx] = { ...cards[idx], ...card };
    const timeline = (message.timeline ?? []).map((item) => {
      if (item.type === "tool_card" && item.card.id === cards[idx].id) {
        return { ...item, card: cards[idx] };
      }
      return item;
    });
    return { ...message, toolCards: cards, timeline };
  }

  const nextCards = [...cards, card].sort((a, b) => a.seq - b.seq);
  const timeline = [...(message.timeline ?? []), { type: "tool_card" as const, card, key: crypto.randomUUID() }];
  return { ...message, toolCards: nextCards, timeline };
}

function cardFromToolEvent(event: ToolEvent): ToolCardMessage | null {
  if (event.status !== "done" && event.status !== "error") return null;
  const seq = typeof event.seq === "number" ? event.seq : Number.MAX_SAFE_INTEGER;
  const card: ToolCardMessage = {
    id: toToolCardId({ seq, tool: event.tool, stepId: event.stepId }),
    seq,
    tool: event.tool,
    stepId: event.stepId,
    status: event.status,
    durationMs: event.durationMs,
    args: event.args,
    resultEnvelope: event.resultEnvelope,
  };
  return card;
}

function cardFromRunEvent(event: AgentRunEvent): ToolCardMessage | null {
  if (event.type !== "tool_end") return null;
  const payload = event.payload ?? {};
  const tool = typeof payload.tool === "string" ? payload.tool : "unknown_tool";
  const seq = typeof event.seq === "number" ? event.seq : Number.MAX_SAFE_INTEGER;
  const status = payload.success === false ? "error" : "done";
  const stepId = typeof payload.step_id === "string" ? payload.step_id : undefined;
  const durationMs = typeof payload.duration_ms === "number" ? payload.duration_ms : undefined;
  const argsPreview = payload.args_preview;
  const args = argsPreview && typeof argsPreview === "object" ? (argsPreview as Record<string, unknown>) : undefined;
  const resultEnvelope = payload.result_envelope;
  const envelope = resultEnvelope && typeof resultEnvelope === "object"
    ? (resultEnvelope as ToolResultEnvelope)
    : undefined;

  return {
    id: toToolCardId({ seq, tool, stepId }),
    seq,
    tool,
    stepId,
    status,
    durationMs,
    args,
    resultEnvelope: envelope,
  };
}

function toBool(value: unknown): boolean | null {
  if (typeof value !== "boolean") return null;
  return value;
}

function deriveSystemStatus(capabilities: Record<string, unknown>): SystemStatusSnapshot {
  const internetOk = toBool(capabilities["internet_dns_ok"]);
  const fmpConfigured = toBool(capabilities["fmp_api_key_present"]);
  const secConfigured = toBool(capabilities["sec_api_key_present"]);
  const workerReachable = toBool(capabilities["worker_reachable"]);
  const dbReady = toBool(capabilities["db_ready"]);
  const faissReady = toBool(capabilities["faiss_ready"]);

  let web: SystemStatusSnapshot["web"] = "unknown";
  if (internetOk === false) {
    web = "disconnected";
  } else if (internetOk === true && fmpConfigured === true && secConfigured === true) {
    web = "online";
  } else if (internetOk === true && (fmpConfigured === false || secConfigured === false)) {
    web = "degraded";
  }

  let worker: SystemStatusSnapshot["worker"] = "unknown";
  if (workerReachable === true) {
    worker = "online";
  } else if (workerReachable === false) {
    worker = "disconnected";
  }

  let core: SystemStatusSnapshot["core"] = "unknown";
  if (dbReady === true && faissReady === true) {
    core = "online";
  } else if (dbReady === false || faissReady === false) {
    core = "degraded";
  }

  return {
    web,
    core,
    worker,
    capabilities,
    updatedAt: Date.now(),
  };
}

function isQuickModeSearchBlocked(event: AgentProgressEvent): boolean {
  if (event.state !== "warning") return false;
  if (!event.details || typeof event.details !== "object") return false;

  if (event.details.quick_mode_blocked_search === true) {
    return true;
  }

  const mode = typeof event.details.mode === "string" ? event.details.mode.toLowerCase() : "";
  if (mode !== "quick") return false;

  const disabledToolsRaw = event.details.disabled_tools;
  const disabledTools = Array.isArray(disabledToolsRaw)
    ? disabledToolsRaw.filter((v): v is string => typeof v === "string").map((v) => v.toLowerCase())
    : [];

  return disabledTools.some((tool) =>
    tool === "search_web"
    || tool === "fetch_webpage"
    || tool === "read_filings"
    || tool === "extract_filing_sections"
    || tool === "get_filings_metadata"
  );
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_BACKEND_STATUS":
      return { ...state, backendStatus: action.status };
    case "SET_MIGRATION_ERROR":
      return { ...state, migrationError: action.error };
    case "SET_WORKER_ONLINE":
      return { ...state, workerOnline: action.online };
    case "SET_PORTFOLIO":
      return { ...state, portfolio: action.positions };
    case "SET_WATCHLIST":
      return { ...state, watchlist: action.items };
    case "SET_ACTIVE_TICKER":
      return { ...state, activeTicker: action.ticker };
    case "SET_ACTIVE_TICKER_LOADING":
      return { ...state, activeTickerLoading: action.loading };
    case "SET_ACTIVE_TICKER_ERROR":
      return { ...state, activeTickerError: action.error };
    case "SET_SELECTED_SYMBOL":
      return { ...state, selectedSymbol: action.symbol };
    case "SET_VIEW_MODE":
      return { ...state, viewMode: action.viewMode };
    case "ADD_CHAT_MESSAGE":
      return { ...state, chatMessages: [...state.chatMessages, action.message] };
    case "APPEND_TO_LAST_MESSAGE": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      const timeline = last.timeline ? [...last.timeline] : [];
      if (timeline.length > 0 && timeline[timeline.length - 1].type === "text") {
        const lastItem = timeline[timeline.length - 1];
        if (lastItem.type === "text") {
          timeline[timeline.length - 1] = { ...lastItem, content: lastItem.content + action.content };
        }
      } else {
        timeline.push({ type: "text", content: action.content, key: crypto.randomUUID() });
      }
      msgs[msgs.length - 1] = { ...last, content: last.content + action.content, timeline };
      return { ...state, chatMessages: msgs };
    }
    case "SET_CHAT_STREAMING":
      return { ...state, chatStreaming: action.streaming };
    case "UPDATE_TOOL_EVENT": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      const existing = last.toolEvents ?? [];
      const idx = existing.findIndex((e) => e.tool === action.event.tool && e.status === "running");
      const updated =
        idx >= 0
          ? [...existing.slice(0, idx), action.event, ...existing.slice(idx + 1)]
          : [...existing, action.event];
      msgs[msgs.length - 1] = { ...last, toolEvents: updated };
      return { ...state, chatMessages: msgs };
    }
    case "UPSERT_TOOL_CARD": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = upsertToolCardInMessage(last, action.card);
      return { ...state, chatMessages: msgs };
    }
    case "HYDRATE_TOOL_CARDS_FROM_RUN": {
      if (action.cards.length === 0) return state;
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      let hydrated = last;
      for (const card of action.cards) {
        hydrated = upsertToolCardInMessage(hydrated, card);
      }
      msgs[msgs.length - 1] = hydrated;
      return { ...state, chatMessages: msgs };
    }
    case "UPDATE_AGENT_STEP": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;

      // Drop the synthetic placeholder once real backend steps start arriving.
      const base = (last.steps ?? []).filter(
        (s) => s.stepId !== "__init_thinking__",
      );

      // Always append — each event is a new sequential log line.
      // Running and done events for the same step are distinct entries,
      // giving a terminal-style chronological view of progress.
      const updated = [...base, action.step];

      const tlBase = (last.timeline ?? []).filter(
        (t) => t.type !== "step" || t.step.stepId !== "__init_thinking__"
      );
      const updatedTimeline = [...tlBase, { type: "step" as const, step: action.step, key: crypto.randomUUID() }];

      msgs[msgs.length - 1] = { ...last, steps: updated, timeline: updatedTimeline };
      return { ...state, chatMessages: msgs };
    }
    case "SET_LAST_ASSISTANT_STATUS": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = { ...last, completionStatus: action.status };
      return { ...state, chatMessages: msgs };
    }
    case "SET_LAST_ASSISTANT_RUN_ID": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = { ...last, runId: action.runId };
      return { ...state, chatMessages: msgs };
    }
    case "FINALIZE_RUNNING_STEPS": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      if (!last.steps || last.steps.length === 0) return state;

      const updatedSteps = last.steps.map((step) =>
        step.state === "running" ? { ...step, state: "done" as const } : step,
      );

      const updatedTimeline = (last.timeline ?? []).map((t) =>
        t.type === "step" && t.step.state === "running"
          ? { ...t, step: { ...t.step, state: "done" as const } }
          : t
      );

      msgs[msgs.length - 1] = { ...last, steps: updatedSteps, timeline: updatedTimeline };
      return { ...state, chatMessages: msgs };
    }
    case "SET_LAST_MESSAGE_SOURCES": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = { ...last, sources: action.sources };
      return { ...state, chatMessages: msgs };
    }
    case "SET_TICKER_ANALYSIS_LOADING":
      return { ...state, tickerAnalysis: { ...state.tickerAnalysis, loading: action.loading } };
    case "SET_TICKER_ANALYSIS_SECTION": {
      const prev = state.tickerAnalysis;
      return {
        ...state,
        tickerAnalysis: {
          ...prev,
          sections: {
            ...prev.sections,
            [action.section]: { content: action.content, rating: action.rating, source: action.source, loading: false },
          },
        },
      };
    }
    case "SET_TICKER_ANALYSIS_OVERALL":
      return { ...state, tickerAnalysis: { ...state.tickerAnalysis, overallRating: action.rating } };
    case "SET_TICKER_ANALYSIS_ERROR":
      return { ...state, tickerAnalysis: { ...state.tickerAnalysis, error: action.error, loading: false } };
    case "CLEAR_TICKER_ANALYSIS":
      return { ...state, tickerAnalysis: { ..._EMPTY_ANALYSIS } };
    case "SET_AGENT_MODE":
      return { ...state, agentMode: action.mode };
    case "SET_SYSTEM_STATUS":
      return { ...state, systemStatus: action.snapshot };
    case "START_CHAT_RUN":
      return {
        ...state,
        activeRunToolCalls: 0,
        activeRunElapsedSeconds: 0,
        activeRunStartedAt: action.startedAt,
      };
    case "INCREMENT_TOOL_CALL_COUNT":
      return { ...state, activeRunToolCalls: state.activeRunToolCalls + 1 };
    case "TICK_CHAT_RUN_SECONDS": {
      if (state.activeRunStartedAt == null) return state;
      const elapsedSeconds = Math.max(0, Math.floor((action.now - state.activeRunStartedAt) / 1000));
      if (elapsedSeconds === state.activeRunElapsedSeconds) return state;
      return { ...state, activeRunElapsedSeconds: elapsedSeconds };
    }
    case "STOP_CHAT_RUN":
      return {
        ...state,
        activeRunStartedAt: null,
      };
    case "MARK_LAST_ASSISTANT_QUICK_BLOCKED": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = { ...last, quickModeBlockedSearch: true };
      return { ...state, chatMessages: msgs };
    }
    case "APPEND_TERMINAL_LOG": {
      const logs = [...state.terminalLogs, action.entry];
      return { ...state, terminalLogs: logs.length > 500 ? logs.slice(logs.length - 500) : logs };
    }
    case "TOGGLE_TERMINAL":
      return { ...state, terminalOpen: !state.terminalOpen };
    case "CLEAR_TERMINAL_LOGS":
      return { ...state, terminalLogs: [] };
    case "KG_UPDATED":
      return { ...state, kgLastUpdated: Date.now(), kgLastTicker: action.ticker ?? null };
    case "SET_DEBUG_MODE":
      return { ...state, debugMode: action.enabled };
    default:
      return state;
  }
}

// ── Context ──────────────────────────────────────────────────────────────────

interface AppContextValue {
  state: AppState;
  selectTicker: (symbol: string) => void;
  navigateToDashboard: () => void;
  sendMessage: (text: string, contextRefs: string[], agentMode?: AgentMode) => void;
  reloadPortfolio: () => void;
  reloadWatchlist: () => void;
  toggleWatchlist: (ticker: string) => Promise<void>;
  addSystemMessage: (content: string) => Promise<void>;
  toggleTerminal: () => void;
  clearTerminalLogs: () => void;
  setDebugMode: (enabled: boolean) => void;
  setAgentMode: (mode: AgentMode) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be used inside AppProvider");
  return ctx;
}

// ── Provider ─────────────────────────────────────────────────────────────────

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const sessionId = useRef(crypto.randomUUID());
  const tickerAbortRef = useRef<AbortController | null>(null);
  const reportAbortRef = useRef<AbortController | null>(null);
  const terminalLogIdRef = useRef(0);

  // Backend health polling
  useEffect(() => {
    let attempts = 0;
    const MAX = 30;

    const check = async () => {
      const health = await fetchHealthDetailed();
      if (health) {
        if (health.needs_wipe) {
          dispatch({ type: "SET_MIGRATION_ERROR", error: health.migration_error });
          dispatch({ type: "SET_BACKEND_STATUS", status: "migration_error" });
        } else {
          dispatch({ type: "SET_BACKEND_STATUS", status: "running" });
        }
        clearInterval(interval);
      } else {
        attempts++;
        if (attempts >= MAX) {
          dispatch({ type: "SET_BACKEND_STATUS", status: "error" });
          clearInterval(interval);
        }
      }
    };

    const interval = setInterval(check, 1000);
    check();
    return () => clearInterval(interval);
  }, []);

  // Load portfolio and watchlist when backend is ready
  useEffect(() => {
    if (state.backendStatus !== "running") return;
    reloadPortfolio();
    reloadWatchlist();
  }, [state.backendStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (state.backendStatus !== "running") {
      dispatch({ type: "SET_WORKER_ONLINE", online: false });
      return;
    }

    let cancelled = false;
    const check = async () => {
      try {
        const status = await fetchWorkerStatus();
        if (!cancelled) {
          dispatch({ type: "SET_WORKER_ONLINE", online: status.online });
        }
      } catch {
        if (!cancelled) {
          dispatch({ type: "SET_WORKER_ONLINE", online: false });
        }
      }
    };

    check();
    const interval = setInterval(check, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [state.backendStatus]);

  useEffect(() => {
    if (!state.chatStreaming || state.activeRunStartedAt == null) return;
    dispatch({ type: "TICK_CHAT_RUN_SECONDS", now: Date.now() });
    const interval = setInterval(() => {
      dispatch({ type: "TICK_CHAT_RUN_SECONDS", now: Date.now() });
    }, 1000);
    return () => clearInterval(interval);
  }, [state.chatStreaming, state.activeRunStartedAt]);

  const reloadPortfolio = useCallback(async () => {
    try {
      const positions = await fetchPortfolio();
      dispatch({ type: "SET_PORTFOLIO", positions });
    } catch {
      // non-fatal: just leave portfolio empty
    }
  }, []);

  const reloadWatchlist = useCallback(async () => {
    try {
      const items = await fetchWatchlist();
      dispatch({ type: "SET_WATCHLIST", items });
    } catch {
      // non-fatal
    }
  }, []);

  const toggleWatchlist = useCallback(async (ticker: string) => {
    const sym = ticker.toUpperCase();
    const inList = (await fetchWatchlist()).some((w) => w.ticker === sym);
    if (inList) {
      await removeFromWatchlist(sym);
    } else {
      await addToWatchlist(sym);
    }
    const items = await fetchWatchlist();
    dispatch({ type: "SET_WATCHLIST", items });
  }, []);

  const selectTicker = useCallback((symbol: string) => {
    // Cancel in-flight requests
    tickerAbortRef.current?.abort();
    reportAbortRef.current?.abort();

    const sym = symbol.toUpperCase();
    dispatch({ type: "SET_VIEW_MODE", viewMode: "ticker" });
    dispatch({ type: "SET_SELECTED_SYMBOL", symbol: sym });
    dispatch({ type: "SET_ACTIVE_TICKER", ticker: null });
    dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: true });
    dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: null });
    dispatch({ type: "CLEAR_TICKER_ANALYSIS" });
    dispatch({ type: "SET_TICKER_ANALYSIS_LOADING", loading: true });

    const tickerAbort = new AbortController();
    tickerAbortRef.current = tickerAbort;

    fetchTicker(sym)
      .then((ticker) => {
        if (tickerAbort.signal.aborted) return;
        dispatch({ type: "SET_ACTIVE_TICKER", ticker });
        dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });

        // Kick off analysis stream
        const reportAbort = new AbortController();
        reportAbortRef.current = reportAbort;

        // Set loading skeletons for each section
        for (const section of ["fundamentals", "sentiment", "technical"] as const) {
          dispatch({
            type: "SET_TICKER_ANALYSIS_SECTION",
            section,
            content: "",
            rating: "",
            source: "",
          });
          // Mark them as loading by re-setting the section with loading state
        }
        // Override loading flag on each section
        dispatch({ type: "SET_TICKER_ANALYSIS_LOADING", loading: true });

        streamAnalysis(
          sym,
          (section, content, rating, source) => {
            if (!reportAbort.signal.aborted) {
              dispatch({
                type: "SET_TICKER_ANALYSIS_SECTION",
                section,
                content,
                rating,
                source,
              });
            }
          },
          (rating) => {
            if (!reportAbort.signal.aborted) {
              dispatch({ type: "SET_TICKER_ANALYSIS_OVERALL", rating });
            }
          },
          () => {
            dispatch({ type: "SET_TICKER_ANALYSIS_LOADING", loading: false });
            dispatch({ type: "KG_UPDATED", ticker: sym });
          },
          (errMsg) => {
            dispatch({ type: "SET_TICKER_ANALYSIS_ERROR", error: errMsg || "Analysis failed" });
          },
          reportAbort.signal,
        );
      })
      .catch((err) => {
        if (tickerAbort.signal.aborted) return;
        dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });
        dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: String(err) });
        dispatch({ type: "SET_TICKER_ANALYSIS_ERROR", error: "Failed to load analysis" });
      });
  }, []);

  const navigateToDashboard = useCallback(() => {
    tickerAbortRef.current?.abort();
    reportAbortRef.current?.abort();
    dispatch({ type: "SET_ACTIVE_TICKER", ticker: null });
    dispatch({ type: "SET_SELECTED_SYMBOL", symbol: null });
    dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });
    dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: null });
    dispatch({ type: "CLEAR_TICKER_ANALYSIS" });
    dispatch({ type: "SET_TICKER_ANALYSIS_ERROR", error: null });
    dispatch({ type: "SET_TICKER_ANALYSIS_LOADING", loading: false });
    dispatch({ type: "SET_VIEW_MODE", viewMode: "dashboard" });
  }, []);

  const addSystemMessage = useCallback(async (content: string) => {
    dispatch({
      type: "ADD_CHAT_MESSAGE",
      message: {
        id: crypto.randomUUID(),
        role: "system",
        content,
        timestamp: Date.now(),
      },
    });
    try {
      await postSystemEvent(sessionId.current, content);
    } catch {
      // keep local system message even if persistence fails
    }
  }, []);

  const toggleTerminal = useCallback(() => {
    dispatch({ type: "TOGGLE_TERMINAL" });
  }, []);

  const clearTerminalLogs = useCallback(() => {
    dispatch({ type: "CLEAR_TERMINAL_LOGS" });
  }, []);

  const termLog = useCallback(
    (type: TerminalLogType, level: TerminalLogLevel, message: string, detail?: string) => {
      dispatch({
        type: "APPEND_TERMINAL_LOG",
        entry: { id: terminalLogIdRef.current++, timestamp: Date.now(), type, level, message, detail },
      });
    },
    []
  );

  const setDebugMode = useCallback((enabled: boolean) => {
    localStorage.setItem("open-fin-debug-mode", String(enabled));
    dispatch({ type: "SET_DEBUG_MODE", enabled });
  }, []);

  const setAgentMode = useCallback((mode: AgentMode) => {
    dispatch({ type: "SET_AGENT_MODE", mode });
  }, []);

  const sendMessage = useCallback((text: string, contextRefs: string[], agentMode?: AgentMode) => {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      timestamp: Date.now(),
    };
    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      toolCards: [],
      completionStatus: "streaming",
      // Immediately show a placeholder step so the bubble is never empty.
      // The reducer removes it once the first real backend step arrives.
      steps: [{
        seq: -1,
        stepId: "__init_thinking__",
        message: "Analyzing your request\u2026",
        state: "running",
        category: "stage",
      }],
      timeline: [{
        type: "step",
        step: {
          seq: -1,
          stepId: "__init_thinking__",
          message: "Analyzing your request\u2026",
          state: "running",
          category: "stage",
        },
        key: crypto.randomUUID(),
      }],
    };

    dispatch({ type: "ADD_CHAT_MESSAGE", message: userMsg });
    dispatch({ type: "ADD_CHAT_MESSAGE", message: assistantMsg });
    dispatch({ type: "SET_CHAT_STREAMING", streaming: true });
    dispatch({ type: "START_CHAT_RUN", startedAt: Date.now() });

    const preview = text.length > 60 ? text.slice(0, 57) + "..." : text;
    termLog("system", "info", `Pipeline initiated → "${preview}"`);

    let tokenLogged = false;
    let runId: string | undefined;
    streamChat(
      text,
      sessionId.current,
      contextRefs,
      (token) => {
        dispatch({ type: "APPEND_TO_LAST_MESSAGE", content: token });
        if (!tokenLogged) {
          tokenLogged = true;
          termLog("agent", "info", "Generating response...");
        }
      },
      () => {
        if (runId) {
          void fetchRunEvents(runId)
            .then((runEventsResponse) => {
              const cards = runEventsResponse.items
                .map(cardFromRunEvent)
                .filter((card): card is ToolCardMessage => card !== null);
              if (cards.length > 0) {
                dispatch({ type: "HYDRATE_TOOL_CARDS_FROM_RUN", cards });
              }
            })
            .catch((err) => {
              termLog("error", "warn", `Run hydration failed: ${String(err)}`);
            });
        }
        dispatch({ type: "SET_CHAT_STREAMING", streaming: false });
        dispatch({ type: "STOP_CHAT_RUN" });
        dispatch({ type: "SET_LAST_ASSISTANT_STATUS", status: "complete" });
        dispatch({ type: "FINALIZE_RUNNING_STEPS" });
        termLog("done", "success", "Pipeline complete.");
      },
      (err, detail) => {
        const displayMsg = state.debugMode && detail ? detail : err;
        dispatch({ type: "SET_CHAT_STREAMING", streaming: false });
        dispatch({ type: "STOP_CHAT_RUN" });
        dispatch({ type: "SET_LAST_ASSISTANT_STATUS", status: "incomplete" });
        termLog("error", "error", displayMsg, detail);
      },
      undefined, // signal
      (toolEvent) => {
        dispatch({ type: "UPDATE_TOOL_EVENT", event: toolEvent });
        const toolCard = cardFromToolEvent(toolEvent);
        if (toolCard) {
          dispatch({ type: "UPSERT_TOOL_CARD", card: toolCard });
        }
        if (toolEvent.status === "running") {
          dispatch({ type: "INCREMENT_TOOL_CALL_COUNT" });
          const argEntries = toolEvent.args ? Object.entries(toolEvent.args) : [];
          const argsPreview = (state.debugMode ? argEntries : argEntries.slice(0, 2))
            .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
            .join(", ");
          termLog("tool_start", "info", `Executing ${toolEvent.tool}(${argsPreview})...`);
        } else if (toolEvent.status === "done") {
          const ms = toolEvent.durationMs ? ` in ${toolEvent.durationMs}ms` : "";
          termLog("tool_end", "success", `${toolEvent.tool}${ms} [SUCCESS]`);
        } else {
          termLog("tool_end", "error", `${toolEvent.tool} [ERROR]`);
        }
      },
      (sources) => {
        dispatch({ type: "SET_LAST_MESSAGE_SOURCES", sources });
        termLog("sources", "info", `${sources.length} citation(s) attached.`);
      },
      (_nodesCreated, _edgesCreated, _kgError) => {
        if (_kgError) {
          termLog("error", "error", `Knowledge graph update failed: ${_kgError}`);
        } else {
          dispatch({ type: "KG_UPDATED" });
          termLog("kg_update", "info", `Graph updated: ${_nodesCreated} node(s), ${_edgesCreated} edge(s).`);
        }
      },
      (progressEvent) => {
        if (progressEvent.runId && progressEvent.runId !== runId) {
          runId = progressEvent.runId;
          dispatch({ type: "SET_LAST_ASSISTANT_RUN_ID", runId: progressEvent.runId });
        }
        const msg = progressEvent.message;
        const level: TerminalLogLevel =
          progressEvent.state === "error"
            ? "error"
            : progressEvent.state === "warning"
              ? "warn"
            : progressEvent.state === "done"
              ? "success"
              : "info";

        // Both "step" (tool events) and "status" (graph stage events) become
        // visible AgentStep rows in the chat bubble. Previously, only "step"
        // events were dispatched — "status" events (Classifying request,
        // Planning data fetches, Synthesizing response, etc.) were silently
        // dropped to the terminal only. That caused the "single massive
        // response" appearance: no progress was visible until tokens arrived.
        dispatch({
          type: "UPDATE_AGENT_STEP",
          step: {
            seq: progressEvent.seq,
            stepId: progressEvent.stepId ?? `seq-${progressEvent.seq}`,
            message: msg,
            state: progressEvent.state,
            category:
              progressEvent.category ??
              (progressEvent.eventType === "status" ? "stage" : "tool"),
            tool: progressEvent.tool,
            durationMs: progressEvent.durationMs,
          },
        });

        if (isQuickModeSearchBlocked(progressEvent)) {
          dispatch({ type: "MARK_LAST_ASSISTANT_QUICK_BLOCKED" });
        }

        const detail = progressEvent.phase ? `phase=${progressEvent.phase}` : undefined;
        termLog(
          progressEvent.eventType === "step" ? "step" : "status",
          level,
          msg,
          detail,
        );
      },
      agentMode ?? state.agentMode,
      (capEvent) => {
        const c = capEvent.capabilities ?? {};
        const statusSnapshot = deriveSystemStatus(c);
        dispatch({ type: "SET_SYSTEM_STATUS", snapshot: statusSnapshot });
        const internetOk = c["internet_dns_ok"] === true;
        const fmpOk = c["fmp_api_key_present"] === true;
        const secOk = c["sec_api_key_present"] === true;
        const workerOk = c["worker_reachable"] === true;

        const summary =
          `System health: DNS ${internetOk ? "ok" : "down"}, ` +
          `FMP ${fmpOk ? "configured" : "missing"}, ` +
          `SEC ${secOk ? "configured" : "missing"}, ` +
          `Worker ${workerOk ? "online" : "offline"}`;

        dispatch({
          type: "UPDATE_AGENT_STEP",
          step: {
            seq: capEvent.seq ?? Number.MIN_SAFE_INTEGER,
            stepId: "stage-capabilities-snapshot",
            message: summary,
            state: "done",
            category: "stage",
          },
        });

        termLog("status", "info", summary, capEvent.phase ? `phase=${capEvent.phase}` : undefined);
      },
    );
  }, [termLog, state.debugMode, state.agentMode]);

  return (
    <AppContext.Provider value={{ state, selectTicker, navigateToDashboard, sendMessage, reloadPortfolio, reloadWatchlist, toggleWatchlist, addSystemMessage, toggleTerminal, clearTerminalLogs, setDebugMode, setAgentMode }}>
      {children}
    </AppContext.Provider>
  );
}
