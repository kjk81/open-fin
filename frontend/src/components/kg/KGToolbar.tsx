import { useRef } from "react";
import type { GraphSummary, NodeKind } from "../../types";

export type KGView = "network" | "table";

interface KGToolbarProps {
  view: KGView;
  onViewChange: (v: KGView) => void;
  search: string;
  onSearchChange: (s: string) => void;
  kindFilter: NodeKind | "";
  onKindFilterChange: (k: NodeKind | "") => void;
  lowResourceMode: boolean;
  onLowResourceModeChange: (v: boolean) => void;
  summary: GraphSummary | null;
  egoLoading: boolean;
  onLoadEgo: (ticker: string, depth: number) => void;
}

export function KGToolbar({
  view,
  onViewChange,
  search,
  onSearchChange,
  kindFilter,
  onKindFilterChange,
  lowResourceMode,
  onLowResourceModeChange,
  summary,
  egoLoading,
  onLoadEgo,
}: KGToolbarProps) {
  const tickerInputRef = useRef<HTMLInputElement>(null);

  function handleExpandSubmit(e: React.FormEvent) {
    e.preventDefault();
    const val = tickerInputRef.current?.value.trim().toUpperCase();
    if (val) {
      onLoadEgo(val, 2);
      if (tickerInputRef.current) tickerInputRef.current.value = "";
    }
  }

  return (
    <div className="kg-toolbar">
      {/* Search */}
      <div className="kg-toolbar-group">
        <input
          className="kg-search"
          type="text"
          placeholder="Search nodes..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </div>

      <div className="kg-toolbar-sep" />

      {/* Kind filter */}
      <div className="kg-toolbar-group">
        <select
          className="kg-select"
          value={kindFilter}
          onChange={(e) => onKindFilterChange(e.target.value as NodeKind | "")}
        >
          <option value="">All types</option>
          <option value="ticker">Tickers</option>
          <option value="sector">Sectors</option>
          <option value="industry">Industries</option>
        </select>
      </div>

      <div className="kg-toolbar-sep" />

      {/* Ego expand form */}
      <form className="kg-toolbar-group" onSubmit={handleExpandSubmit}>
        <input
          ref={tickerInputRef}
          className="kg-search"
          style={{ width: 100 }}
          type="text"
          placeholder="AAPL…"
          maxLength={10}
        />
        <button className="kg-btn" type="submit" disabled={egoLoading}>
          {egoLoading ? "Loading…" : "Expand ego"}
        </button>
      </form>

      <div className="kg-toolbar-sep" />

      {/* Low Resource Mode */}
      <div className="kg-toolbar-group">
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={lowResourceMode}
            onChange={(e) => onLowResourceModeChange(e.target.checked)}
            style={{ accentColor: "var(--accent)" }}
          />
          Low Resource Mode
        </label>
      </div>

      {/* Stats */}
      {summary && (
        <span className="kg-stats">
          {summary.node_count.toLocaleString()} nodes · {summary.edge_count.toLocaleString()} edges · {summary.communities.length} communities
        </span>
      )}

      <div className="kg-toolbar-sep" style={{ marginLeft: summary ? 0 : "auto" }} />

      {/* View toggle */}
      <div className="kg-view-toggle">
        <button
          className={view === "network" ? "active" : ""}
          onClick={() => onViewChange("network")}
        >
          Network
        </button>
        <button
          className={view === "table" ? "active" : ""}
          onClick={() => onViewChange("table")}
        >
          Table
        </button>
      </div>
    </div>
  );
}
