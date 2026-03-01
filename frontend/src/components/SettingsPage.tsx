import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchSettingsSchema,
  fetchSettings,
  saveSettings,
  fetchLlmSettings,
  updateLlmSettings,
} from "../api";
import type { SettingSchema, SettingsValues, LlmProvider, LlmSettings } from "../types";
import { Spinner } from "./Spinner";

// ── Provider labels (migrated from LlmSettingsPanel) ─────────────────────

const providerLabel: Record<LlmProvider, string> = {
  openrouter: "OpenRouter",
  gemini: "Gemini",
  openai: "OpenAI",
  groq: "Groq",
  huggingface: "Hugging Face Inference",
  ollama: "Ollama (Local)",
};

// ── SVG helpers ──────────────────────────────────────────────────────────

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", opacity: 0.5 }}>
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function EyeIcon({ open }: { open: boolean }) {
  if (open) {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

// ── Main component ───────────────────────────────────────────────────────

interface SettingsPageProps {
  onBack: () => void;
}

export function SettingsPage({ onBack }: SettingsPageProps) {
  // ── Schema + values state ──────────────────────────────────────────────
  const [schema, setSchema] = useState<SettingSchema[]>([]);
  const [values, setValues] = useState<SettingsValues>({});
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [visibleSecrets, setVisibleSecrets] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);

  // ── LLM routing state (migrated from LlmSettingsPanel) ────────────────
  const [llmSettings, setLlmSettings] = useState<LlmSettings | null>(null);
  const [llmMode, setLlmMode] = useState<"cloud" | "ollama">("cloud");
  const [llmOrder, setLlmOrder] = useState<LlmProvider[]>([]);
  const [llmSubagentOrder, setLlmSubagentOrder] = useState<LlmProvider[] | null>(null);
  const [dragging, setDragging] = useState<LlmProvider | null>(null);
  const [draggingSubagent, setDraggingSubagent] = useState<LlmProvider | null>(null);
  const [advancedModelMode, setAdvancedModelMode] = useState(false);

  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});
  const contentRef = useRef<HTMLDivElement>(null);

  // ── Load all data on mount ─────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [schemaData, valuesData, llmData] = await Promise.all([
          fetchSettingsSchema(),
          fetchSettings(),
          fetchLlmSettings(),
        ]);
        if (!mounted) return;
        setSchema(schemaData);
        setValues(valuesData);
        setLlmSettings(llmData);
        setLlmMode(llmData.mode);
        setLlmOrder(llmData.fallback_order);
        setLlmSubagentOrder(llmData.subagent_fallback_order ?? null);
        // Initialise advanced model toggle from persisted .env state
        setAdvancedModelMode(
          Boolean(valuesData["SUBAGENT_PROVIDER"]?.is_set || valuesData["SUBAGENT_MODEL"]?.is_set)
        );
        // Set first category as active
        if (schemaData.length > 0) {
          const cats = [...new Set(schemaData.map((s) => s.category))];
          setActiveCategory(cats[0]);
        }
      } catch (err) {
        if (mounted) setError(String(err));
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => { mounted = false; };
  }, []);

  // ── Derived data ───────────────────────────────────────────────────────

  const categories = useMemo(() => {
    const cats = [...new Set(schema.map((s) => s.category))];
    // Ensure "LLM Routing" is always the last nav item, whether it arrived
    // from schema items (4 role-override env vars) or needs to be injected.
    const idx = cats.indexOf("LLM Routing");
    if (idx !== -1) cats.splice(idx, 1);
    cats.push("LLM Routing");
    return cats;
  }, [schema]);

  const filteredSchema = useMemo(() => {
    if (!searchQuery.trim()) return schema;
    const q = searchQuery.toLowerCase();
    return schema.filter(
      (s) =>
        s.label.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.key.toLowerCase().includes(q)
    );
  }, [schema, searchQuery]);

  const groupedSettings = useMemo(() => {
    const groups: Record<string, SettingSchema[]> = {};
    for (const s of filteredSchema) {
      (groups[s.category] ??= []).push(s);
    }
    return groups;
  }, [filteredSchema]);

  // ── Dirty detection ────────────────────────────────────────────────────

  const llmDirty = useMemo(() => {
    if (!llmSettings) return false;
    if (llmMode !== llmSettings.mode) return true;
    if (llmOrder.join("|") !== llmSettings.fallback_order.join("|")) return true;
    const savedSub = llmSettings.subagent_fallback_order ?? null;
    if (savedSub === null && llmSubagentOrder === null) return false;
    if (savedSub === null || llmSubagentOrder === null) return true;
    return llmSubagentOrder.join("|") !== savedSub.join("|");
  }, [llmMode, llmOrder, llmSubagentOrder, llmSettings]);

  const envDirty = Object.keys(edits).length > 0;
  const dirty = envDirty || llmDirty;

  // ── Handlers ───────────────────────────────────────────────────────────

  const handleEditChange = useCallback((key: string, value: string) => {
    setEdits((prev) => {
      const next = { ...prev };
      if (value === "" && !values[key]?.is_set) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  }, [values]);

  const handleCategoryClick = useCallback((cat: string) => {
    setActiveCategory(cat);
    const el = sectionRefs.current[cat];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  const toggleSecret = useCallback((key: string) => {
    setVisibleSecrets((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const moveProvider = useCallback((from: LlmProvider, to: LlmProvider) => {
    if (from === to) return;
    setLlmOrder((prev) => {
      const next = [...prev];
      const fromIdx = next.indexOf(from);
      const toIdx = next.indexOf(to);
      if (fromIdx === -1 || toIdx === -1) return prev;
      next.splice(fromIdx, 1);
      next.splice(toIdx, 0, from);
      return next;
    });
  }, []);

  const moveSubagentProvider = useCallback((from: LlmProvider, to: LlmProvider) => {
    if (from === to) return;
    setLlmSubagentOrder((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      const fromIdx = next.indexOf(from);
      const toIdx = next.indexOf(to);
      if (fromIdx === -1 || toIdx === -1) return prev;
      next.splice(fromIdx, 1);
      next.splice(toIdx, 0, from);
      return next;
    });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      // Save env values if changed
      if (envDirty) {
        const payload: Record<string, string | null> = {};
        for (const [key, val] of Object.entries(edits)) {
          payload[key] = val.trim() === "" ? null : val;
        }
        await saveSettings(payload);
      }
      // Save LLM routing if changed
      if (llmDirty) {
        const saved = await updateLlmSettings(llmMode, llmOrder, llmSubagentOrder);
        setLlmSettings(saved);
        setLlmMode(saved.mode);
        setLlmOrder(saved.fallback_order);
        setLlmSubagentOrder(saved.subagent_fallback_order ?? null);
      }
      // Reload values from server
      const freshValues = await fetchSettings();
      setValues(freshValues);
      setEdits({});
      setVisibleSecrets(new Set());
      setSuccessMsg("Settings saved successfully.");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDiscard = () => {
    setEdits({});
    setVisibleSecrets(new Set());
    if (llmSettings) {
      setLlmMode(llmSettings.mode);
      setLlmOrder(llmSettings.fallback_order);
      setLlmSubagentOrder(llmSettings.subagent_fallback_order ?? null);
    }
    setAdvancedModelMode(
      Boolean(values["SUBAGENT_PROVIDER"]?.is_set || values["SUBAGENT_MODEL"]?.is_set)
    );
    setError(null);
  };

  const handleAdvancedToggle = () => {
    if (advancedModelMode) {
      // Switching back to simple mode: clear subagent overrides and separate order
      handleEditChange("SUBAGENT_PROVIDER", "");
      handleEditChange("SUBAGENT_MODEL", "");
      setLlmSubagentOrder(null);
    } else {
      // Switching ON: seed subagent order from current agent order
      setLlmSubagentOrder(llmOrder.slice());
    }
    setAdvancedModelMode((prev) => !prev);
  };

  // ── Scroll spy ─────────────────────────────────────────────────────────

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    const handleScroll = () => {
      for (const cat of categories) {
        const el = sectionRefs.current[cat];
        if (el) {
          const rect = el.getBoundingClientRect();
          const containerRect = container.getBoundingClientRect();
          if (rect.top <= containerRect.top + 80) {
            setActiveCategory(cat);
          }
        }
      }
    };
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [categories]);

  // ── Render ─────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="settings-page" style={{ alignItems: "center", justifyContent: "center" }}>
        <Spinner size={20} />
        <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>Loading settings…</span>
      </div>
    );
  }

  return (
    <div className="settings-page">
      {/* Left sidebar nav */}
      <nav className="settings-nav">
        <button
          className="btn-ghost settings-back-btn"
          onClick={onBack}
          style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}
        >
          <BackIcon /> Back
        </button>
        <h2 className="pane-title" style={{ marginBottom: 12 }}>Settings</h2>
        {categories.map((cat) => (
          <button
            key={cat}
            className={`settings-nav-item${activeCategory === cat ? " active" : ""}`}
            onClick={() => handleCategoryClick(cat)}
          >
            {cat}
          </button>
        ))}
      </nav>

      {/* Right content area */}
      <div className="settings-content" ref={contentRef}>
        {/* Sticky search bar */}
        <div className="settings-search">
          <div style={{ position: "relative" }}>
            <SearchIcon />
            <input
              type="text"
              placeholder="Search settings…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              spellCheck={false}
            />
          </div>
        </div>

        {error && (
          <div className="settings-error">{error}</div>
        )}
        {successMsg && (
          <div className="settings-success">{successMsg}</div>
        )}

        {/* Setting sections by category */}
        {categories.map((cat) => {
          if (cat === "LLM Routing") {
            // Show section if search matches category name or any of the role-config keys
            if (searchQuery.trim()) {
              const q = searchQuery.toLowerCase();
              const routingItems = groupedSettings["LLM Routing"] || [];
              const schemaMatch = routingItems.some(
                (s) =>
                  s.label.toLowerCase().includes(q) ||
                  s.key.toLowerCase().includes(q) ||
                  s.description.toLowerCase().includes(q)
              );
              if (!"llm routing model configuration".includes(q) && !schemaMatch) return null;
            }
            return (
              <section
                key={cat}
                id={`settings-cat-${cat}`}
                ref={(el) => { sectionRefs.current[cat] = el; }}
              >
                {/* ── Model Configuration ──────────────────────────────── */}
                <h3 className="settings-category-title">Model Configuration</h3>
                <p className="settings-category-desc">
                  Assign specific providers and models to each AI role. By default both roles share the global fallback chain.
                </p>

                {/* Advanced-mode toggle */}
                <div className="settings-item">
                  <div className="settings-toggle-row">
                    <div style={{ flex: 1 }}>
                      <div className="settings-item-label" style={{ marginBottom: 4 }}>
                        Advanced: Use separate models for Reasoning (Subagent)?
                      </div>
                      <div className="settings-item-desc" style={{ marginBottom: 0 }}>
                        When off, both Agent and Subagent use the Primary Model. When on, configure each role independently.
                      </div>
                    </div>
                    <label className="settings-toggle">
                      <input
                        type="checkbox"
                        checked={advancedModelMode}
                        onChange={handleAdvancedToggle}
                      />
                      <span className="settings-toggle-slider" />
                    </label>
                  </div>
                </div>

                {/* Simple mode: single Primary Model block */}
                {!advancedModelMode ? (
                  <ModelBlock
                    title="Primary Model"
                    desc="Used by both the Agent (routing / prose) and Subagent (tool use). Leave blank to use the provider's default model."
                    providerKey="AGENT_PROVIDER"
                    modelKey="AGENT_MODEL"
                    values={values}
                    edits={edits}
                    onChange={handleEditChange}
                  />
                ) : (
                  <div className="settings-model-cards">
                    <ModelBlock
                      title="Agent (Orchestrator)"
                      desc="Fast model for chat, routing, and response synthesis."
                      providerKey="AGENT_PROVIDER"
                      modelKey="AGENT_MODEL"
                      values={values}
                      edits={edits}
                      onChange={handleEditChange}
                    />
                    <ModelBlock
                      title="Subagent (Analyst)"
                      desc="High-reasoning model for tool use and deep analysis. Falls back to Agent config when unset."
                      providerKey="SUBAGENT_PROVIDER"
                      modelKey="SUBAGENT_MODEL"
                      values={values}
                      edits={edits}
                      onChange={handleEditChange}
                    />
                  </div>
                )}

                {/* ── LLM Routing ───────────────────────────────────── */}
                <h3 className="settings-category-title" style={{ marginTop: 32 }}>LLM Routing</h3>
                <p className="settings-category-desc">
                  Configure how the AI agent selects LLM providers and fallback order.
                </p>

                {/* Mode selector */}
                <div className="settings-item">
                  <div className="settings-item-label">Mode</div>
                  <div className="settings-item-desc">
                    Cloud + Fallback uses cloud providers in priority order. Ollama Only forces local inference.
                  </div>
                  <div className="settings-radio-group">
                    <label className="settings-radio-label">
                      <input
                        type="radio"
                        name="llm-mode"
                        checked={llmMode === "cloud"}
                        onChange={() => setLlmMode("cloud")}
                      />
                      Cloud + Fallback
                    </label>
                    <label className="settings-radio-label">
                      <input
                        type="radio"
                        name="llm-mode"
                        checked={llmMode === "ollama"}
                        onChange={() => setLlmMode("ollama")}
                      />
                      Ollama Only
                    </label>
                  </div>
                </div>

                {/* Fallback order — agent (or shared when not in advanced mode) */}
                <div className="settings-item">
                  <div className="settings-item-label">
                    {advancedModelMode ? "Agent Fallback Order" : "Fallback Order"}
                  </div>
                  <div className="settings-item-desc">
                    Drag providers to reorder. The {advancedModelMode ? "agent" : "AI"} will try each in order until one succeeds.
                  </div>
                  <div className="settings-provider-list">
                    {llmOrder.map((provider, idx) => (
                      <div
                        key={provider}
                        className="settings-provider-item"
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
                        <span className="settings-drag-handle">⋮⋮</span>
                        <span className="settings-provider-rank">{idx + 1}</span>
                        <span>{providerLabel[provider] ?? provider}</span>
                      </div>
                    ))}
                  </div>
                  {llmMode === "ollama" && (
                    <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                      Ollama mode forces local-only execution — fallback order is ignored.
                    </p>
                  )}
                </div>

                {/* Subagent fallback order — only visible in advanced mode */}
                {advancedModelMode && llmSubagentOrder && (
                  <div className="settings-item">
                    <div className="settings-item-label">Subagent Fallback Order</div>
                    <div className="settings-item-desc">
                      Drag providers to set an independent fallback chain for the Subagent (tool-use / deep analysis) role.
                    </div>
                    <div className="settings-provider-list">
                      {llmSubagentOrder.map((provider, idx) => (
                        <div
                          key={provider}
                          className="settings-provider-item"
                          draggable
                          onDragStart={() => setDraggingSubagent(provider)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => {
                            if (!draggingSubagent) return;
                            moveSubagentProvider(draggingSubagent, provider);
                            setDraggingSubagent(null);
                          }}
                          onDragEnd={() => setDraggingSubagent(null)}
                        >
                          <span className="settings-drag-handle">⋮⋮</span>
                          <span className="settings-provider-rank">{idx + 1}</span>
                          <span>{providerLabel[provider] ?? provider}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            );
          }

          const items = groupedSettings[cat];
          if (!items || items.length === 0) return null;

          return (
            <section
              key={cat}
              id={`settings-cat-${cat}`}
              ref={(el) => { sectionRefs.current[cat] = el; }}
            >
              <h3 className="settings-category-title">{cat}</h3>
              {items.map((setting) => (
                <SettingRow
                  key={setting.key}
                  setting={setting}
                  serverValue={values[setting.key]}
                  editValue={edits[setting.key]}
                  isSecretVisible={visibleSecrets.has(setting.key)}
                  onToggleSecret={() => toggleSecret(setting.key)}
                  onChange={(val) => handleEditChange(setting.key, val)}
                />
              ))}
            </section>
          );
        })}
      </div>

      {/* Sticky save bar */}
      {dirty && (
        <div className="settings-save-bar">
          <button className="btn-ghost" onClick={handleDiscard} disabled={saving}>
            Discard
          </button>
          <button className="btn-send" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Model config block (provider select + model name input) ──────────────

const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "— Use global fallback —" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "gemini", label: "Gemini" },
  { value: "openai", label: "OpenAI" },
  { value: "groq", label: "Groq" },
  { value: "huggingface", label: "Hugging Face Inference" },
  { value: "ollama", label: "Ollama (Local)" },
];

interface ModelBlockProps {
  title: string;
  desc: string;
  providerKey: string;
  modelKey: string;
  values: import("../types").SettingsValues;
  edits: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

function ModelBlock({ title, desc, providerKey, modelKey, values, edits, onChange }: ModelBlockProps) {
  const providerVal = edits[providerKey] ?? (values[providerKey]?.value ?? "");
  const modelVal = edits[modelKey] ?? (values[modelKey]?.value ?? "");
  return (
    <div className="settings-model-card">
      <h4 className="settings-model-card-title">{title}</h4>
      <p className="settings-model-card-desc">{desc}</p>
      <div className="settings-model-field">
        <label className="settings-model-field-label">Provider</label>
        <div className="settings-item-key" style={{ marginBottom: 6 }}>{providerKey}</div>
        <select
          value={providerVal}
          onChange={(e) => onChange(providerKey, e.target.value)}
          style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)", padding: "7px 10px", borderRadius: 4, fontFamily: "inherit", fontSize: 13, width: "100%", outline: "none" }}
        >
          {PROVIDER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
      <div className="settings-model-field" style={{ marginTop: 10 }}>
        <label className="settings-model-field-label">Model</label>
        <div className="settings-item-key" style={{ marginBottom: 6 }}>{modelKey}</div>
        <input
          type="text"
          value={modelVal}
          placeholder="Use provider default"
          onChange={(e) => onChange(modelKey, e.target.value)}
          spellCheck={false}
          style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text)", padding: "7px 10px", borderRadius: 4, fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 13, width: "100%", outline: "none" }}
        />
      </div>
    </div>
  );
}

// ── Individual setting row ───────────────────────────────────────────────

interface SettingRowProps {
  setting: SettingSchema;
  serverValue?: { is_set: boolean; preview: string; value: string };
  editValue?: string;
  isSecretVisible: boolean;
  onToggleSecret: () => void;
  onChange: (value: string) => void;
}

function SettingRow({
  setting,
  serverValue,
  editValue,
  isSecretVisible,
  onToggleSecret,
  onChange,
}: SettingRowProps) {
  const hasEdit = editValue !== undefined;
  const displayValue = hasEdit ? editValue : (serverValue?.value ?? "");
  const placeholder = setting.type === "secret" && serverValue?.is_set && !hasEdit
    ? serverValue.preview
    : "";

  return (
    <div className="settings-item">
      <div className="settings-item-label">{setting.label}</div>
      <div className="settings-item-desc">{setting.description}</div>
      <div className="settings-item-key">{setting.key}</div>

      {setting.type === "secret" ? (
        <div className="settings-secret-wrap">
          <input
            type={isSecretVisible ? "text" : "password"}
            value={hasEdit ? editValue : ""}
            placeholder={placeholder || "Not set"}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
          <button
            className="btn-ghost settings-eye-btn"
            onClick={onToggleSecret}
            title={isSecretVisible ? "Hide" : "Show"}
            type="button"
          >
            <EyeIcon open={isSecretVisible} />
          </button>
        </div>
      ) : setting.type === "select" && setting.options ? (
        <select
          value={displayValue}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">— Select —</option>
          {setting.options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : setting.type === "number" ? (
        <input
          type="number"
          value={displayValue}
          placeholder="Not set"
          onChange={(e) => onChange(e.target.value)}
          step="any"
        />
      ) : (
        <input
          type="text"
          value={displayValue}
          placeholder="Not set"
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
        />
      )}
    </div>
  );
}
