import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisPanel } from "../components/AnalysisPanel";
import type { TickerAnalysis } from "../types";

function makeAnalysis(overrides: Partial<TickerAnalysis> = {}): TickerAnalysis {
  return {
    sections: {
      fundamentals: {
        rating: "Buy",
        content: "Revenue and margin trends are improving.",
        source: "llm",
        loading: false,
      },
      sentiment: {
        rating: "Bullish",
        content: "News flow remains positive.",
        source: "llm",
        loading: false,
      },
      technical: {
        rating: "Uptrend",
        content: "Price is above key moving averages.",
        source: "llm",
        loading: false,
      },
    },
    overallRating: null,
    loading: false,
    error: null,
    ...overrides,
  };
}

describe("AnalysisPanel", () => {
  it("renders section headers in fixed order: Fundamentals, Sentiment, Technical", () => {
    const { container } = render(<AnalysisPanel analysis={makeAnalysis()} />);
    const titles = Array.from(container.querySelectorAll(".analysis-section-title")).map((el) =>
      (el.textContent ?? "").trim(),
    );
    expect(titles).toEqual(["Fundamentals", "Sentiment", "Technical"]);
  });

  it("renders per-section rating badges and overall rating badge when provided", () => {
    render(
      <AnalysisPanel
        analysis={makeAnalysis({
          overallRating: "Strong Buy",
        })}
      />,
    );

    expect(screen.getByText("Strong Buy")).toBeTruthy();
    expect(screen.getByText("Buy")).toBeTruthy();
    expect(screen.getByText("Bullish")).toBeTruthy();
    expect(screen.getByText("Uptrend")).toBeTruthy();
  });

  it("renders error text when analysis.error is set", () => {
    render(<AnalysisPanel analysis={makeAnalysis({ error: "Analysis backend unavailable" })} />);
    expect(screen.getByText("Analysis backend unavailable")).toBeTruthy();
  });
});
