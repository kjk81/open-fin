import { useRef, useEffect, useCallback, memo } from "react";
import { useAppContext } from "../context/AppContext";
import type { TerminalLogEntry } from "../types";

// ── Tag labels ────────────────────────────────────────────────────────────────

const LOG_PREFIX: Record<TerminalLogEntry["type"], string> = {
  system: "[SYSTEM]",
  agent: "[AGENT]",
  tool_start: "[TOOL]",
  tool_end: "[TOOL]",
  sources: "[SOURCES]",
  kg_update: "[KG]",
  error: "[ERROR]",
  done: "[SYSTEM]",
};

// ── Tag level CSS modifier classes ────────────────────────────────────────────

const LEVEL_CLASSES: Record<TerminalLogEntry["level"], string> = {
  info: "terminal-log-prefix--info",
  success: "terminal-log-prefix--success",
  warn: "terminal-log-prefix--warn",
  error: "terminal-log-prefix--error",
};

// ── Single log line ───────────────────────────────────────────────────────────

interface LogLineProps {
  entry: TerminalLogEntry;
}

const LogLine = memo(function LogLine({ entry }: LogLineProps) {
  const ts = new Date(entry.timestamp).toTimeString().slice(0, 8);
  const prefix = LOG_PREFIX[entry.type];
  const levelClass = LEVEL_CLASSES[entry.level];

  return (
    <div className="terminal-log-line">
      <span className="terminal-log-time">[{ts}]</span>
      <span className={`terminal-log-prefix ${levelClass}`}>{prefix}</span>
      <span className="terminal-log-msg">{entry.message}</span>
    </div>
  );
});

// ── Main component ────────────────────────────────────────────────────────────

export const AgentTerminal = memo(function AgentTerminal() {
  const { state, clearTerminalLogs, toggleTerminal } = useAppContext();
  const { terminalLogs } = state;
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever new logs arrive
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [terminalLogs.length]);

  const handleClear = useCallback(() => {
    clearTerminalLogs();
  }, [clearTerminalLogs]);

  const handleClose = useCallback(() => {
    toggleTerminal();
  }, [toggleTerminal]);

  return (
    <div className="terminal-panel" style={{ height: "220px" }}>
      {/* Header bar */}
      <div className="terminal-header">
        <div className="terminal-header-left">
          <span className="terminal-title">Terminal</span>
          <span className="terminal-entry-count">
            {terminalLogs.length} {terminalLogs.length === 1 ? "entry" : "entries"}
          </span>
        </div>
        <div className="terminal-header-actions">
          <button onClick={handleClear} className="terminal-btn" title="Clear terminal">
            clear
          </button>
          <button onClick={handleClose} className="terminal-btn" title="Close terminal (Ctrl+`)">
            ×
          </button>
        </div>
      </div>

      {/* Log area */}
      <div
        ref={scrollRef}
        className="terminal-log-area"
        style={{ scrollbarWidth: "thin", scrollbarColor: "#3f3f46 transparent" }}
      >
        {terminalLogs.length === 0 ? (
          <span className="terminal-empty">No events yet. Send a chat message to see agent logs.</span>
        ) : (
          terminalLogs.map((entry) => <LogLine key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
});
