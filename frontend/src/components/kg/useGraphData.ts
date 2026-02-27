import { useRef, useState, useCallback } from "react";
import MultiDirectedGraph from "graphology";
import { fetchGraphSummary, fetchGraphEgo } from "../../api";
import type { GraphSummary, SubgraphData } from "../../types";
import { NODE_COLORS, EDGE_COLORS, degreeToSize, randomPosition, nodeLabel } from "./graphHelpers";

export interface GraphDataState {
  summary: GraphSummary | null;
  summaryLoading: boolean;
  summaryError: string | null;
  egoLoading: boolean;
  egoError: string | null;
  loadedTickers: Set<string>;
}

export interface GraphDataActions {
  graphRef: React.MutableRefObject<MultiDirectedGraph>;
  loadSummary: () => Promise<void>;
  loadEgo: (ticker: string, depth?: number) => Promise<void>;
  resetGraph: () => void;
}

/**
 * Manages a graphology MultiDirectedGraph as the Single Source of Truth.
 * React state is kept minimal — only loading/error flags and the summary.
 * All node/edge data lives in the mutable graphRef.current instance.
 */
export function useGraphData(): GraphDataState & GraphDataActions {
  const graphRef = useRef<MultiDirectedGraph>(new MultiDirectedGraph());

  const [summary, setSummary] = useState<GraphSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [egoLoading, setEgoLoading] = useState(false);
  const [egoError, setEgoError] = useState<string | null>(null);
  const [loadedTickers, setLoadedTickers] = useState<Set<string>>(new Set());

  const loadSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const data = await fetchGraphSummary();
      setSummary(data);
    } catch (e) {
      setSummaryError(String(e));
    } finally {
      setSummaryLoading(false);
    }
  }, []);

  const mergeSubgraph = useCallback((data: SubgraphData) => {
    const G = graphRef.current;
    for (const node of data.nodes) {
      if (!G.hasNode(node.id)) {
        const pos = randomPosition();
        G.addNode(node.id, {
          label: nodeLabel(node.id),
          color: NODE_COLORS[node.kind] ?? "#64748b",
          size: degreeToSize(node.degree ?? 0),
          x: pos.x,
          y: pos.y,
          kind: node.kind,
          degree: node.degree ?? 0,
        });
      } else {
        // Update degree/size in case it grew
        G.setNodeAttribute(node.id, "degree", node.degree ?? 0);
        G.setNodeAttribute(node.id, "size", degreeToSize(node.degree ?? 0));
      }
    }
    for (const edge of data.edges) {
      const key = `${edge.source}--${edge.target}--${edge.kind}`;
      if (!G.hasEdge(key) && G.hasNode(edge.source) && G.hasNode(edge.target)) {
        G.addEdgeWithKey(key, edge.source, edge.target, {
          kind: edge.kind,
          color: EDGE_COLORS[edge.kind] ?? "#64748b40",
          size: 1,
        });
      }
    }
  }, []);

  const loadEgo = useCallback(
    async (ticker: string, depth = 2) => {
      const upper = ticker.toUpperCase();
      setEgoLoading(true);
      setEgoError(null);
      try {
        const data = await fetchGraphEgo(upper, depth);
        mergeSubgraph(data);
        setLoadedTickers((prev) => new Set([...prev, upper]));
      } catch (e) {
        setEgoError(String(e));
      } finally {
        setEgoLoading(false);
      }
    },
    [mergeSubgraph],
  );

  const resetGraph = useCallback(() => {
    graphRef.current = new MultiDirectedGraph();
    setSummary(null);
    setSummaryError(null);
    setEgoError(null);
    setLoadedTickers(new Set());
  }, []);

  return {
    graphRef,
    summary,
    summaryLoading,
    summaryError,
    egoLoading,
    egoError,
    loadedTickers,
    loadSummary,
    loadEgo,
    resetGraph,
  };
}
