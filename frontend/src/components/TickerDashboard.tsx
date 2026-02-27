import { useAppContext } from "../context/AppContext";
import { Spinner } from "./Spinner";
import { TickerCard } from "./TickerCard";

export function TickerDashboard() {
  const { state } = useAppContext();
  const {
    activeTicker,
    activeTickerLoading,
    activeTickerError,
    selectedSymbol,
    tickerReport,
    tickerReportLoading,
  } = state;

  return (
    <aside className="pane-dashboard">
      <h2 className="pane-title" style={{ marginBottom: "16px" }}>Ticker</h2>

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

            {tickerReportLoading && tickerReport === "" && (
              <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "var(--text-muted)", fontSize: "13px" }}>
                <Spinner />
                Checking report cache...
              </div>
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
