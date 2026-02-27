import { useEffect, useMemo, useState } from "react";
import { fetchLlmSettings, updateLlmSettings } from "../api";
import type { LlmProvider, LlmSettings } from "../types";

const providerLabel: Record<LlmProvider, string> = {
  openrouter: "OpenRouter",
  gemini: "Gemini",
  openai: "OpenAI",
  groq: "Groq",
  huggingface: "Hugging Face Inference",
  ollama: "Ollama (Local)",
};

export function LlmSettingsPanel() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [mode, setMode] = useState<"cloud" | "ollama">("cloud");
  const [order, setOrder] = useState<LlmProvider[]>([]);
  const [dragging, setDragging] = useState<LlmProvider | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchLlmSettings();
        if (!mounted) return;
        setSettings(result);
        setMode(result.mode);
        setOrder(result.fallback_order);
      } catch (err) {
        if (!mounted) return;
        setError(String(err));
      } finally {
        if (mounted) setLoading(false);
      }
    };

    load();
    return () => {
      mounted = false;
    };
  }, []);

  const dirty = useMemo(() => {
    if (!settings) return false;
    if (mode !== settings.mode) return true;
    return order.join("|") !== settings.fallback_order.join("|");
  }, [mode, order, settings]);

  const moveProvider = (from: LlmProvider, to: LlmProvider) => {
    if (from === to) return;
    const next = [...order];
    const fromIdx = next.indexOf(from);
    const toIdx = next.indexOf(to);
    if (fromIdx === -1 || toIdx === -1) return;
    next.splice(fromIdx, 1);
    next.splice(toIdx, 0, from);
    setOrder(next);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await updateLlmSettings(mode, order);
      setSettings(saved);
      setMode(saved.mode);
      setOrder(saved.fallback_order);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="llm-settings-wrap">
      <button className="btn-ghost" onClick={() => setOpen((v) => !v)}>
        LLM Settings
      </button>

      {open && (
        <div className="llm-settings-popover">
          <div className="llm-settings-title">Model Routing</div>

          {loading ? (
            <div className="llm-settings-hint">Loading…</div>
          ) : (
            <>
              <div className="llm-mode-row">
                <label>
                  <input
                    type="radio"
                    name="llm-mode"
                    checked={mode === "cloud"}
                    onChange={() => setMode("cloud")}
                  />
                  Cloud + Fallback
                </label>
                <label>
                  <input
                    type="radio"
                    name="llm-mode"
                    checked={mode === "ollama"}
                    onChange={() => setMode("ollama")}
                  />
                  Ollama Only
                </label>
              </div>

              <div className="llm-settings-hint">Fallback order (drag to reorder):</div>
              <div className="llm-provider-list">
                {order.map((provider) => (
                  <div
                    key={provider}
                    className="llm-provider-item"
                    draggable
                    onDragStart={() => setDragging(provider)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => {
                      if (!dragging) return;
                      moveProvider(dragging, provider);
                      setDragging(null);
                    }}
                    onDragEnd={() => setDragging(null)}
                  >
                    <span className="llm-drag-handle">⋮⋮</span>
                    <span>{providerLabel[provider]}</span>
                  </div>
                ))}
              </div>

              {mode === "ollama" && (
                <div className="llm-settings-hint">Ollama mode forces local-only execution.</div>
              )}

              {error && <div className="trade-error">{error}</div>}

              <div className="modal-actions" style={{ marginTop: "12px" }}>
                <button className="btn-ghost" onClick={() => setOpen(false)} disabled={saving}>
                  Close
                </button>
                <button className="btn-send" onClick={handleSave} disabled={!dirty || saving}>
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
