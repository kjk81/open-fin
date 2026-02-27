import { useState } from "react";
import { useAppContext } from "../context/AppContext";
import { syncPortfolio } from "../api";
import { fmt } from "../utils";
import { Spinner } from "./Spinner";

export function PortfolioSidebar() {
  const { state, selectTicker, reloadPortfolio } = useAppContext();
  const { portfolio, selectedSymbol, watchlist } = state;
  const [syncing, setSyncing] = useState(false);

  const totalValue = portfolio.reduce((s, p) => s + p.market_value, 0);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await syncPortfolio();
      await reloadPortfolio();
    } catch {
      // ignore
    } finally {
      setSyncing(false);
    }
  };

  return (
    <aside className="pane-sidebar">
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <h2 className="pane-title">Portfolio</h2>
        <button
          className="btn-ghost"
          onClick={handleSync}
          disabled={syncing}
          title="Sync from Alpaca"
        >
          {syncing ? <Spinner size={12} /> : "↻"} Sync
        </button>
      </div>

      {portfolio.length === 0 ? (
        <p style={{ color: "var(--text-muted)", fontSize: "12px", lineHeight: 1.6 }}>
          No positions. Add Alpaca keys to{" "}
          <code style={{ background: "var(--bg)", padding: "1px 5px", borderRadius: "3px" }}>
            backend/.env
          </code>{" "}
          and restart.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {portfolio.map((p) => {
            const selected = selectedSymbol === p.symbol;
            const pnl = p.current_price - p.avg_entry_price;
            const pnlPct = (pnl / p.avg_entry_price) * 100;
            const pnlColor = pnl >= 0 ? "var(--green)" : "var(--red)";

            return (
              <button
                key={p.symbol}
                className={`position-row${selected ? " position-row--selected" : ""}`}
                onClick={() => selectTicker(p.symbol)}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <span style={{ fontWeight: 700, fontSize: "14px" }}>{p.symbol}</span>
                  <span style={{ fontWeight: 600, fontSize: "13px" }}>${fmt(p.market_value)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: "4px" }}>
                  <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                    {p.qty} sh · ${fmt(p.current_price)}
                  </span>
                  <span style={{ fontSize: "11px", color: pnlColor }}>
                    {pnl >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
                  </span>
                </div>
              </button>
            );
          })}

          <div style={{
            borderTop: "1px solid var(--border)",
            paddingTop: "8px",
            display: "flex",
            justifyContent: "space-between",
            fontSize: "12px",
            color: "var(--text-muted)",
            marginTop: "4px",
          }}>
            <span>Total</span>
            <span style={{ color: "var(--text)", fontWeight: 600 }}>${fmt(totalValue)}</span>
          </div>
        </div>
      )}
      {/* Watchlist section */}
      <div style={{ marginTop: "24px" }}>
        <h2 className="pane-title" style={{ marginBottom: "12px" }}>Watchlist</h2>
        {watchlist.length === 0 ? (
          <p style={{ color: "var(--text-muted)", fontSize: "12px", lineHeight: 1.6 }}>
            Star a ticker in the dashboard to add it here.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {watchlist.map((w) => {
              const selected = selectedSymbol === w.ticker;
              return (
                <button
                  key={w.ticker}
                  className={`position-row${selected ? " position-row--selected" : ""}`}
                  onClick={() => selectTicker(w.ticker)}
                  style={{ padding: "8px 10px" }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ color: "var(--accent)", fontSize: "14px" }}>★</span>
                    <span style={{ fontWeight: 700, fontSize: "14px" }}>{w.ticker}</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
