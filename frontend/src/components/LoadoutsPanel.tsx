import { useCallback, useEffect, useState } from "react";
import {
  createLoadout,
  deleteLoadout,
  fetchLoadouts,
  fetchStrategies,
  updateLoadout,
} from "../api";
import type { Loadout, StrategyInfo } from "../types";
import { LoadoutExecutionLog } from "./LoadoutExecutionLog";

const DEFAULT_CONFIRM_TEXT = "This will execute real trades on schedule. Continue?";
const SECOND_CONFIRM_TOKEN = "ENABLE";

export function LoadoutsPanel() {
  const [loadouts, setLoadouts] = useState<Loadout[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [ticker, setTicker] = useState("");
  const [strategyName, setStrategyName] = useState("momentum");
  const [schedule, setSchedule] = useState("0 9 * * 1-5");
  const [maxQty, setMaxQty] = useState(100);
  const [dryRun, setDryRun] = useState(true);
  const [expandedLoadoutId, setExpandedLoadoutId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [loadoutRows, strategyRows] = await Promise.all([fetchLoadouts(), fetchStrategies()]);
      setLoadouts(loadoutRows);
      setStrategies(strategyRows);
      if (strategyRows.length > 0 && !strategyRows.some((s) => s.name === strategyName)) {
        setStrategyName(strategyRows[0].name);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [strategyName]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const onCreate = useCallback(async () => {
    if (!ticker.trim()) return;
    setError(null);
    try {
      await createLoadout({
        ticker: ticker.trim().toUpperCase(),
        strategy_name: strategyName,
        schedule: schedule.trim(),
        max_qty: maxQty,
        dry_run: dryRun,
        parameters: {},
      });
      setTicker("");
      setDryRun(true);
      await loadData();
    } catch (err) {
      setError(String(err));
    }
  }, [ticker, strategyName, schedule, maxQty, dryRun, loadData]);

  const onToggleActive = useCallback(async (loadout: Loadout) => {
    const next = !loadout.is_active;
    if (next) {
      if (!window.confirm(DEFAULT_CONFIRM_TEXT)) return;
      const confirmText = window.prompt(`Type ${SECOND_CONFIRM_TOKEN} to activate loadout #${loadout.id}.`);
      if ((confirmText ?? "").trim().toUpperCase() !== SECOND_CONFIRM_TOKEN) return;
    }
    try {
      await updateLoadout(loadout.id, { is_active: next });
      await loadData();
    } catch (err) {
      setError(String(err));
    }
  }, [loadData]);

  const onDelete = useCallback(async (id: number) => {
    try {
      await deleteLoadout(id);
      if (expandedLoadoutId === id) setExpandedLoadoutId(null);
      await loadData();
    } catch (err) {
      setError(String(err));
    }
  }, [expandedLoadoutId, loadData]);

  return (
    <div className="loadouts-panel">
      <div className="loadouts-toolbar">
        <input
          className="kg-search"
          placeholder="Ticker"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
        />
        <select className="kg-select" value={strategyName} onChange={(e) => setStrategyName(e.target.value)}>
          {(strategies.length ? strategies : [{ name: "momentum" }]).map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        <input
          className="kg-search"
          style={{ width: 180 }}
          placeholder="Cron schedule"
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
        />
        <input
          className="kg-search"
          style={{ width: 90 }}
          type="number"
          min={1}
          max={1000000}
          value={maxQty}
          onChange={(e) => setMaxQty(Number(e.target.value || 1))}
        />
        <label className="loadout-checkbox">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          Dry Run
        </label>
        <button className="btn-send" onClick={onCreate}>Create Loadout</button>
      </div>

      {error && <div className="loadout-error">{error}</div>}

      <div className="loadouts-table-wrap">
        {loading ? (
          <div className="loadout-log-empty">Loading loadouts…</div>
        ) : loadouts.length === 0 ? (
          <div className="loadout-log-empty">No loadouts configured.</div>
        ) : (
          <table className="loadout-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Ticker</th>
                <th>Strategy</th>
                <th>Cron</th>
                <th>Max Qty</th>
                <th>Dry Run</th>
                <th>Active</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loadouts.map((loadout) => (
                <tr key={loadout.id}>
                  <td>{loadout.id}</td>
                  <td>{loadout.ticker}</td>
                  <td>{loadout.strategy_name}</td>
                  <td>{loadout.schedule}</td>
                  <td>{loadout.max_qty}</td>
                  <td>{loadout.dry_run ? "Yes" : "No"}</td>
                  <td>
                    <label className="loadout-toggle">
                      <input
                        type="checkbox"
                        checked={loadout.is_active}
                        onChange={() => onToggleActive(loadout)}
                      />
                      <span className="loadout-toggle-slider" />
                    </label>
                  </td>
                  <td style={{ display: "flex", gap: 8 }}>
                    <button
                      className="kg-btn"
                      onClick={() => setExpandedLoadoutId((v) => (v === loadout.id ? null : loadout.id))}
                    >
                      {expandedLoadoutId === loadout.id ? "Hide Log" : "Show Log"}
                    </button>
                    <button className="kg-btn" onClick={() => onDelete(loadout.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {expandedLoadoutId != null && <LoadoutExecutionLog loadoutId={expandedLoadoutId} />}
    </div>
  );
}
