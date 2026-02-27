import type { NodeKind, EdgeKind } from "../../types";

// ── Color maps ───────────────────────────────────────────────────────────────

export const NODE_COLORS: Record<NodeKind, string> = {
  ticker:   "#3b82f6",
  sector:   "#22c55e",
  industry: "#f59e0b",
};

export const NODE_COLORS_DIM = "#27272a";

export const EDGE_COLORS: Record<EdgeKind, string> = {
  IN_SECTOR:   "#22c55e80",
  IN_INDUSTRY: "#f59e0b80",
  CO_MENTION:  "#64748b60",
};

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
