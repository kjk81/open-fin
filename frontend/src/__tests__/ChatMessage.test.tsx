/**
 * Tests for ChatMessage component — markdown rendering, mention tags, and trade blocks.
 *
 * Covers:
 * - Plain text renders without crashing.
 * - @TICKER mention becomes a clickable button that calls selectTicker.
 * - Lowercase @ticker is normalized to UPPERCASE before rendering.
 * - @portfolio renders a non-interactive mention tag.
 * - [TRADE: {...}] block renders a "Review Trade" button.
 * - Malformed TRADE JSON falls back to raw text.
 * - GFM features (bold, code) render without crashing.
 * - External links are rendered as <a> tags (not mention buttons).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ChatMessage as ChatMessageType } from "../types";
import { ChatMessage } from "../components/ChatMessage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockSelectTicker = vi.fn();

vi.mock("../context/AppContext", () => ({
  useAppContext: vi.fn(() => ({
    selectTicker: mockSelectTicker,
    state: {},
    dispatch: vi.fn(),
  })),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function assistant(content: string): ChatMessageType {
  return { id: "1", role: "assistant", content, timestamp: 0, sources: [], toolEvents: [] };
}

function user(content: string): ChatMessageType {
  return { id: "2", role: "user", content, timestamp: 0 };
}

const noop = vi.fn();
const onOpenRunExplorer = vi.fn();

beforeEach(() => {
  mockSelectTicker.mockClear();
  noop.mockClear();
  onOpenRunExplorer.mockClear();
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe("ChatMessage plain text", () => {
  it("renders without crashing for an empty message", () => {
    render(<ChatMessage message={assistant("")} isStreaming={false} onReviewTrade={noop} />);
  });

  it("renders the text content of a user message", () => {
    render(<ChatMessage message={user("Hello there")} isStreaming={false} onReviewTrade={noop} />);
    expect(screen.getByText("Hello there")).toBeTruthy();
  });

  it("renders the text content of an assistant message", () => {
    render(
      <ChatMessage
        message={assistant("The stock looks bullish.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    expect(screen.getByText(/bullish/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// @TICKER mention handling
// ---------------------------------------------------------------------------

describe("ChatMessage @TICKER mentions", () => {
  it("renders uppercase @AAPL as a clickable mention button", () => {
    render(
      <ChatMessage
        message={assistant("Check out @AAPL today.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    const btn = screen.getByRole("button", { name: /@AAPL/i });
    expect(btn).toBeTruthy();
  });

  it("clicking the mention button calls selectTicker with the correct symbol", () => {
    render(
      <ChatMessage
        message={assistant("@TSLA is moving fast.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    const btn = screen.getByRole("button", { name: /@TSLA/i });
    fireEvent.click(btn);
    expect(mockSelectTicker).toHaveBeenCalledWith("TSLA");
  });

  it("normalizes lowercase @aapl to AAPL before rendering", () => {
    render(
      <ChatMessage
        message={assistant("Watch @aapl closely.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    // The button text should be @AAPL (uppercased), not @aapl
    const btn = screen.getByRole("button", { name: /@AAPL/i });
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    expect(mockSelectTicker).toHaveBeenCalledWith("AAPL");
  });

  it("renders @portfolio as a non-interactive span, not a button", () => {
    render(
      <ChatMessage
        message={assistant("Your @portfolio is up 5%.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    const tag = document.querySelector(".mention-tag");
    expect(tag).toBeTruthy();
    // Must not be a button
    expect(tag?.tagName.toLowerCase()).not.toBe("button");
  });

  it("renders multiple mentions in a single message", () => {
    render(
      <ChatMessage
        message={assistant("Compare @AAPL and @MSFT performance.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    expect(screen.getByRole("button", { name: /@AAPL/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /@MSFT/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// [TRADE: ...] block
// ---------------------------------------------------------------------------

describe("ChatMessage trade block", () => {
  it("renders a trade review button for valid BUY trade", () => {
    const content = 'I recommend: [TRADE: {"action":"BUY","ticker":"NVDA","qty":5}]';
    render(
      <ChatMessage message={assistant(content)} isStreaming={false} onReviewTrade={noop} />
    );
    const btn = screen.getByRole("button", { name: /Review Trade/i });
    expect(btn).toBeTruthy();
    expect(btn.textContent).toContain("BUY");
    expect(btn.textContent).toContain("NVDA");
  });

  it("calls onReviewTrade with correct payload when trade button is clicked", () => {
    const content = '[TRADE: {"action":"SELL","ticker":"TSLA","qty":10}]';
    render(
      <ChatMessage message={assistant(content)} isStreaming={false} onReviewTrade={noop} />
    );
    fireEvent.click(screen.getByRole("button", { name: /Review Trade/i }));
    expect(noop).toHaveBeenCalledWith({ action: "SELL", ticker: "TSLA", qty: 10 });
  });

  it("renders malformed TRADE JSON as raw text (no trade button)", () => {
    const content = "[TRADE: {not valid json}]";
    render(
      <ChatMessage message={assistant(content)} isStreaming={false} onReviewTrade={noop} />
    );
    expect(screen.queryByRole("button", { name: /Review Trade/i })).toBeNull();
    // Raw text should appear
    expect(screen.getByText(/not valid json/)).toBeTruthy();
  });

  it("rejects trades with invalid action field", () => {
    const content = '[TRADE: {"action":"HOLD","ticker":"AAPL","qty":1}]';
    render(
      <ChatMessage message={assistant(content)} isStreaming={false} onReviewTrade={noop} />
    );
    expect(screen.queryByRole("button", { name: /Review Trade/i })).toBeNull();
  });
});

describe("ChatMessage timeline behavior", () => {
  it("renders discrete step rows when the same stepId appears multiple times", () => {
    render(
      <ChatMessage
        message={{
          ...assistant(""),
          timeline: [
            {
              type: "step",
              key: "step-1",
              step: {
                seq: 1,
                stepId: "fetch_quotes",
                message: "Fetching quotes",
                state: "running",
                category: "tool",
              },
            },
            {
              type: "step",
              key: "step-2",
              step: {
                seq: 2,
                stepId: "fetch_quotes",
                message: "Quotes fetched",
                state: "done",
                category: "tool",
              },
            },
          ],
        }}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );

    expect(screen.getByText("Fetching quotes")).toBeTruthy();
    expect(screen.getByText("Quotes fetched")).toBeTruthy();
    expect(document.querySelectorAll(".chat-step-row")).toHaveLength(2);
  });

  it("renders alternating text and step timeline entries in chronological order", () => {
    const { container } = render(
      <ChatMessage
        message={{
          ...assistant(""),
          timeline: [
            { type: "text", key: "text-1", content: "First text." },
            {
              type: "step",
              key: "step-1",
              step: {
                seq: 1,
                stepId: "s1",
                message: "Step one running",
                state: "running",
                category: "stage",
              },
            },
            { type: "text", key: "text-2", content: "Second text." },
            {
              type: "step",
              key: "step-2",
              step: {
                seq: 2,
                stepId: "s2",
                message: "Step two done",
                state: "done",
                category: "stage",
              },
            },
          ],
        }}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );

    expect(screen.getByText("First text.")).toBeTruthy();
    expect(screen.getByText("Second text.")).toBeTruthy();
    expect(screen.getByText("Step one running")).toBeTruthy();
    expect(screen.getByText("Step two done")).toBeTruthy();

    const fullText = container.textContent ?? "";
    expect(fullText.indexOf("First text.")).toBeLessThan(fullText.indexOf("Step one running"));
    expect(fullText.indexOf("Step one running")).toBeLessThan(fullText.indexOf("Second text."));
    expect(fullText.indexOf("Second text.")).toBeLessThan(fullText.indexOf("Step two done"));
  });

  it("renders tool cards in timeline and supports citation marker buttons", () => {
    render(
      <ChatMessage
        message={{
          ...assistant("Use the quote [1] for context."),
          timeline: [
            { type: "text", key: "text-1", content: "Use the quote [1] for context." },
            {
              type: "tool_card",
              key: "tool-card-1",
              card: {
                id: "tool-card-seq-11",
                seq: 11,
                tool: "price_quote",
                status: "done",
                resultEnvelope: {
                  data: { symbol: "AAPL", price: 190.22 },
                  provenance: { source: "fmp", retrieved_at: "2026-03-03T00:00:00Z" },
                  sources: [{ title: "FMP", url: "https://example.com", fetched_at: "2026-03-03T00:00:00Z" }],
                },
              },
            },
          ],
          toolCards: [
            {
              id: "tool-card-seq-11",
              seq: 11,
              tool: "price_quote",
              status: "done",
              resultEnvelope: {
                data: { symbol: "AAPL", price: 190.22 },
                provenance: { source: "fmp", retrieved_at: "2026-03-03T00:00:00Z" },
                sources: [{ title: "FMP", url: "https://example.com", fetched_at: "2026-03-03T00:00:00Z" }],
              },
            },
          ],
        }}
        isStreaming={false}
        onReviewTrade={noop}
      />,
    );

    expect(screen.getByText(/price quote/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "1" })).toBeTruthy();
  });
});

describe("ChatMessage run explorer", () => {
  it("renders a Run Explorer button and calls callback", () => {
    render(
      <ChatMessage
        message={{ ...assistant("Done."), runId: "run-123" }}
        isStreaming={false}
        onReviewTrade={noop}
        onOpenRunExplorer={onOpenRunExplorer}
      />,
    );

    const btn = screen.getByRole("button", { name: /Run Explorer/i });
    fireEvent.click(btn);
    expect(onOpenRunExplorer).toHaveBeenCalledWith("run-123");
  });
});

// ---------------------------------------------------------------------------
// External links and basic GFM
// ---------------------------------------------------------------------------

describe("ChatMessage markdown features", () => {
  it("renders external links as <a> tags (not mention buttons)", () => {
    render(
      <ChatMessage
        message={assistant("See [SEC](https://sec.gov) for details.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    const link = screen.getByRole("link", { name: "SEC" });
    expect(link.getAttribute("href")).toBe("https://sec.gov");
  });

  it("renders bold text without crashing", () => {
    render(
      <ChatMessage
        message={assistant("**Strong** recommendation.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    // <strong> element should be in the DOM
    expect(document.querySelector("strong")).toBeTruthy();
  });

  it("renders inline code spans without crashing", () => {
    render(
      <ChatMessage
        message={assistant("Use `P/E ratio` as a metric.")}
        isStreaming={false}
        onReviewTrade={noop}
      />
    );
    expect(document.querySelector("code")).toBeTruthy();
  });

  it("renders a streaming typing cursor when isStreaming is true for assistant", () => {
    render(
      <ChatMessage message={assistant("Thinking...")} isStreaming={true} onReviewTrade={noop} />
    );
    expect(document.querySelector(".typing-cursor")).toBeTruthy();
  });

  it("does not render a typing cursor for user messages", () => {
    render(
      <ChatMessage message={user("Hello")} isStreaming={true} onReviewTrade={noop} />
    );
    expect(document.querySelector(".typing-cursor")).toBeNull();
  });
});
