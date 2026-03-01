export interface TickerInfo {
  symbol: string;
  name: string | null;
  price: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  sector: string | null;
  industry: string | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  dividend_yield: number | null;
  beta: number | null;
}

export interface PortfolioPosition {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  synced_at: string | null;
}

export type BackendStatus = "connecting" | "running" | "error";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  toolEvents?: ToolEvent[];
  sources?: SourceRef[];
}

export interface ToolEvent {
  tool: string;
  status: "running" | "done" | "error";
  args?: Record<string, unknown>;
  durationMs?: number;
}

export interface SourceRef {
  url: string;
  title: string;
}

export interface MentionOption {
  type: "portfolio" | "ticker";
  label: string;
  value: string;
}

export interface TradeOrder {
  action: "BUY" | "SELL";
  ticker: string;
  qty: number;
}

export interface TradeResult {
  success: boolean;
  order_id: string;
  symbol: string;
  qty: number;
  side: string;
  status: string;
}

export type LlmProvider =
  | "openrouter"
  | "gemini"
  | "openai"
  | "groq"
  | "huggingface"
  | "ollama";

export interface WatchlistItem {
  id: number;
  ticker: string;
  added_at: string;
}

export interface LlmSettings {
  mode: "cloud" | "ollama";
  providers: LlmProvider[];
  fallback_order: LlmProvider[];
  /** Resolved provider/model for each role (informational, returned by backend). */
  agent_provider?: string;
  agent_model?: string;
  subagent_provider?: string;
  subagent_model?: string;
}

// ── Knowledge Graph ─────────────────────────────────────────────────────────

export type NodeKind = "ticker" | "sector" | "industry";
export type EdgeKind = "IN_SECTOR" | "IN_INDUSTRY" | "CO_MENTION";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  degree: number;
  updated_at?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface SubgraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Community {
  id: number;
  size: number;
  representative: string;
  members: string[];
}

export interface GraphSummary {
  node_count: number;
  edge_count: number;
  communities: Community[];
}

export interface PaginatedNodes {
  total: number;
  items: GraphNode[];
}

export interface PaginatedEdges {
  total: number;
  items: GraphEdge[];
}

export interface NodeQueryParams {
  kind?: NodeKind;
  search?: string;
  offset?: number;
  limit?: number;
}

export interface EdgeQueryParams {
  kind?: EdgeKind;
  source?: string;
  offset?: number;
  limit?: number;
}

export interface Loadout {
  id: number;
  ticker: string;
  strategy_name: string;
  schedule: string;
  is_active: boolean;
  parameters: Record<string, unknown>;
  max_qty: number;
  dry_run: boolean;
  created_at: string;
  updated_at: string;
}

export interface LoadoutCreate {
  ticker: string;
  strategy_name: string;
  schedule: string;
  parameters?: Record<string, unknown>;
  max_qty?: number;
  dry_run?: boolean;
}

export interface LoadoutUpdate {
  ticker?: string;
  strategy_name?: string;
  schedule?: string;
  is_active?: boolean;
  parameters?: Record<string, unknown>;
  max_qty?: number;
  dry_run?: boolean;
}

export interface LoadoutExecution {
  id: number;
  loadout_id: number;
  timestamp: string;
  action: "BUY" | "SELL" | "HOLD";
  ticker: string;
  quantity: number;
  confidence: number;
  status: "pending" | "filled" | "failed" | "dry_run" | string;
  dry_run: boolean;
  error_trace: string | null;
  order_id: string | null;
}

export interface PaginatedExecutions {
  total: number;
  items: LoadoutExecution[];
}

export interface WorkerStatusInfo {
  online: boolean;
  status: string;
  stale: boolean;
  last_heartbeat: string | null;
  worker_id: string | null;
  pid?: number;
}

export interface StrategyInfo {
  name: string;
}

// ── Settings ────────────────────────────────────────────────────────────────

export interface SettingSchema {
  key: string;
  label: string;
  description: string;
  type: "string" | "secret" | "number" | "select";
  category: string;
  options?: string[];
}

export interface SettingValue {
  is_set: boolean;
  preview: string;
  value: string;
}

export type SettingsValues = Record<string, SettingValue>;

// ── Agent Terminal ────────────────────────────────────────────────────────────

export type TerminalLogLevel = "info" | "success" | "warn" | "error";

export type TerminalLogType =
  | "system"
  | "agent"
  | "tool_start"
  | "tool_end"
  | "sources"
  | "kg_update"
  | "error"
  | "done";

export interface TerminalLogEntry {
  id: number;
  timestamp: number;
  type: TerminalLogType;
  level: TerminalLogLevel;
  message: string;
}
