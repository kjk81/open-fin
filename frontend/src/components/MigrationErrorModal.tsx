import { useState } from "react";
import { wipeData } from "../api";

interface Props {
  error: string | null;
}

export function MigrationErrorModal({ error }: Props) {
  const [wiping, setWiping] = useState(false);
  const [wipeError, setWipeError] = useState<string | null>(null);

  const handleWipe = async () => {
    setWiping(true);
    setWipeError(null);

    // Try the REST endpoint first; fall back to Electron IPC if available
    const ok = await wipeData("all");
    if (!ok) {
      try {
        await window.electronAPI?.wipeUserData?.();
      } catch {
        setWipeError("Failed to reset data. Please delete the Open-Fin data folder manually and restart.");
        setWiping(false);
        return;
      }
    }

    // Reload the app so health polling restarts cleanly
    window.location.reload();
  };

  return (
    <div className="modal-overlay">
      <div
        className="modal-card"
        style={{ maxWidth: 480, padding: 28, display: "flex", flexDirection: "column", gap: 16 }}
      >
        <h2 style={{ margin: 0, fontSize: 17, color: "var(--text)" }}>
          Database Migration Required
        </h2>

        <p style={{ margin: 0, color: "var(--text-muted)", lineHeight: 1.5 }}>
          Open-Fin found data from a previous installation that cannot be
          automatically migrated to the current version:
        </p>

        {error && (
          <pre
            style={{
              margin: 0,
              padding: "10px 12px",
              background: "var(--surface-raised, rgba(255,255,255,0.05))",
              border: "1px solid var(--border)",
              borderRadius: 4,
              fontSize: 12,
              color: "var(--yellow, #f9c74f)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontFamily: "monospace",
              maxHeight: 120,
              overflowY: "auto",
            }}
          >
            {error}
          </pre>
        )}

        <p style={{ margin: 0, color: "var(--text-muted)", lineHeight: 1.5 }}>
          You can reset your local data to continue. This will erase portfolio
          sync history, chat history, knowledge graph, and cached reports.{" "}
          <strong style={{ color: "var(--text)" }}>
            Your <code>.env</code> file and Alpaca API keys are not affected.
          </strong>
        </p>

        {wipeError && (
          <p style={{ margin: 0, color: "var(--red)", fontSize: 13 }}>{wipeError}</p>
        )}

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 4 }}>
          <button
            onClick={() => window.close()}
            disabled={wiping}
            style={{
              padding: "6px 16px",
              background: "transparent",
              border: "1px solid var(--border)",
              borderRadius: 4,
              color: "var(--text-muted)",
              cursor: wiping ? "not-allowed" : "pointer",
              fontSize: 13,
            }}
          >
            Quit
          </button>
          <button
            onClick={handleWipe}
            disabled={wiping}
            style={{
              padding: "6px 16px",
              background: "var(--red, #e63946)",
              border: "none",
              borderRadius: 4,
              color: "#fff",
              cursor: wiping ? "not-allowed" : "pointer",
              opacity: wiping ? 0.7 : 1,
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            {wiping ? "Resetting…" : "Reset Local Data"}
          </button>
        </div>
      </div>
    </div>
  );
}
