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

// ── Tag color classes (Tailwind) ──────────────────────────────────────────────

const LEVEL_CLASSES: Record<TerminalLogEntry["level"], string> = {
  info: "text-blue-400",
  success: "text-green-400",
  warn: "text-yellow-400",
  error: "text-red-400",
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
    <div className="flex gap-2 leading-5 py-px">
      <span className="text-zinc-600 shrink-0">[{ts}]</span>
      <span className={`${levelClass} shrink-0 font-semibold`}>{prefix}</span>
      <span className="text-zinc-300 break-all">{entry.message}</span>
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
    <div className="flex flex-col border-t border-zinc-700 bg-zinc-950" style={{ height: "220px" }}>
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1 bg-zinc-900 border-b border-zinc-700 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-zinc-400 text-xs font-mono font-semibold tracking-wider uppercase">
            Terminal
          </span>
          <span className="text-zinc-600 text-xs font-mono">
            {terminalLogs.length} {terminalLogs.length === 1 ? "entry" : "entries"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleClear}
            className="text-zinc-500 hover:text-zinc-300 text-xs font-mono px-2 py-0.5 rounded hover:bg-zinc-800 transition-colors"
            title="Clear terminal"
          >
            clear
          </button>
          <button
            onClick={handleClose}
            className="text-zinc-500 hover:text-zinc-300 text-xs font-mono px-2 py-0.5 rounded hover:bg-zinc-800 transition-colors"
            title="Close terminal (Ctrl+`)"
          >
            ×
          </button>
        </div>
      </div>

      {/* Log area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-3 py-2 font-mono text-xs"
        style={{ scrollbarWidth: "thin", scrollbarColor: "#3f3f46 transparent" }}
      >
        {terminalLogs.length === 0 ? (
          <span className="text-zinc-600">
            No events yet. Send a chat message to see agent logs.
          </span>
        ) : (
          terminalLogs.map((entry) => <LogLine key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
});
