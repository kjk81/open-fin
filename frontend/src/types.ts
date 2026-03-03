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

export interface DashboardStockMetric {
  symbol: string;
  trailing_pe: number | null;
  agent_score: number;
}

export interface DashboardMetrics {
  best_pe: DashboardStockMetric[];
  best_agent_score: DashboardStockMetric[];
}

export interface TickerEventItem {
  title: string;
  url: string;
  snippet: string;
  provider: string;
  rank: number;
  occurred_at: string;
}

export interface SentimentSnapshot {
  overall_bias: "Bullish" | "Bearish" | "Neutral" | "Mixed";
  key_catalysts: string[];
  majority_opinion: string;
  reddit_summary: string;
  twitter_summary: string;
  confidence: "High" | "Medium" | "Low";
  searched_at: string;
}

export interface TickerEventsResponse {
  sentiment: SentimentSnapshot | null;
  events: TickerEventItem[];
}

export interface PortfolioPosition {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  synced_at: string | null;
}

export type BackendStatus = "connecting" | "running" | "migration_error" | "error";

export type TimelineItem =
  | { type: "text"; content: string; key: string }
  | { type: "step"; step: AgentStep; key: string }
  | { type: "tool_card"; card: ToolCardMessage; key: string };

export interface VerificationWarning {
  type: string;
  claim_key: string;
  source?: string;
  sources?: string[];
  min_value?: number;
  max_value?: number;
  spread_pct?: number;
}

export interface VerificationReport {
  status: "pass" | "warning" | "critical";
  warnings: VerificationWarning[];
  critical: VerificationWarning[];
}

export interface ConsentProposal {
  proposal_id: string;
  status: string;
  reason: string;
  tool_result_count: number;
  source_count: number;
  expires_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  runId?: string;
  toolEvents?: ToolEvent[];
  toolCards?: ToolCardMessage[];
  steps?: AgentStep[];
  timeline?: TimelineItem[];
  completionStatus?: AssistantCompletionStatus;
  sources?: SourceRef[];
  quickModeBlockedSearch?: boolean;
  verificationReport?: VerificationReport;
  consentProposal?: ConsentProposal;
}

export type AssistantCompletionStatus = "streaming" | "complete" | "incomplete";

export type AgentStepState = "running" | "done" | "warning" | "error";

export interface AgentStep {
  seq: number;
  stepId: string;
  message: string;
  state: AgentStepState;
  category: "tool" | "stage";
  tool?: string;
  durationMs?: number;
}

export interface AgentProgressEvent {
  seq: number;
  eventType: "step" | "status";
  state: AgentStepState;
  message: string;
  runId?: string;
  stepId?: string;
  category?: "tool" | "stage";
  tool?: string;
  durationMs?: number;
  phase?: string;
  verbose?: boolean;
  details?: Record<string, unknown>;
}

export interface CapabilitiesSnapshotEvent {
  seq?: number;
  runId?: string;
  phase?: string;
  capabilities: Record<string, unknown>;
}

export type SystemStatusLevel = "online" | "degraded" | "disconnected" | "unknown";

export interface SystemStatusSnapshot {
  web: SystemStatusLevel;
  core: SystemStatusLevel;
  worker: SystemStatusLevel;
  capabilities: Record<string, unknown>;
  updatedAt: number;
}

export interface ToolProvenance {
  source?: string;
  retrieved_at?: string;
  as_of?: string;
  identifier?: string;
}

export interface ToolQuality {
  warnings?: string[];
  completeness?: number;
}

export interface ToolEnvelopeSourceRef {
  url?: string;
  title?: string;
  fetched_at?: string;
}

export interface ToolResultEnvelope {
  data?: unknown;
  provenance?: ToolProvenance;
  quality?: ToolQuality;
  timing?: {
    tool_name?: string;
    started_at?: string;
    ended_at?: string;
    duration_ms?: number;
  };
  sources?: ToolEnvelopeSourceRef[];
  success?: boolean;
  error?: string | null;
  raw_ref?: {
    storage_type?: string;
    ref?: string;
  } | null;
}

export interface ToolCardMessage {
  id: string;
  seq: number;
  tool: string;
  stepId?: string;
  status: "done" | "error";
  durationMs?: number;
  args?: Record<string, unknown>;
  resultEnvelope?: ToolResultEnvelope;
}

export interface ToolEvent {
  seq?: number;
  tool: string;
  stepId?: string;
  status: "running" | "done" | "error";
  args?: Record<string, unknown>;
  durationMs?: number;
  resultEnvelope?: ToolResultEnvelope;
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
  subagent_fallback_order?: LlmProvider[];
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
  in_sector_count?: number;
  in_industry_count?: number;
  co_mention_count?: number;
  updated_at?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  weight?: number;
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
  sort_by?: "id" | "kind" | "degree" | "updated_at";
  sort_dir?: "asc" | "desc";
  min_degree?: number;
  offset?: number;
  limit?: number;
}

export interface TickerNote {
  id: number;
  ticker: string;
  content: string;
  created_at: string | null;
}

export interface PaginatedTickerNotes {
  total: number;
  items: TickerNote[];
}

export interface GraphConnectionNeighbor {
  name: string;
  kind: NodeKind;
  edge_kind: EdgeKind;
  weight: number;
}

export interface GraphConnectionsSummary {
  ticker: string;
  total_connections: number;
  by_kind: Partial<Record<EdgeKind, number>>;
  neighbors: GraphConnectionNeighbor[];
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

// ── Agent Modes + Analysis ──────────────────────────────────────────────────

export type AgentMode = "quick" | "research" | "portfolio" | "strategy";

export type AnalysisSectionName = "fundamentals" | "sentiment" | "technical";

export interface AnalysisSectionData {
  rating: string;
  content: string;
  source: string;
  loading: boolean;
}

export interface TickerAnalysis {
  sections: Partial<Record<AnalysisSectionName, AnalysisSectionData>>;
  overallRating: string | null;
  loading: boolean;
  error: string | null;
}

// ── Agent Terminal ────────────────────────────────────────────────────────────

export type TerminalLogLevel = "info" | "success" | "warn" | "error";

export type TerminalLogType =
  | "system"
  | "agent"
  | "step"
  | "status"
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
  detail?: string;
}

export interface AgentRunSummary {
  id: string;
  session_id: string;
  mode: AgentMode;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface AgentRunEvent {
  id: number;
  run_id: string;
  seq: number;
  type: string;
  payload_json: string;
  payload?: Record<string, unknown> | null;
  created_at: string | null;
}

export interface AgentRunEventsResponse {
  run_id: string;
  total: number;
  items: AgentRunEvent[];
}
