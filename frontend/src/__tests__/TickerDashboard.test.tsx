/**
 * Unit tests for TickerDashboard component rendering states.
 *
 * Tests cover:
 * - Empty / no ticker selected
 * - Loading ticker data
 * - Ticker data error
 * - AI analysis panel rendering (via AnalysisPanel)
 * - Watchlist star button interaction
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TickerDashboard } from "../components/TickerDashboard";
import * as AppContextModule from "../context/AppContext";
import type { TickerInfo, TickerAnalysis } from "../types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../context/AppContext", () => ({
  useAppContext: vi.fn(),
}));

// Stub Spinner so tests don't depend on CSS animation details
vi.mock("../components/Spinner", () => ({
  Spinner: ({ size }: { size?: number }) => (
    <span data-testid="spinner" data-size={size} />
  ),
}));

// Stub TickerCard to a simple element we can assert on
vi.mock("../components/TickerCard", () => ({
  TickerCard: ({ info }: { info: TickerInfo }) => (
    <div data-testid="ticker-card" data-symbol={info.symbol} />
  ),
}));

// Stub AnalysisPanel to inspect the analysis prop
vi.mock("../components/AnalysisPanel", () => ({
  AnalysisPanel: ({ analysis }: { analysis: TickerAnalysis }) => (
    <div data-testid="analysis-panel" data-loading={analysis.loading} data-error={analysis.error ?? ""} />
  ),
}));

vi.mock("../components/RecentEventTimeline", () => ({
  RecentEventTimeline: ({ symbol }: { symbol: string }) => (
    <div data-testid="recent-events-timeline" data-symbol={symbol} />
  ),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockTicker: TickerInfo = {
  symbol: "AAPL",
  name: "Apple Inc.",
  price: 175.5,
  market_cap: 2_700_000_000_000,
  pe_ratio: 28.5,
  forward_pe: 25.1,
  sector: "Technology",
  industry: "Consumer Electronics",
  fifty_two_week_high: 200.0,
  fifty_two_week_low: 124.17,
  dividend_yield: 0.56,
  beta: 1.23,
};

const emptyAnalysis: TickerAnalysis = {
  loading: false,
  error: null,
  overallRating: null,
  sections: {},
};

function makeContext(overrides: Partial<ReturnType<typeof AppContextModule.useAppContext>["state"]> = {}) {
  return {
    state: {
      backendStatus: "running" as const,
      migrationError: null,
      workerOnline: true,
      portfolio: [],
      watchlist: [],
      activeTicker: null,
      activeTickerLoading: false,
      activeTickerError: null,
      chatMessages: [],
      chatStreaming: false,
      tickerAnalysis: { ...emptyAnalysis },
      selectedSymbol: null,
      viewMode: "ticker" as const,
      terminalLogs: [],
      terminalOpen: false,
      kgLastUpdated: 0,
      kgLastTicker: null,
      debugMode: false,
      agentMode: "genie" as const,
      ...overrides,
    },
    selectTicker: vi.fn(),
    sendMessage: vi.fn(),
    reloadPortfolio: vi.fn(),
    reloadWatchlist: vi.fn(),
    toggleWatchlist: vi.fn().mockResolvedValue(undefined),
    addSystemMessage: vi.fn(),
    toggleTerminal: vi.fn(),
    clearTerminalLogs: vi.fn(),
    navigateToDashboard: vi.fn(),
    setDebugMode: vi.fn(),
    setAgentMode: vi.fn(),
    clearConsentProposal: vi.fn(),
  };
}

function setup(stateOverrides: object = {}) {
  const ctx = makeContext(stateOverrides);
  vi.mocked(AppContextModule.useAppContext).mockReturnValue(ctx as ReturnType<typeof AppContextModule.useAppContext>);
  const result = render(<TickerDashboard />);
  return { ...result, ctx };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TickerDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Empty state ──────────────────────────────────────────────────────────

  it("shows placeholder text when no ticker is selected", () => {
    setup({ selectedSymbol: null, activeTickerLoading: false });
    expect(
      screen.getByText(/Click a portfolio position or mention a ticker/i),
    ).toBeInTheDocument();
  });

  it("does not show ticker card or AI analysis when no ticker selected", () => {
    setup({ selectedSymbol: null });
    expect(screen.queryByTestId("ticker-card")).not.toBeInTheDocument();
    expect(screen.queryByText(/AI Analysis/i)).not.toBeInTheDocument();
  });

  // ── Loading ticker data ──────────────────────────────────────────────────

  it("shows loading spinner while fetching ticker data", () => {
    setup({ selectedSymbol: "AAPL", activeTickerLoading: true });
    expect(screen.getByTestId("spinner")).toBeInTheDocument();
    expect(screen.getByText(/Loading AAPL/i)).toBeInTheDocument();
  });

  it("hides the placeholder when ticker is loading", () => {
    setup({ selectedSymbol: "AAPL", activeTickerLoading: true });
    expect(
      screen.queryByText(/Click a portfolio position/i),
    ).not.toBeInTheDocument();
  });

  // ── Ticker data error ────────────────────────────────────────────────────

  it("shows error message when ticker fetch fails", () => {
    setup({
      selectedSymbol: "BADTICKER",
      activeTickerLoading: false,
      activeTickerError: "Symbol not found",
    });
    expect(screen.getByText("Symbol not found")).toBeInTheDocument();
  });

  it("does not show ticker card when there is a ticker error", () => {
    setup({
      selectedSymbol: "BADTICKER",
      activeTickerLoading: false,
      activeTickerError: "Symbol not found",
    });
    expect(screen.queryByTestId("ticker-card")).not.toBeInTheDocument();
  });

  // ── Ticker data loaded, analysis panel rendering ─────────────────────────

  it("shows TickerCard when ticker data is available", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    expect(screen.getByTestId("ticker-card")).toBeInTheDocument();
    expect(screen.getByTestId("ticker-card")).toHaveAttribute("data-symbol", "AAPL");
  });

  it("shows AI Analysis section heading when ticker data is available", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    expect(screen.getByText("AI Analysis")).toBeInTheDocument();
  });

  it("renders AnalysisPanel with tickerAnalysis state", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerAnalysis: { ...emptyAnalysis, loading: true },
    });
    const panel = screen.getByTestId("analysis-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveAttribute("data-loading", "true");
  });

  it("passes analysis error to AnalysisPanel", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerAnalysis: { ...emptyAnalysis, error: "LLM failed" },
    });
    const panel = screen.getByTestId("analysis-panel");
    expect(panel).toHaveAttribute("data-error", "LLM failed");
  });

  // ── Watchlist star button ────────────────────────────────────────────────

  it("shows watchlist star button when a symbol is selected", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    const btn = screen.getByTitle(/watchlist/i);
    expect(btn).toBeInTheDocument();
  });

  it("shows hollow star (☆) when ticker is not in watchlist", () => {
    setup({
      selectedSymbol: "AAPL",
      watchlist: [],
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    expect(screen.getByText("☆")).toBeInTheDocument();
  });

  it("shows filled star (★) when ticker is in watchlist", () => {
    setup({
      selectedSymbol: "AAPL",
      watchlist: [{ id: 1, ticker: "AAPL", added_at: "2024-01-01T00:00:00" }],
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    expect(screen.getByText("★")).toBeInTheDocument();
  });

  it("calls toggleWatchlist when star button is clicked", async () => {
    const { ctx } = setup({
      selectedSymbol: "AAPL",
      watchlist: [],
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    const btn = screen.getByTitle(/Add to Watchlist/i);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(ctx.toggleWatchlist).toHaveBeenCalledWith("AAPL");
    });
  });

  it("does not show star button when no symbol is selected", () => {
    setup({ selectedSymbol: null });
    expect(screen.queryByRole("button", { name: /watchlist/i })).not.toBeInTheDocument();
  });

  it("calls navigateToDashboard when back button is clicked", () => {
    const { ctx } = setup({ selectedSymbol: "AAPL", activeTicker: mockTicker, activeTickerLoading: false });
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(ctx.navigateToDashboard).toHaveBeenCalledTimes(1);
  });

  // ── Ticker panel header ──────────────────────────────────────────────────

  it("always shows the Ticker panel heading", () => {
    setup();
    expect(screen.getByText("Ticker")).toBeInTheDocument();
  });

  it("renders recent event timeline when ticker data is available", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
    });
    expect(screen.getByTestId("recent-events-timeline")).toHaveAttribute("data-symbol", "AAPL");
  });

  // ── Stale message check ──────────────────────────────────────────────────

  it("does NOT show 'Checking report cache...' (old message)", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerAnalysis: { ...emptyAnalysis, loading: true },
    });
    expect(screen.queryByText(/Checking report cache/i)).not.toBeInTheDocument();
  });

  // ── [object Object] regression ───────────────────────────────────────────

  it("never renders [object Object] in the analysis panel area", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerAnalysis: {
        ...emptyAnalysis,
        sections: {
          fundamentals: { content: "Strong fundamentals.", rating: "positive", source: "kg", loading: false },
        },
      },
    });
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });
});
