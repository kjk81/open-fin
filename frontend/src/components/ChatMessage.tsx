import { useAppContext } from "../context/AppContext";
import type { ChatMessage as ChatMessageType, TradeOrder } from "../types";

// Matches [TRADE: {...}] blocks emitted by the LLM
const TRADE_RE = /\[TRADE:\s*(\{[^}]*\})\]/g;

interface Props {
  message: ChatMessageType;
  isStreaming: boolean;
  onReviewTrade: (trade: TradeOrder) => void;
}

export function ChatMessage({ message, isStreaming, onReviewTrade }: Props) {
  const { selectTicker } = useAppContext();
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  const parts = renderContent(message.content, selectTicker, onReviewTrade);

  return (
    <div className={`chat-message chat-message--${isUser ? "user" : isSystem ? "system" : "assistant"}`}>
      <div className="chat-bubble">
        {parts}
        {isStreaming && !isUser && !isSystem && <span className="typing-cursor" />}
      </div>
      <div className="chat-meta">
        {isUser ? "You" : isSystem ? "System" : "Open-Fin AI"}
      </div>
    </div>
  );
}

function parseMentions(
  text: string,
  selectTicker: (s: string) => void,
  keyStart: number,
): { nodes: JSX.Element[]; nextKey: number } {
  const nodes: JSX.Element[] = [];
  let last = 0;
  let key = keyStart;
  const re = /@([A-Za-z]{1,10})\b/g;
  let match: RegExpExecArray | null;

  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      const markdown = parseMarkdownInline(text.slice(last, match.index), key);
      nodes.push(...markdown.nodes);
      key = markdown.nextKey;
    }

    const raw = match[1].toUpperCase();
    const isPortfolio = raw === "PORTFOLIO";

    if (isPortfolio) {
      nodes.push(
        <span key={key++} className="mention-tag">
          @portfolio
        </span>,
      );
    } else {
      nodes.push(
        <button
          key={key++}
          className="mention-tag mention-tag--clickable"
          onClick={() => selectTicker(raw)}
          title={`View ${raw}`}
        >
          @{raw}
        </button>,
      );
    }

    last = match.index + match[0].length;
  }

  if (last < text.length) {
    const markdown = parseMarkdownInline(text.slice(last), key);
    nodes.push(...markdown.nodes);
    key = markdown.nextKey;
  }

  return { nodes, nextKey: key };
}

function parseMarkdownInline(
  text: string,
  keyStart: number,
): { nodes: JSX.Element[]; nextKey: number } {
  const nodes: JSX.Element[] = [];
  const tokenRe = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  let key = keyStart;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRe.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(<span key={key++}>{text.slice(last, match.index)}</span>);
    }

    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*") && token.endsWith("*")) {
      nodes.push(<em key={key++}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a key={key++} href={linkMatch[2]} target="_blank" rel="noreferrer">
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(<span key={key++}>{token}</span>);
      }
    }

    last = match.index + token.length;
  }

  if (last < text.length) {
    nodes.push(<span key={key++}>{text.slice(last)}</span>);
  }

  return { nodes, nextKey: key };
}

function renderContent(
  text: string,
  selectTicker: (s: string) => void,
  onReviewTrade: (trade: TradeOrder) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let key = 0;
  let last = 0;

  TRADE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = TRADE_RE.exec(text)) !== null) {
    // Process text before this trade block through the mention parser
    if (match.index > last) {
      const segment = text.slice(last, match.index);
      const { nodes, nextKey } = parseMentions(segment, selectTicker, key);
      parts.push(...nodes);
      key = nextKey;
    }

    // Try to parse the trade JSON
    const jsonStr = match[1];
    let trade: TradeOrder | null = null;
    try {
      const parsed = JSON.parse(jsonStr) as Record<string, unknown>;
      const action = typeof parsed.action === "string" ? parsed.action.toUpperCase() : "";
      const ticker = typeof parsed.ticker === "string" ? parsed.ticker.toUpperCase() : "";
      const qtyRaw = parsed.qty;
      const qty = typeof qtyRaw === "number" ? qtyRaw : typeof qtyRaw === "string" ? Number(qtyRaw) : NaN;
      if (
        (action === "BUY" || action === "SELL") &&
        /^[A-Z]{1,10}$/.test(ticker) &&
        Number.isFinite(qty) &&
        qty > 0
      ) {
        trade = { action: action as "BUY" | "SELL", ticker, qty: Math.floor(qty) };
      }
    } catch {
      // malformed JSON — fall through to raw text
    }

    if (trade) {
      const t = trade;
      parts.push(
        <button
          key={key++}
          className="trade-review-btn"
          onClick={() => onReviewTrade(t)}
        >
          Review Trade: {t.action} {t.qty} {t.ticker}
        </button>,
      );
    } else {
      // Fallback: render the raw block as text
      parts.push(<span key={key++}>{match[0]}</span>);
    }

    last = match.index + match[0].length;
  }

  // Process any remaining text after the last trade block
  if (last < text.length) {
    const segment = text.slice(last);
    const { nodes, nextKey } = parseMentions(segment, selectTicker, key);
    parts.push(...nodes);
    key = nextKey;
  }

  // If no trade blocks at all, run the full text through mention parsing
  if (parts.length === 0) {
    const { nodes } = parseMentions(text, selectTicker, key);
    parts.push(...nodes);
  }

  return parts;
}
