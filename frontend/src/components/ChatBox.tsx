import { useEffect, useRef, useState } from "react";
import { useAppContext } from "../context/AppContext";
import type { AgentMode, MentionOption, TradeOrder } from "../types";
import { ChatMessage } from "./ChatMessage";
import { MentionPopover } from "./MentionPopover";
import { RunExplorerModal } from "./RunExplorerModal";
import { TradeTicket } from "./TradeTicket";

const AGENT_MODES: { value: AgentMode; label: string }[] = [
  { value: "quick", label: "Quick" },
  { value: "research", label: "Research" },
  { value: "portfolio", label: "Portfolio" },
  { value: "strategy", label: "Strategy" },
];

function statusLabel(value: "online" | "degraded" | "disconnected" | "unknown"): string {
  if (value === "online") return "Online";
  if (value === "degraded") return "Degraded";
  if (value === "disconnected") return "Disconnected";
  return "Unknown";
}

// Parse @mentions from text and return context_refs array
function extractContextRefs(text: string): string[] {
  const refs: string[] = [];
  const re = /@([A-Za-z]{1,10})\b/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const raw = match[1].toUpperCase();
    if (raw === "PORTFOLIO") {
      refs.push("user_portfolio");
    } else {
      refs.push(raw);
    }
  }
  return [...new Set(refs)];
}

// Find the mention query at the current cursor position
function getMentionQuery(value: string, cursorPos: number): string | null {
  const before = value.slice(0, cursorPos);
  const atIdx = before.lastIndexOf("@");
  if (atIdx === -1) return null;
  const segment = before.slice(atIdx + 1);
  // Only trigger if no whitespace between @ and cursor
  if (/\s/.test(segment)) return null;
  return segment;
}

