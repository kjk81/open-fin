import { useEffect, useState } from "react";
import { fetchExecutions } from "../api";
import type { LoadoutExecution } from "../types";

const PAGE_SIZE = 10;

export function LoadoutExecutionLog({ loadoutId }: { loadoutId: number }) {
  const [items, setItems] = useState<LoadoutExecution[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);

    fetchExecutions(loadoutId, page * PAGE_SIZE, PAGE_SIZE)
      .then((res) => {
        if (!mounted) return;
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((err) => {
        if (!mounted) return;
        setError(String(err));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [loadoutId, page]);

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="loadout-log">
      <div className="loadout-log-header">
        <span>Execution Log</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="kg-btn" disabled={page <= 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
            Prev
          </button>
          <button className="kg-btn" disabled={page >= maxPage} onClick={() => setPage((p) => Math.min(maxPage, p + 1))}>
            Next
          </button>
        </div>
      </div>

      {loading ? (
        <div className="loadout-log-empty">Loading executions…</div>
      ) : error ? (
        <div className="loadout-log-empty" style={{ color: "var(--red)" }}>{error}</div>
      ) : items.length === 0 ? (
        <div className="loadout-log-empty">No executions yet.</div>
      ) : (
        <table className="loadout-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Ticker</th>
              <th>Qty</th>
              <th>Confidence</th>
              <th>Status</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{new Date(item.timestamp).toLocaleString()}</td>
                <td>{item.action}</td>
                <td>{item.ticker}</td>
                <td>{item.quantity}</td>
                <td>{item.confidence.toFixed(2)}</td>
                <td>
                  <span className={`loadout-status loadout-status--${item.status}`}>{item.status}</span>
                </td>
                <td title={item.error_trace ?? ""}>{item.error_trace ? "View" : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
