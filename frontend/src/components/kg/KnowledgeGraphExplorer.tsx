import { useState, useEffect, useCallback } from "react";
import { KGToolbar, type KGView } from "./KGToolbar";
import { KGNetworkView } from "./KGNetworkView";
import { KGTableView } from "./KGTableView";
import { useGraphData } from "./useGraphData";
import { useAppContext } from "../../context/AppContext";
import type { NodeKind } from "../../types";

/**
 * KnowledgeGraphExplorer — full-width tab that replaces the 3-pane layout.
 * Dual-view: Network (Sigma WebGL) and Table (TanStack Virtual).
 */
export function KnowledgeGraphExplorer() {
  const [view, setView] = useState<KGView>("network");
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<NodeKind | "">("");
  const [lowResourceMode, setLowResourceMode] = useState(false);
  const [focusNode, setFocusNode] = useState<string | null>(null);
  const [nodeCount, setNodeCount] = useState(0);

  const {
    graphRef,
    summary,
    summaryLoading,
    summaryError,
    egoLoading,
    egoError,
    loadSummary,
    loadEgo,
    resetGraph,
  } = useGraphData();

  const { state: appState } = useAppContext();

  // Load summary on mount
  useEffect(() => {
    loadSummary();
  }, [loadSummary]);


  // Keep nodeCount in sync with the graphology instance
  // (graphology is mutable, not reactive, so we track it separately)
  const refreshNodeCount = useCallback(() => {
    setNodeCount(graphRef.current.order);
  }, [graphRef]);

  const handleLoadEgo = useCallback(
    async (ticker: string, depth = 2) => {
      await loadEgo(ticker, depth);
      refreshNodeCount();
      // Switch to network view and focus on the expanded node
      setView("network");
      setFocusNode(ticker.toUpperCase());
    },
    [loadEgo, refreshNodeCount],
  );

  // Auto-refresh summary and auto-load ego when KG is updated via analysis
  useEffect(() => {
    if (appState.kgLastUpdated > 0) {
      loadSummary();
      if (appState.kgLastTicker) {
        handleLoadEgo(appState.kgLastTicker, 2);
      }
    }
  }, [appState.kgLastUpdated, loadSummary, handleLoadEgo]);

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      // Clicking a ticker node expands its ego (depth 1 for performance)
      const kind = graphRef.current.getNodeAttribute(nodeId, "kind");
      if (kind === "ticker") {
        handleLoadEgo(nodeId, 1);
      }
    },
    [graphRef, handleLoadEgo],
  );

  const handleReset = useCallback(() => {
    resetGraph();
    setNodeCount(0);
    setFocusNode(null);
  }, [resetGraph]);

  return (
    <div className="kg-explorer">
      <KGToolbar
        view={view}
        onViewChange={setView}
        search={search}
        onSearchChange={setSearch}
        kindFilter={kindFilter}
        onKindFilterChange={setKindFilter}
        lowResourceMode={lowResourceMode}
        onLowResourceModeChange={setLowResourceMode}
        summary={summary}
        egoLoading={egoLoading}
        onLoadEgo={handleLoadEgo}
      />

      {/* Inline status messages */}
      {(summaryLoading || summaryError || egoError) && (
        <div style={{
          padding: "6px 16px",
          fontSize: 12,
          color: summaryError || egoError ? "var(--red)" : "var(--text-muted)",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface)",
        }}>
          {summaryLoading && "Loading graph summary…"}
          {summaryError && `Summary error: ${summaryError}`}
          {egoError && `Ego error: ${egoError}`}
        </div>
      )}

      <div className="kg-body">
        {view === "network" ? (
          <KGNetworkView
            graphRef={graphRef}
            search={search}
            kindFilter={kindFilter}
            lowResourceMode={lowResourceMode}
            nodeCount={nodeCount}
            focusNode={focusNode}
            onNodeClick={handleNodeClick}
          />
        ) : (
          <KGTableView
            search={search}
            kindFilter={kindFilter}
            onExpandEgo={(ticker) => {
              handleLoadEgo(ticker, 2);
              setView("network");
            }}
          />
        )}
      </div>

      {/* Footer: reset + node count when nodes exist */}
      {nodeCount > 0 && (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "5px 14px",
          borderTop: "1px solid var(--border)",
          background: "var(--surface)",
          fontSize: 11,
          color: "var(--text-muted)",
        }}>
          <span>{nodeCount.toLocaleString()} nodes in view</span>
          <button
            className="kg-btn"
            style={{ fontSize: 11, padding: "2px 10px" }}
            onClick={handleReset}
          >
            Clear graph
          </button>
        </div>
      )}
    </div>
  );
}
