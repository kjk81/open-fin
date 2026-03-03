import React, { useCallback, useMemo, useState, type AnchorHTMLAttributes, type ClassAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAppContext } from "../context/AppContext";
import { ToolCard } from "./ToolCard";
import { VerificationBadge } from "./VerificationBadge";
import { SaveToLibraryButton } from "./SaveToLibraryButton";
import type {
  AgentStep,
  ChatMessage as ChatMessageType,
  SourceRef,
  ToolCardMessage,
  ToolEvent,
  TradeOrder,
} from "../types";

// Matches [TRADE: {...}] blocks emitted by the LLM
const TRADE_RE = /\[TRADE:\s*(\{[^}]*\})\]/g;

interface Props {
  message: ChatMessageType;
  isStreaming: boolean;
  onReviewTrade: (trade: TradeOrder) => void;
  onOpenRunExplorer?: (runId: string) => void;
  onRunInResearchMode?: (prompt: string) => void;
  retryPrompt?: string;
}

export function ChatMessage({
  message,
  isStreaming,
  onReviewTrade,
  onOpenRunExplorer,
  onRunInResearchMode,
  retryPrompt,
}: Props) {
  const { selectTicker } = useAppContext();
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const isAssistant = !isUser && !isSystem;
  const [highlightedCitation, setHighlightedCitation] = useState<number | null>(null);

  const cardsByCitation = useMemo(() => {
    const sorted = [...(message.toolCards ?? [])].sort((a, b) => a.seq - b.seq);
    return sorted;
  }, [message.toolCards]);

  const anchorIdForCitation = useCallback((index: number) => {
    return `tool-card-anchor-${message.id}-${index}`;
  }, [message.id]);

  const handleCitationClick = useCallback((citationIndex: number) => {
    const anchorId = anchorIdForCitation(citationIndex);
    const node = document.getElementById(anchorId);
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightedCitation(citationIndex);
      window.setTimeout(() => setHighlightedCitation((cur) => (cur === citationIndex ? null : cur)), 1400);
    }
  }, [anchorIdForCitation]);

  const toolCardIndexById = useMemo(() => {
    const map = new Map<string, number>();
    cardsByCitation.forEach((card, idx) => {
      map.set(card.id, idx + 1);
    });
    return map;
  }, [cardsByCitation]);

  let contentArea: React.ReactNode;
  if (message.timeline && message.timeline.length > 0) {
    const groupedItems: Array<
      { type: "text"; content: string; key: string }
      | { type: "step"; steps: AgentStep[]; key: string }
      | { type: "tool_card"; card: ToolCardMessage; key: string }
    > = [];

    for (const item of message.timeline) {
      const lastGroup = groupedItems[groupedItems.length - 1];
      if (!lastGroup) {
        if (item.type === "step") {
          groupedItems.push({ type: "step", steps: [item.step], key: item.key });
        } else if (item.type === "text") {
          groupedItems.push({ ...item });
        } else {
          groupedItems.push({ ...item });
        }
        continue;
      }

      if (item.type === "text" && lastGroup.type === "text") {
        lastGroup.content += item.content;
      } else if (item.type === "step" && lastGroup.type === "step") {
        lastGroup.steps.push(item.step);
      } else if (item.type === "step") {
        groupedItems.push({ type: "step", steps: [item.step], key: item.key });
      } else {
        groupedItems.push({ ...item });
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
          } else if (group.type === "tool_card") {
            const citationIndex = toolCardIndexById.get(group.card.id) ?? 0;
            if (citationIndex <= 0) return null;
            return (
              <ToolCard
                key={group.key}
                card={group.card}
                citationIndex={citationIndex}
                anchorId={anchorIdForCitation(citationIndex)}
                highlighted={highlightedCitation === citationIndex}
              />
            );
          } else {
            const parts = renderContent(group.content, selectTicker, onReviewTrade, handleCitationClick);
            return <React.Fragment key={group.key}>{parts}</React.Fragment>;
          }
        })}
      </>
    );
  } else {
    // Fallback for older messages
    const parts = renderContent(message.content, selectTicker, onReviewTrade, handleCitationClick);
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
        {cardsByCitation.length > 0 && (
          <div className="tool-cards">
            {cardsByCitation.map((card, idx) => (
              <ToolCard
                key={card.id}
                card={card}
                citationIndex={idx + 1}
                anchorId={anchorIdForCitation(idx + 1)}
                highlighted={highlightedCitation === idx + 1}
              />
            ))}
          </div>
        )}
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
        {isAssistant && message.verificationReport && message.verificationReport.status !== "pass" && (
          <VerificationBadge report={message.verificationReport} />
        )}
        {isAssistant && message.sources && message.sources.length > 0 && (
          <CitationFooter sources={message.sources} />
        )}
        {isAssistant && (message.runId || message.completionStatus === "complete") && (
          <div className="chat-run-link-row">
            {message.runId && (
              <button className="btn-ghost chat-run-link" onClick={() => onOpenRunExplorer?.(message.runId!)}>
                Run Explorer
              </button>
            )}
            <SaveToLibraryButton message={message} />
          </div>
        )}
        {isAssistant && message.quickModeBlockedSearch && retryPrompt && (
          <div className="chat-run-link-row">
            <button
              className="btn-ghost chat-run-link"
              onClick={() => onRunInResearchMode?.(retryPrompt)}
            >
              Run again in Research Mode
            </button>
          </div>
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
  const isWarning = step.state === "warning";

  // Inline icon: ✓ done, ✕ error, spinner for running
  const indicatorClass = isRunning
    ? "chat-step-indicator chat-step-indicator--running"
    : isWarning
      ? "chat-step-indicator chat-step-indicator--warning"
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

function preprocessCitations(text: string): string {
  const withBracketMarkers = text.replace(/\[(\d+)\]/g, (_m, rawNum: string) => {
    return `[${rawNum}](citation://${rawNum})`;
  });
  return withBracketMarkers.replace(/\^(\d+)\b/g, (_m, rawNum: string) => {
    return `[^${rawNum}](citation://${rawNum})`;
  });
}

/** Build the custom components map for ReactMarkdown */
function makeComponents(selectTicker: (s: string) => void, onCitationClick: (citationIndex: number) => void) {
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
      if (href?.startsWith("citation://")) {
        const raw = href.slice("citation://".length);
        const citationIndex = Number(raw);
        return (
          <button
            className="citation-marker"
            onClick={() => {
              if (Number.isFinite(citationIndex) && citationIndex > 0) {
                onCitationClick(citationIndex);
              }
            }}
            title={`Open tool card [${raw}]`}
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
  onCitationClick,
  segKey,
}: {
  text: string;
  selectTicker: (s: string) => void;
  onCitationClick: (citationIndex: number) => void;
  segKey: number;
}) {
  const components = makeComponents(selectTicker, onCitationClick);
  return (
    <ReactMarkdown
      key={segKey}
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => url}
      components={components}
    >
      {preprocessCitations(preprocessMentions(text))}
    </ReactMarkdown>
  );
}

function renderContent(
  text: string,
  selectTicker: (s: string) => void,
  onReviewTrade: (trade: TradeOrder) => void,
  onCitationClick: (citationIndex: number) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let key = 0;
  let last = 0;

  TRADE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = TRADE_RE.exec(text)) !== null) {
    if (match.index > last) {
      const segment = text.slice(last, match.index);
      parts.push(
        <MarkdownSegment
          key={key++}
          segKey={key}
          text={segment}
          selectTicker={selectTicker}
          onCitationClick={onCitationClick}
        />,
      );
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
    parts.push(
      <MarkdownSegment
        key={key++}
        segKey={key}
        text={segment}
        selectTicker={selectTicker}
        onCitationClick={onCitationClick}
      />,
    );
  }

  // Only add the fallback segment when there is actual text to render.
  // An empty string produces a phantom <p> tag that pushes layout around
  // while steps are rendering before the first tokens arrive.
  if (parts.length === 0 && text.trim()) {
    parts.push(
      <MarkdownSegment
        key={key++}
        segKey={key}
        text={text}
        selectTicker={selectTicker}
        onCitationClick={onCitationClick}
      />,
    );
  }

  return parts;
}
