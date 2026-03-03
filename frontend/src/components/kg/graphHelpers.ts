import type { NodeKind, EdgeKind } from "../../types";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// ── Color maps ───────────────────────────────────────────────────────────────

export const NODE_COLORS: Record<NodeKind, string> = {
  ticker: "#3b82f6",
  sector: "#22c55e",
  industry: "#f59e0b",
};

export const NODE_COLORS_DIM = "#27272a";

export const EDGE_COLORS: Record<EdgeKind, string> = {
  IN_SECTOR: "#22c55e80",
  IN_INDUSTRY: "#f59e0b80",
  CO_MENTION: "#64748b60",
};

export function getGraphThemeColors() {
  return {
    background: cssVar("--kg-graph-bg", cssVar("--bg", "#18181b")),
    label: cssVar("--kg-graph-label", cssVar("--text", "#f4f4f5")),
    labelBg: cssVar("--kg-graph-label-bg", "rgba(9,9,11,0.78)"),
    nodeTicker: cssVar("--kg-node-ticker", cssVar("--accent", NODE_COLORS.ticker)),
    nodeSector: cssVar("--kg-node-sector", cssVar("--green", NODE_COLORS.sector)),
    nodeIndustry: cssVar("--kg-node-industry", cssVar("--yellow", NODE_COLORS.industry)),
    nodeDim: cssVar("--kg-node-dim", cssVar("--surface", NODE_COLORS_DIM)),
    edgeSector: cssVar("--kg-edge-sector", EDGE_COLORS.IN_SECTOR),
    edgeIndustry: cssVar("--kg-edge-industry", EDGE_COLORS.IN_INDUSTRY),
    edgeCoMention: cssVar("--kg-edge-co", EDGE_COLORS.CO_MENTION),
  };
}

// ── Node sizing ──────────────────────────────────────────────────────────────

/** Map a degree value to a Sigma node size (px). */
export function degreeToSize(degree: number): number {
  if (degree <= 1) return 5;
  if (degree <= 5) return 7;
  if (degree <= 20) return 10;
  if (degree <= 100) return 14;
  return 18;
}

// ── Initial position jitter ──────────────────────────────────────────────────

/** Random position in a unit circle for stable layout seeding. */
export function randomPosition(): { x: number; y: number } {
  const angle = Math.random() * 2 * Math.PI;
  const r = Math.random();
  return { x: r * Math.cos(angle), y: r * Math.sin(angle) };
}

export function seededPosition(id: string): { x: number; y: number } {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  const angle = ((hash % 360) * Math.PI) / 180;
  const radius = 0.2 + ((hash % 1000) / 1000) * 0.9;
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

// ── Label helpers ────────────────────────────────────────────────────────────

/** Human-readable display label for a graph node id. */
export function nodeLabel(id: string): string {
  if (id.startsWith("sector:"))   return id.slice(7);
  if (id.startsWith("industry:")) return id.slice(9);
  return id;
}

// ── LOD thresholds ───────────────────────────────────────────────────────────

/** Camera ratio above which CO_MENTION edges are hidden. */
export const LOD_EDGE_HIDE_RATIO = 1.8;

/** Minimum rendered node size (px on screen) for a label to be shown. */
export const LOD_LABEL_SIZE_THRESHOLD = 10;
