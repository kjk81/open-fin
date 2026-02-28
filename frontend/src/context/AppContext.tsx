import {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from "react";
import type { BackendStatus, ChatMessage, PortfolioPosition, SourceRef, TickerInfo, ToolEvent, WatchlistItem } from "../types";
import {
  fetchHealth,
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
  selectedSymbol: string | null;
}

const initialState: AppState = {
  backendStatus: "connecting",
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
  selectedSymbol: null,
};

// ── Actions ──────────────────────────────────────────────────────────────────

type Action =
  | { type: "SET_BACKEND_STATUS"; status: BackendStatus }
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
  | { type: "SET_TICKER_REPORT_LOADING"; loading: boolean };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_BACKEND_STATUS":
      return { ...state, backendStatus: action.status };
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

  // Backend health polling
  useEffect(() => {
    let attempts = 0;
    const MAX = 30;

    const check = async () => {
      const ok = await fetchHealth();
      if (ok) {
        dispatch({ type: "SET_BACKEND_STATUS", status: "running" });
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
          () => dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: false }),
          reportAbort.signal,
        );
      })
      .catch((err) => {
        if (tickerAbort.signal.aborted) return;
        dispatch({ type: "SET_ACTIVE_TICKER_LOADING", loading: false });
        dispatch({ type: "SET_ACTIVE_TICKER_ERROR", error: String(err) });
        dispatch({ type: "SET_TICKER_REPORT_LOADING", loading: false });
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

    streamChat(
      text,
      sessionId.current,
      contextRefs,
      (token) => dispatch({ type: "APPEND_TO_LAST_MESSAGE", content: token }),
      () => dispatch({ type: "SET_CHAT_STREAMING", streaming: false }),
      (err) => {
        dispatch({ type: "APPEND_TO_LAST_MESSAGE", content: `\n[Error: ${err}]` });
        dispatch({ type: "SET_CHAT_STREAMING", streaming: false });
      },
      undefined, // signal
      (toolEvent) => dispatch({ type: "UPDATE_TOOL_EVENT", event: toolEvent }),
      (sources) => dispatch({ type: "SET_LAST_MESSAGE_SOURCES", sources }),
    );
  }, []);

  return (
    <AppContext.Provider value={{ state, selectTicker, sendMessage, reloadPortfolio, reloadWatchlist, toggleWatchlist, addSystemMessage }}>
      {children}
    </AppContext.Provider>
  );
}
