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

export interface LlmSettings {
  mode: "cloud" | "ollama";
  providers: LlmProvider[];
  fallback_order: LlmProvider[];
}
