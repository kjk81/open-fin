import { useEffect, useRef, useCallback } from "react";
import { SigmaContainer, useRegisterEvents, useSigma } from "@react-sigma/core";
import { useWorkerLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import type MultiDirectedGraph from "graphology";
import type { NodeKind } from "../../types";
import {
  NODE_COLORS,
  NODE_COLORS_DIM,
  EDGE_COLORS,
  LOD_EDGE_HIDE_RATIO,
  LOD_LABEL_SIZE_THRESHOLD,
} from "./graphHelpers";

import "@react-sigma/core/lib/style.css";
import { ErrorBoundary } from "../ErrorBoundary";

// ── ForceAtlas2 controller ───────────────────────────────────────────────────

interface FA2ControllerProps {
  lowResourceMode: boolean;
  shouldRun: boolean;
}

function FA2Controller({ lowResourceMode, shouldRun }: FA2ControllerProps) {
  const { start, stop, kill } = useWorkerLayoutForceAtlas2({
    settings: {
      gravity: 1,
      scalingRatio: 2,
      slowDown: 8,
      barnesHutOptimize: true,
    },
  });

  useEffect(() => {
    if (!shouldRun) return;
    start();
    if (lowResourceMode) {
      const t = setTimeout(() => stop(), 3000);
      return () => { clearTimeout(t); stop(); };
    }
    return () => stop();
  }, [shouldRun, lowResourceMode, start, stop]);

  // Kill worker on unmount
  useEffect(() => () => { kill(); }, [kill]);

  return null;
}

// ── Event handlers (click node → expand ego) ─────────────────────────────────

interface EventHandlerProps {
  search: string;
  kindFilter: NodeKind | "";
  onNodeClick: (nodeId: string) => void;
}

function GraphEventHandlers({ search, kindFilter, onNodeClick }: EventHandlerProps) {
  const sigma = useSigma();
  const registerEvents = useRegisterEvents();
  const searchFocusTimer = useRef<number | null>(null);

  // Node reducers: LOD + search dim + kind filter dim
  useEffect(() => {
    const searchUpper = search.toUpperCase();

    sigma.setSetting("nodeReducer", (node: string, data: Record<string, unknown>) => {
      const camera = sigma.getCamera();
      const ratio = camera.ratio;

      // In low-resource / zoomed-out mode, cap label size to avoid rendering thousands
      const displayData = { ...data };

      // Kind filter dim
      if (kindFilter && data["kind"] !== kindFilter) {
        displayData["color"] = NODE_COLORS_DIM;
        displayData["label"] = null;
        displayData["zIndex"] = 0;
        return displayData;
      }

      // Search dim
      if (searchUpper && !node.toUpperCase().includes(searchUpper)) {
        displayData["color"] = NODE_COLORS_DIM;
        displayData["label"] = null;
        displayData["zIndex"] = 0;
        return displayData;
      }

      // LOD: hide labels when zoomed out
      const renderedSize = (data["size"] as number) / ratio;
      if (renderedSize < LOD_LABEL_SIZE_THRESHOLD) {
        displayData["label"] = null;
      }

      return displayData;
    });

    sigma.setSetting("edgeReducer", (_edge: string, data: Record<string, unknown>) => {
      const ratio = sigma.getCamera().ratio;
      // Hide CO_MENTION edges when zoomed out — biggest category, saves GPU
      if (ratio > LOD_EDGE_HIDE_RATIO && data["kind"] === "CO_MENTION") {
        return { ...data, hidden: true };
      }
      return data;
    });

    sigma.refresh();
  }, [sigma, search, kindFilter]);

  // Search focus: dim non-matches via reducer and focus camera on matching neighborhood.
  useEffect(() => {
    if (searchFocusTimer.current) {
      window.clearTimeout(searchFocusTimer.current);
      searchFocusTimer.current = null;
    }

    const searchUpper = search.trim().toUpperCase();
    if (!searchUpper) return;

    searchFocusTimer.current = window.setTimeout(() => {
      const graph = sigma.getGraph();
      const matches: string[] = [];

      graph.forEachNode((node) => {
        if (node.toUpperCase().includes(searchUpper)) matches.push(node);
      });

      if (matches.length === 0) return;

      const neighborhood = new Set<string>();
      for (const node of matches.slice(0, 50)) {
        neighborhood.add(node);
        graph.forEachNeighbor(node, (neighbor) => neighborhood.add(neighbor));
      }

      let minX = Number.POSITIVE_INFINITY;
      let minY = Number.POSITIVE_INFINITY;
      let maxX = Number.NEGATIVE_INFINITY;
      let maxY = Number.NEGATIVE_INFINITY;

      neighborhood.forEach((node) => {
        if (!graph.hasNode(node)) return;
        const x = graph.getNodeAttribute(node, "x") as number;
        const y = graph.getNodeAttribute(node, "y") as number;
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
      });

      if (!Number.isFinite(minX) || !Number.isFinite(minY)) return;

      const centerX = (minX + maxX) / 2;
      const centerY = (minY + maxY) / 2;
      const span = Math.max(maxX - minX, maxY - minY);
      const ratio = Math.min(2.5, Math.max(0.2, span * 1.4 || 0.5));

      sigma.getCamera().animate({ x: centerX, y: centerY, ratio }, { duration: 350 });
    }, 160);

    return () => {
      if (searchFocusTimer.current) {
        window.clearTimeout(searchFocusTimer.current);
        searchFocusTimer.current = null;
      }
    };
  }, [sigma, search]);

  // Register click event: clicking a node triggers ego loading
  useEffect(() => {
    registerEvents({
      clickNode: (event) => {
        onNodeClick(event.node);
      },
    });
  }, [registerEvents, onNodeClick]);

  return null;
}

// ── Camera focus helper ──────────────────────────────────────────────────────

interface CameraFocusProps {
  focusNode: string | null;
}

function CameraFocus({ focusNode }: CameraFocusProps) {
  const sigma = useSigma();
  const prevFocus = useRef<string | null>(null);

  useEffect(() => {
    if (!focusNode || focusNode === prevFocus.current) return;
    prevFocus.current = focusNode;
    const pos = sigma.getNodeDisplayData(focusNode);
    if (pos) {
      sigma.getCamera().animate(
        { x: pos.x, y: pos.y, ratio: 0.5 },
        { duration: 400 },
      );
    }
  }, [sigma, focusNode]);

  return null;
}

// ── Main network view ────────────────────────────────────────────────────────

interface KGNetworkViewProps {
  graphRef: React.MutableRefObject<MultiDirectedGraph>;
  search: string;
  kindFilter: NodeKind | "";
  lowResourceMode: boolean;
  nodeCount: number;
  focusNode: string | null;
  onNodeClick: (nodeId: string) => void;
}

export function KGNetworkView({
  graphRef,
  search,
  kindFilter,
  lowResourceMode,
  nodeCount,
  focusNode,
  onNodeClick,
}: KGNetworkViewProps) {
  const hasNodes = nodeCount > 0;

  const sigmaSettings = {
    renderLabels: true,
    labelSize: 12,
    labelWeight: "600",
    labelColor: { color: "#e2e8f0" },
    edgeLabelSize: 10,
    labelRenderedSizeThreshold: lowResourceMode ? 14 : 8,
    hideEdgesOnMove: lowResourceMode,
    renderEdgeLabels: !lowResourceMode,
    zIndex: !lowResourceMode,
    defaultNodeColor: NODE_COLORS["ticker"],
    defaultEdgeColor: EDGE_COLORS["CO_MENTION"],
    // Node programs (default: filled circle — fast WebGL)
    allowInvalidContainer: true,
  };

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      onNodeClick(nodeId);
    },
    [onNodeClick],
  );

  if (!hasNodes) {
    return (
      <div className="kg-empty">
        <span style={{ fontSize: 32 }}>🕸</span>
        <span>No graph data loaded.</span>
        <span style={{ fontSize: 12 }}>Analyze tickers in the Co-Pilot tab, or expand an ego network using the toolbar.</span>
      </div>
    );
  }

  return (
    <ErrorBoundary label="Knowledge Graph">
      <SigmaContainer
        graph={graphRef.current}
        settings={sigmaSettings}
        style={{ width: "100%", height: "100%", background: "#0f172a" }}
      >
        <FA2Controller lowResourceMode={lowResourceMode} shouldRun={hasNodes} />
        <GraphEventHandlers
          search={search}
          kindFilter={kindFilter}
          onNodeClick={handleNodeClick}
        />
        <CameraFocus focusNode={focusNode} />
      </SigmaContainer>
    </ErrorBoundary>
  );
}
