/**
 * Unit tests for TickerDashboard component rendering states.
 *
 * Tests cover:
 * - Empty / no ticker selected
 * - Loading ticker data
 * - Ticker data error
 * - AI analysis: loading spinner (no report yet)
 * - AI analysis: streaming (partial report + typing cursor)
 * - AI analysis: complete (report done, no spinner)
 * - Watchlist star button interaction
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TickerDashboard } from "../components/TickerDashboard";
import * as AppContextModule from "../context/AppContext";
import type { TickerInfo } from "../types";

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

function makeContext(overrides: Partial<Parameters<typeof AppContextModule.useAppContext>[0] extends never ? object : ReturnType<typeof AppContextModule.useAppContext>["state"]> = {}) {
  return {
    state: {
      backendStatus: "running" as const,
      workerOnline: true,
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
      terminalLogs: [],
      terminalOpen: false,
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

  // ── Ticker data loaded, AI analysis loading ──────────────────────────────

  it("shows TickerCard when ticker data is available", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "",
      tickerReportLoading: false,
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

  it("shows spinner and 'Generating AI analysis...' while loading with no report", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "",
      tickerReportLoading: true,
    });
    expect(screen.getByText("Generating AI analysis...")).toBeInTheDocument();
    expect(screen.getByTestId("spinner")).toBeInTheDocument();
  });

  it("does NOT show the loading spinner message when tickerReport already has content", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "Apple has strong fundamentals.",
      tickerReportLoading: true,
    });
    expect(screen.queryByText("Generating AI analysis...")).not.toBeInTheDocument();
  });

  // ── AI analysis streaming state ──────────────────────────────────────────

  it("shows partial report text with typing cursor while still loading", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "Apple has strong",
      tickerReportLoading: true,
    });
    expect(screen.getByText(/Apple has strong/)).toBeInTheDocument();
    // Typing cursor is a span with class "typing-cursor"
    const cursor = document.querySelector(".typing-cursor");
    expect(cursor).toBeInTheDocument();
  });

  // ── AI analysis complete state ───────────────────────────────────────────

  it("shows full report text without typing cursor when loading is done", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "Apple Inc. has strong fundamentals with excellent cash flow.",
      tickerReportLoading: false,
    });
    expect(
      screen.getByText(/Apple Inc\. has strong fundamentals/),
    ).toBeInTheDocument();
    const cursor = document.querySelector(".typing-cursor");
    expect(cursor).not.toBeInTheDocument();
  });

  it("does not show loading spinner message when analysis is complete", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "Analysis complete.",
      tickerReportLoading: false,
    });
    expect(screen.queryByText("Generating AI analysis...")).not.toBeInTheDocument();
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

  // ── Ticker panel header ──────────────────────────────────────────────────

  it("always shows the Ticker panel heading", () => {
    setup();
    expect(screen.getByText("Ticker")).toBeInTheDocument();
  });

  // ── Stale message check ──────────────────────────────────────────────────

  it("does NOT show 'Checking report cache...' (old message)", () => {
    setup({
      selectedSymbol: "AAPL",
      activeTickerLoading: false,
      activeTicker: mockTicker,
      tickerReport: "",
      tickerReportLoading: true,
    });
    expect(screen.queryByText(/Checking report cache/i)).not.toBeInTheDocument();
  });
});
