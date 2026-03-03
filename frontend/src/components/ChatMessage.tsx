import React, { type AnchorHTMLAttributes, type ClassAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAppContext } from "../context/AppContext";
import type {
  AgentStep,
  ChatMessage as ChatMessageType,
  SourceRef,
  ToolEvent,
  TradeOrder,
} from "../types";

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
  const isAssistant = !isUser && !isSystem;

  let contentArea: React.ReactNode;
  if (message.timeline && message.timeline.length > 0) {
    // Group contiguous sequences of 'text' together and contiguous 'step' together
    const groupedItems: Array<{ type: "text", content: string, key: string } | { type: "step", steps: AgentStep[], key: string }> = [];

    for (const item of message.timeline) {
      if (groupedItems.length === 0) {
        if (item.type === "text") {
          groupedItems.push({ ...item });
        } else {
          groupedItems.push({ type: "step", steps: [item.step], key: item.key });
        }
      } else {
        const lastGroup = groupedItems[groupedItems.length - 1];
        if (item.type === "text") {
          if (lastGroup.type === "text") {
            lastGroup.content += item.content;
          } else {
            groupedItems.push({ ...item });
          }
        } else {
          if (lastGroup.type === "step") {
            lastGroup.steps.push(item.step);
          } else {
            groupedItems.push({ type: "step", steps: [item.step], key: item.key });
          }
        }
      }
    }

    contentArea = (
      <>
        {groupedItems.map((group) => {
          if (group.type === "step") {
            return (
              <div key={group.key} className="chat-steps">
                {group.steps.map((step) => (
                  <StepRow key={`${step.stepId}-${step.state}`} step={step} />
                ))}
              </div>
            );
          } else {
            const parts = renderContent(group.content, selectTicker, onReviewTrade);
            return <React.Fragment key={group.key}>{parts}</React.Fragment>;
          }
        })}
      </>
    );
  } else {
    // Fallback for older messages
    const parts = renderContent(message.content, selectTicker, onReviewTrade);
    contentArea = (
      <>
        {isAssistant && message.steps && message.steps.length > 0 && (
          <div className="chat-steps" aria-label="Agent execution steps">
            {message.steps.map((step, index) => (
              <StepRow key={`${step.stepId}-${index}`} step={step} />
            ))}
          </div>
        )}
        {parts}
      </>
    );
  }

  return (
    <div className={`chat-message chat-message--${isUser ? "user" : isSystem ? "system" : "assistant"}`}>
      <div className="chat-bubble">
        {isAssistant && (!message.steps || message.steps.length === 0) && message.toolEvents && message.toolEvents.length > 0 && (
          <div className="tool-chips">
            {message.toolEvents.map((e, i) => (
              <ToolChip key={`${e.tool}-${i}`} event={e} />
            ))}
          </div>
        )}
        {contentArea}
        {isStreaming && isAssistant && <span className="typing-cursor" />}
        {isAssistant && message.completionStatus === "incomplete" && (
          <div className="chat-incomplete">Response incomplete — request timed out or failed.</div>
        )}
        {isAssistant && message.sources && message.sources.length > 0 && (
          <CitationFooter sources={message.sources} />
        )}
      </div>
      <div className="chat-meta">
        {isUser ? "You" : isSystem ? "System" : "Finneas"}
      </div>
    </div>
  );
}

function ToolChip({ event }: { event: ToolEvent }) {
  const statusClass =
    event.status === "running"
      ? "tool-chip--running"
      : event.status === "error"
        ? "tool-chip--error"
        : "tool-chip--done";

  const label =
    event.status === "running"
      ? event.tool
      : event.durationMs !== undefined
        ? `${event.tool} ${event.durationMs}ms`
        : event.tool;

  return <span className={`tool-chip ${statusClass}`}>{label}</span>;
}

function CitationFooter({ sources }: { sources: SourceRef[] }) {
  return (
    <div className="chat-citations">
      {sources.map((src, i) => (
        <a
          key={src.url}
          className="citation-link"
          href={src.url}
          target="_blank"
          rel="noreferrer"
          title={src.url}
        >
          [{i + 1}] {src.title}
        </a>
      ))}
    </div>
  );
}

function StepRow({ step }: { step: AgentStep }) {
  const isDone = step.state === "done";
  const isRunning = step.state === "running";

  // Inline icon: ✓ done, ✕ error, spinner for running
  const indicatorClass = isRunning
    ? "chat-step-indicator chat-step-indicator--running"
    : isDone
      ? "chat-step-indicator chat-step-indicator--done"
      : "chat-step-indicator chat-step-indicator--error";

  // Append duration to tool steps that completed
  const durationSuffix =
    isDone && step.category === "tool" && step.durationMs != null
      ? ` (${step.durationMs}ms)`
      : "";

  return (
    <div className="chat-step-row">
      <span className={indicatorClass} aria-hidden="true" />
      <span className="chat-step-text">
        {step.message}{durationSuffix}
      </span>
    </div>
  );
}

/** Convert @AAPL mentions into markdown links with a custom mention:// scheme */
function preprocessMentions(text: string): string {
  return text.replace(/@([A-Za-z]{1,10})\b/g, (_, sym: string) => {
    const upper = sym.toUpperCase();
    return `[@${upper}](mention://${upper})`;
  });
}

/** Build the custom components map for ReactMarkdown */
function makeComponents(selectTicker: (s: string) => void) {
  return {
    // Intercept mention:// links to render as clickable mention tags
    a: ({ href, children, ...props }: ClassAttributes<HTMLAnchorElement> & AnchorHTMLAttributes<HTMLAnchorElement>) => {
      if (href?.startsWith("mention://")) {
        const sym = href.slice("mention://".length);
        if (sym === "PORTFOLIO") {
          return <span className="mention-tag">@portfolio</span>;
        }
        return (
          <button
            className="mention-tag mention-tag--clickable"
            onClick={() => selectTicker(sym)}
            title={`View ${sym}`}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" {...props}>
          {children}
        </a>
      );
    },
    // Distinguish inline code from fenced code blocks
    code: ({ className, children, ...props }: React.HTMLAttributes<HTMLElement> & { className?: string }) => {
      const isBlock = Boolean(className);
      if (isBlock) {
        return <pre><code className={className} {...props}>{children}</code></pre>;
      }
      return <code className={className} {...props}>{children}</code>;
    },
  };
}

function MarkdownSegment({
  text,
  selectTicker,
  segKey,
}: {
  text: string;
  selectTicker: (s: string) => void;
  segKey: number;
}) {
  const components = makeComponents(selectTicker);
  return (
    <ReactMarkdown
      key={segKey}
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => url}
      components={components}
    >
      {preprocessMentions(text)}
    </ReactMarkdown>
  );
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
    if (match.index > last) {
      const segment = text.slice(last, match.index);
      parts.push(<MarkdownSegment key={key++} segKey={key} text={segment} selectTicker={selectTicker} />);
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
      parts.push(<span key={key++}>{match[0]}</span>);
    }

    last = match.index + match[0].length;
  }

  if (last < text.length) {
    const segment = text.slice(last);
    parts.push(<MarkdownSegment key={key++} segKey={key} text={segment} selectTicker={selectTicker} />);
  }

  // Only add the fallback segment when there is actual text to render.
  // An empty string produces a phantom <p> tag that pushes layout around
  // while steps are rendering before the first tokens arrive.
  if (parts.length === 0 && text.trim()) {
    parts.push(<MarkdownSegment key={key++} segKey={key} text={text} selectTicker={selectTicker} />);
  }

  return parts;
}