export function ChatBox() {
  const { state, sendMessage, selectTicker, reloadPortfolio, addSystemMessage, setAgentMode } = useAppContext();
  const {
    chatMessages,
    chatStreaming,
    portfolio,
    agentMode,
    systemStatus,
    activeRunToolCalls,
    activeRunElapsedSeconds,
  } = state;

  const [text, setText] = useState("");
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [tradeToReview, setTradeToReview] = useState<TradeOrder | null>(null);
  const [exploringRunId, setExploringRunId] = useState<string | null>(null);

  const handleTradeSuccess = async (trade: TradeOrder, orderId: string) => {
    setTradeToReview(null);
    await addSystemMessage(
      `Trade Executed: ${trade.action} ${trade.qty} ${trade.ticker} (Order ID: ${orderId})`,
    );
    reloadPortfolio();
  };

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const portfolioSymbols = portfolio.map((p) => p.symbol);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  // Auto-resize textarea
  const autoResize = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    autoResize();
    const cursor = e.target.selectionStart ?? val.length;
    const query = getMentionQuery(val, cursor);
    setMentionQuery(query);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // If popover is open, let it handle Up/Down/Enter/Tab/Escape
    if (mentionQuery !== null && ["ArrowUp", "ArrowDown", "Tab", "Escape"].includes(e.key)) {
      e.preventDefault();
      return;
    }
    if (mentionQuery !== null && e.key === "Enter") {
      e.preventDefault();
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleMentionSelect = (option: MentionOption) => {
    const ta = textareaRef.current;
    if (!ta) return;

    const cursor = ta.selectionStart ?? text.length;
    const before = text.slice(0, cursor);
    const atIdx = before.lastIndexOf("@");
    const after = text.slice(cursor);

    const replacement = `@${option.value} `;
    const newText = text.slice(0, atIdx) + replacement + after;
    setText(newText);
    setMentionQuery(null);

    // Trigger ticker dashboard if it's a ticker mention
    if (option.type === "ticker") {
      selectTicker(option.value);
    }

    // Restore focus and move cursor after replacement
    requestAnimationFrame(() => {
      if (!ta) return;
      ta.focus();
      const newCursor = atIdx + replacement.length;
      ta.setSelectionRange(newCursor, newCursor);
      autoResize();
    });
  };

  const handleMentionClose = () => setMentionQuery(null);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || chatStreaming) return;
    const contextRefs = extractContextRefs(trimmed);
    const tickerRef = contextRefs.find((ref) => ref !== "user_portfolio");
    if (tickerRef) {
      selectTicker(tickerRef);
    }
    sendMessage(trimmed, contextRefs, agentMode);
    setText("");
    setMentionQuery(null);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    });
  };

  const handleRunInResearchMode = (prompt: string) => {
    const trimmed = prompt.trim();
    if (!trimmed || chatStreaming) return;
    const contextRefs = extractContextRefs(trimmed);
    sendMessage(trimmed, contextRefs, "research");
    setAgentMode("research");
  };

  return (
    <main className="pane-chat">
      <div className="chat-system-status" aria-live="polite">
        <span>System Status</span>
        <span className={`status-pill status-pill--${systemStatus.web}`}>Web: {statusLabel(systemStatus.web)}</span>
        <span className={`status-pill status-pill--${systemStatus.core}`}>Core: {statusLabel(systemStatus.core)}</span>
        <span className={`status-pill status-pill--${systemStatus.worker}`}>Worker: {statusLabel(systemStatus.worker)}</span>
      </div>
      {chatStreaming && (
        <div className="chat-run-progress" aria-live="polite">
          <span>Tool Calls: {activeRunToolCalls}</span>
          <span>Seconds Elapsed: {activeRunElapsedSeconds}</span>
        </div>
      )}

      {/* Message list */}
      <div className="chat-messages">
        {chatMessages.length === 0 && (
          <div className="chat-empty">
            <p>Ask me anything about your portfolio or a ticker.</p>
            <p style={{ marginTop: "8px", fontSize: "12px" }}>
              Tip: type <span className="mention-tag">@portfolio</span> or{" "}
              <span className="mention-tag">@AAPL</span> to include context.
            </p>
          </div>
        )}
        {chatMessages.map((msg, idx) => {
          const retryPrompt = msg.role === "assistant"
            ? (() => {
                for (let i = idx - 1; i >= 0; i -= 1) {
                  if (chatMessages[i]?.role === "user") {
                    return chatMessages[i].content;
                  }
                }
                return undefined;
              })()
            : undefined;

          return (
            <ChatMessage
              key={msg.id}
              message={msg}
              isStreaming={chatStreaming && idx === chatMessages.length - 1}
              onReviewTrade={setTradeToReview}
              onOpenRunExplorer={setExploringRunId}
              onRunInResearchMode={handleRunInResearchMode}
              retryPrompt={retryPrompt}
            />
          );
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* Mode selector */}
      <div className="mode-selector">
        {AGENT_MODES.map((m) => (
          <button
            key={m.value}
            className={`mode-selector-btn${agentMode === m.value ? " mode-selector-btn--active" : ""}`}
            onClick={() => setAgentMode(m.value)}
            disabled={chatStreaming}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Input area */}
      <div className="chat-input-area">
        <div style={{ position: "relative", flex: 1 }}>
          {mentionQuery !== null && (
            <MentionPopover
              query={mentionQuery}
              portfolioSymbols={portfolioSymbols}
              onSelect={handleMentionSelect}
              onClose={handleMentionClose}
            />
          )}
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            value={text}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder='Ask a question... (type @ to mention a ticker or portfolio)'
            rows={1}
            disabled={chatStreaming}
          />
        </div>
        <button
          className="btn-send"
          onClick={handleSend}
          disabled={!text.trim() || chatStreaming}
        >
          {chatStreaming ? "..." : "Send"}
        </button>
      </div>
      {tradeToReview && (
        <TradeTicket
          trade={tradeToReview}
          onClose={() => setTradeToReview(null)}
          onSuccess={handleTradeSuccess}
        />
      )}
      {exploringRunId && (
        <RunExplorerModal runId={exploringRunId} onClose={() => setExploringRunId(null)} />
      )}
    </main>
  );
}
