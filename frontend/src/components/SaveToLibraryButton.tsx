import { useState } from "react";
import type { ChatMessage } from "../types";
import { saveToResearchLibrary } from "../api";

interface Props {
  message: ChatMessage;
}

type SaveState = "idle" | "saving" | "saved" | "error";

const TICKER_RE = /@([A-Z]{1,10})\b/g;

function extractTickers(text: string): string[] {
  const tickers = new Set<string>();
  let m: RegExpExecArray | null;
  TICKER_RE.lastIndex = 0;
  while ((m = TICKER_RE.exec(text)) !== null) {
    tickers.add(m[1]);
  }
  return Array.from(tickers);
}

export function SaveToLibraryButton({ message }: Props) {
  const [saveState, setSaveState] = useState<SaveState>("idle");

  if (!message.runId || message.completionStatus !== "complete") return null;

  const handleSave = async () => {
    setSaveState("saving");
    try {
      await saveToResearchLibrary({
        run_id: message.runId!,
        category: "chat_response",
        content: message.content,
        sources: (message.sources ?? []).map((s) => ({ url: s.url, title: s.title })),
        tags: extractTickers(message.content),
      });
      setSaveState("saved");
    } catch {
      setSaveState("error");
      setTimeout(() => setSaveState("idle"), 2500);
    }
  };

  if (saveState === "saved") {
    return (
      <span className="save-library-btn save-library-btn--saved" aria-label="Saved to Research Library">
        ✓ Saved to Library
      </span>
    );
  }

  return (
    <button
      className="btn-ghost chat-run-link save-library-btn"
      onClick={handleSave}
      disabled={saveState === "saving"}
      title="Save this response to your Research Library"
    >
      {saveState === "saving" ? "Saving…" : saveState === "error" ? "Save failed — retry?" : "Save to Library"}
    </button>
  );
}
