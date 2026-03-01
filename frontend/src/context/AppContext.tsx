import {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from "react";
import type { BackendStatus, ChatMessage, PortfolioPosition, SourceRef, TerminalLogEntry, TerminalLogLevel, TerminalLogType, TickerInfo, ToolEvent, WatchlistItem } from "../types";
import {
  fetchHealthDetailed,
  fetchPortfolio,
  fetchTicker,
  postSystemEvent,
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
  tickerReport: string;
  tickerReportLoading: boolean;
  tickerReportError: string | null;
  selectedSymbol: string | null;
  terminalLogs: TerminalLogEntry[];
  terminalOpen: boolean;
  kgLastUpdated: number;
  debugMode: boolean;
}

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
  tickerReport: "",
  tickerReportLoading: false,
  tickerReportError: null,
  selectedSymbol: null,
  terminalLogs: [],
  terminalOpen: false,
  kgLastUpdated: 0,
  debugMode: typeof localStorage !== "undefined" && localStorage.getItem("open-fin-debug-mode") === "true",
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
  | { type: "ADD_CHAT_MESSAGE"; message: ChatMessage }
  | { type: "APPEND_TO_LAST_MESSAGE"; content: string }
  | { type: "SET_CHAT_STREAMING"; streaming: boolean }
  | { type: "UPDATE_TOOL_EVENT"; event: ToolEvent }
  | { type: "SET_LAST_MESSAGE_SOURCES"; sources: SourceRef[] }
  | { type: "SET_TICKER_REPORT"; report: string }
  | { type: "APPEND_TICKER_REPORT"; content: string }
  | { type: "SET_TICKER_REPORT_LOADING"; loading: boolean }
  | { type: "SET_TICKER_REPORT_ERROR"; error: string | null }
  | { type: "APPEND_TERMINAL_LOG"; entry: TerminalLogEntry }
  | { type: "TOGGLE_TERMINAL" }
  | { type: "CLEAR_TERMINAL_LOGS" }
  | { type: "KG_UPDATED" }
  | { type: "SET_DEBUG_MODE"; enabled: boolean };

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
    case "ADD_CHAT_MESSAGE":
      return { ...state, chatMessages: [...state.chatMessages, action.message] };
    case "APPEND_TO_LAST_MESSAGE": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      msgs[msgs.length - 1] = { ...last, content: last.content + action.content };
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
    case "SET_LAST_MESSAGE_SOURCES": {
      const msgs = [...state.chatMessages];
      if (msgs.length === 0) return state;
      const last = msgs[msgs.length - 1];
      if (last.role !== "assistant") return state;
      msgs[msgs.length - 1] = { ...last, sources: action.sources };
      return { ...state, chatMessages: msgs };
    }
    case "SET_TICKER_REPORT":
      return { ...state, tickerReport: action.report };
    case "APPEND_TICKER_REPORT":
      return { ...state, tickerReport: state.tickerReport + action.content };
    case "SET_TICKER_REPORT_LOADING":
      return { ...state, tickerReportLoading: action.loading };
    case "SET_TICKER_REPORT_ERROR":
      return { ...state, tickerReportError: action.error };
    case "APPEND_TERMINAL_LOG": {
      const logs = [...state.terminalLogs, action.entry];
      return { ...state, terminalLogs: logs.length > 500 ? logs.slice(logs.length - 500) : logs };
    }
    case "TOGGLE_TERMINAL":
      return { ...state, terminalOpen: !state.terminalOpen };
    case "CLEAR_TERMINAL_LOGS":
      return { ...state, terminalLogs: [] };
    case "KG_UPDATED":
      return { ...state, kgLastUpdated: Date.now() };
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
  sendMessage: (text: string, contextRefs: string[]) => void;
  reloadPortfolio: () => void;
  reloadWatchlist: () => void;
  toggleWatchlist: (ticker: string) => Promise<void>;
  addSystemMessage: (content: string) => Promise<void>;
  toggleTerminal: () => void;
  clearTerminalLogs: () => void;
  setDebugMode: (enabled: boolean) => void;
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
    dispatch({ type: "SET_SELECTED_SYMBOL", symbol: sym });
    dispatch({ type: "SET_ACTIVE_TICKER", ticker: null });
    dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: true });
    dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: null });
    dispatch({ type: "SET_TICKER_REPORT", report: "" });
    dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: true });
    dispatch({ type: "SET_TICKER_REPORT_ERROR", error: null });

    const tickerAbort = new AbortController();
    tickerAbortRef.current = tickerAbort;

    fetchTicker(sym)
      .then((ticker) => {
        if (tickerAbort.signal.aborted) return;
        dispatch({ type: "SET_ACTIVE_TICKER", ticker });
        dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });

        // Kick off report stream
        const reportAbort = new AbortController();
        reportAbortRef.current = reportAbort;

        streamChat(
          `Give me a concise 3-4 sentence fundamental analysis of ${sym}.`,
          sessionId.current + "-report",
          [sym],
          (token) => {
            if (!reportAbort.signal.aborted)
              dispatch({ type: "APPEND_TICKER_REPORT", content: token });
          },
          () => dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: false }),
          (errMsg) => {
            dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: false });
            dispatch({ type: "SET_TICKER_REPORT_ERROR", error: errMsg || "Analysis failed" });
          },
          reportAbort.signal,
        );
      })
      .catch((err) => {
        if (tickerAbort.signal.aborted) return;
        dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });
        dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: String(err) });
        dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: false });
        dispatch({ type: "SET_TICKER_REPORT_ERROR", error: "Failed to load analysis" });
      });
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

  const sendMessage = useCallback((text: string, contextRefs: string[]) => {
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
    };

    dispatch({ type: "ADD_CHAT_MESSAGE", message: userMsg });
    dispatch({ type: "ADD_CHAT_MESSAGE", message: assistantMsg });
    dispatch({ type: "SET_CHAT_STREAMING", streaming: true });

    const preview = text.length > 60 ? text.slice(0, 57) + "..." : text;
    termLog("system", "info", `Pipeline initiated → "${preview}"`);

    let tokenLogged = false;
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
        dispatch({ type: "SET_CHAT_STREAMING", streaming: false });
        termLog("done", "success", "Pipeline complete.");
      },
      (err, detail) => {
        const displayMsg = state.debugMode && detail ? detail : err;
        dispatch({ type: "APPEND_TO_LAST_MESSAGE", content: `\n[Error: ${displayMsg}]` });
        dispatch({ type: "SET_CHAT_STREAMING", streaming: false });
        termLog("error", "error", displayMsg, detail);
      },
      undefined, // signal
      (toolEvent) => {
        dispatch({ type: "UPDATE_TOOL_EVENT", event: toolEvent });
        if (toolEvent.status === "running") {
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
      (_nodesCreated, _edgesCreated) => {
        dispatch({ type: "KG_UPDATED" });
        termLog("kg_update", "info", `Graph updated: ${_nodesCreated} node(s), ${_edgesCreated} edge(s).`);
      },
    );
  }, [termLog, state.debugMode]);

  return (
    <AppContext.Provider value={{ state, selectTicker, sendMessage, reloadPortfolio, reloadWatchlist, toggleWatchlist, addSystemMessage, toggleTerminal, clearTerminalLogs, setDebugMode }}>
      {children}
    </AppContext.Provider>
  );
}
