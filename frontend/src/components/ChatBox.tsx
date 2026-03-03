import { useEffect, useRef, useState } from "react";
import { useAppContext } from "../context/AppContext";
import type { AgentMode, MentionOption, TradeOrder } from "../types";
import { ChatMessage } from "./ChatMessage";
import { MentionPopover } from "./MentionPopover";
import { TradeTicket } from "./TradeTicket";

const AGENT_MODES: { value: AgentMode; label: string }[] = [
  { value: "genie", label: "Genie" },
  { value: "fundamentals", label: "Fundamentals" },
  { value: "sentiment", label: "Sentiment" },
  { value: "technical", label: "Technical" },
];

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
  const { chatMessages, chatStreaming, portfolio, agentMode } = state;

  const [text, setText] = useState("");
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [tradeToReview, setTradeToReview] = useState<TradeOrder | null>(null);

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

  return (
    <main className="pane-chat">
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
        {chatMessages.map((msg, idx) => (
          <ChatMessage
            key={msg.id}
            message={msg}
            isStreaming={chatStreaming && idx === chatMessages.length - 1}
            onReviewTrade={setTradeToReview}
          />
        ))}
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
    </main>
  );
}
