import type {
  TickerInfo,
  PortfolioPosition,
  TradeOrder,
  TradeResult,
  LlmSettings,
  LlmProvider,
} from "./types";

const API = "http://localhost:8000";

export async function fetchHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchTicker(symbol: string): Promise<TickerInfo> {
  const res = await fetch(`${API}/api/ticker/${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error(`Ticker fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchPortfolio(): Promise<PortfolioPosition[]> {
  const res = await fetch(`${API}/api/portfolio`);
  if (!res.ok) throw new Error(`Portfolio fetch failed: ${res.status}`);
  return res.json();
}

export async function syncPortfolio(): Promise<void> {
  const res = await fetch(`${API}/api/sync-portfolio`, { method: "POST" });
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
}

export async function executeTrade(order: TradeOrder): Promise<TradeResult> {
  const res = await fetch(`${API}/api/execute_trade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(order),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Trade failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchLlmSettings(): Promise<LlmSettings> {
  const res = await fetch(`${API}/api/llm/settings`);
  if (!res.ok) throw new Error(`LLM settings fetch failed: ${res.status}`);
  return res.json();
}

export async function updateLlmSettings(
  mode: "cloud" | "ollama",
  fallbackOrder: LlmProvider[],
): Promise<LlmSettings> {
  const res = await fetch(`${API}/api/llm/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, fallback_order: fallbackOrder }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `LLM settings update failed: ${res.status}`);
  }
  return res.json();
}

export async function postSystemEvent(sessionId: string, content: string): Promise<void> {
  const res = await fetch(`${API}/api/chat/system_event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, content }),
  });
  if (!res.ok) throw new Error(`System event failed: ${res.status}`);
}

export async function streamChat(
  message: string,
  sessionId: string,
  contextRefs: string[],
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, context_refs: contextRefs }),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    onError(String(e));
    return;
  }

  if (!res.body) {
    onError("No response body");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE lines are separated by "\n\n"
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        const match = part.match(/^data:\s*(.+)$/m);
        if (!match) continue;
        try {
          const event = JSON.parse(match[1]);
          if (event.type === "token") onToken(event.content);
          else if (event.type === "done") onDone();
          else if (event.type === "error") onError(event.content ?? "Unknown error");
        } catch {
          // ignore malformed JSON
        }
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") onError(String(e));
  } finally {
    reader.releaseLock();
  }
}
