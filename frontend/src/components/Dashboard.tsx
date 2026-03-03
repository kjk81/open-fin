import { useEffect, useMemo, useState } from "react";

import { fetchDashboardMetrics } from "../api";
import { useAppContext } from "../context/AppContext";
import type { DashboardMetrics, DashboardStockMetric } from "../types";

const EMPTY_METRICS: DashboardMetrics = {
  best_pe: [],
  best_agent_score: [],
};

function MetricList({ items, valueLabel }: { items: DashboardStockMetric[]; valueLabel: "pe" | "score" }) {
  if (items.length === 0) {
    return <p className="dashboard-empty">No data available.</p>;
  }

  return (
    <ul className="dashboard-metric-list">
      {items.map((item) => (
        <li key={item.symbol} className="dashboard-metric-row">
          <span className="dashboard-metric-symbol">{item.symbol}</span>
          {valueLabel === "pe" ? (
            <span className="dashboard-metric-value">
              {item.trailing_pe != null ? item.trailing_pe.toFixed(2) : "—"}
            </span>
          ) : (
            <span className="dashboard-metric-value">{item.agent_score}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Dashboard() {
  const { selectTicker } = useAppContext();
  const [symbolInput, setSymbolInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<DashboardMetrics>(EMPTY_METRICS);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchDashboardMetrics()
      .then((data) => {
        if (!cancelled) {
          setMetrics(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setMetrics(EMPTY_METRICS);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const disabled = useMemo(() => symbolInput.trim().length === 0, [symbolInput]);

  const onGo = () => {
    const symbol = symbolInput.trim().toUpperCase();
    if (!symbol) return;
    selectTicker(symbol);
  };

  return (
    <aside className="pane-dashboard">
      <h2 className="pane-title" style={{ marginBottom: "12px" }}>Dashboard</h2>

      <div className="dashboard-search-row">
        <input
          className="dashboard-search-input"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          placeholder="Enter ticker (e.g. AAPL)"
          aria-label="Ticker symbol"
        />
        <button className="btn-ghost dashboard-go-btn" onClick={onGo} disabled={disabled}>
          Go
        </button>
      </div>

      {error && <p className="dashboard-error">{error}</p>}
      {loading && <p className="dashboard-empty">Loading metrics...</p>}

      <section className="dashboard-card">
        <h3 className="dashboard-card-title">Stored stocks with best P/E</h3>
        <MetricList items={metrics.best_pe} valueLabel="pe" />
      </section>

      <section className="dashboard-card">
        <h3 className="dashboard-card-title">Best Agent Score</h3>
        <MetricList items={metrics.best_agent_score} valueLabel="score" />
      </section>
    </aside>
  );
}
