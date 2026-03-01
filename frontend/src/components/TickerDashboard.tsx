import { useState } from "react";
import { useAppContext } from "../context/AppContext";
import { Spinner } from "./Spinner";
import { TickerCard } from "./TickerCard";

export function TickerDashboard() {
  const { state, toggleWatchlist } = useAppContext();
  const {
    activeTicker,
    activeTickerLoading,
    activeTickerError,
    selectedSymbol,
    tickerReport,
    tickerReportLoading,
    tickerReportError,
    watchlist,
  } = state;
  const [starring, setStarring] = useState(false);

  const isWatched = selectedSymbol != null && watchlist.some((w) => w.ticker === selectedSymbol);

  const handleStar = async () => {
    if (!selectedSymbol || starring) return;
    setStarring(true);
    try {
      await toggleWatchlist(selectedSymbol);
    } finally {
      setStarring(false);
    }
  };

  return (
    <aside className="pane-dashboard">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <h2 className="pane-title">Ticker</h2>
        {selectedSymbol && (
          <button
            className="btn-ghost"
            onClick={handleStar}
            disabled={starring}
            title={isWatched ? "Remove from Watchlist" : "Add to Watchlist"}
            style={{ fontSize: "18px", lineHeight: 1, padding: "2px 6px", color: isWatched ? "var(--accent)" : "var(--text-muted)" }}
          >
            {starring ? <Spinner size={14} /> : isWatched ? "★" : "☆"}
          </button>
        )}
      </div>

      {/* Empty state */}
      {!selectedSymbol && !activeTickerLoading && (
        <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
          Click a portfolio position or mention a ticker in chat to see details here.
        </p>
      )}

      {/* Loading ticker data */}
      {activeTickerLoading && (
        <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--text-muted)", fontSize: "13px" }}>
          <Spinner />
          Loading {selectedSymbol}...
        </div>
      )}

      {/* Error */}
      {activeTickerError && !activeTickerLoading && (
        <div style={{ color: "var(--red)", fontSize: "13px" }}>
          {activeTickerError}
        </div>
      )}

      {/* Ticker stats */}
      {activeTicker && !activeTickerLoading && (
        <>
          <TickerCard info={activeTicker} />

          <div style={{ borderTop: "1px solid var(--border)", marginTop: "20px", paddingTop: "16px" }}>
            <h3 style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "12px" }}>
              AI Analysis
            </h3>

            {tickerReportLoading && tickerReport === "" && !tickerReportError && (
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--text-muted)", fontSize: "13px" }}>
                <Spinner />
                Generating AI analysis...
              </div>
            )}

            {tickerReportError && !tickerReportLoading && (
              <div style={{ color: "var(--red)", fontSize: "13px" }}>
                {tickerReportError}
              </div>
            )}

            {!tickerReportLoading && !tickerReportError && !tickerReport && (
              <p style={{ color: "var(--red)", fontSize: "13px" }}>
                No analysis available. Please verify your LLM API key configuration and ensure sufficient data payload is loaded.
              </p>
            )}

            {tickerReport && (
              <p style={{ fontSize: "13px", lineHeight: 1.7, color: "var(--text)", whiteSpace: "pre-wrap" }}>
                {tickerReport}
                {tickerReportLoading && (
                  <span className="typing-cursor" />
                )}
              </p>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
