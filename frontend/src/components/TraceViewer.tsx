import type { AgentRunBundle, AgentRunExportEvent, RunBundleArtifact } from "../types";

interface TraceViewerProps {
  runId: string;
  bundle: AgentRunBundle;
}

interface ArtifactRef {
  label: string;
  anchorId: string;
  artifact: RunBundleArtifact;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "--:--:--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString([], { hour12: false });
}

function formatDuration(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${Math.round(value)}ms`;
}

function collectRefTokens(value: unknown, refs: Set<string>): void {
  if (typeof value === "string") {
    const matches = value.match(/\bREF-\d+\b/gi);
    if (matches) {
      for (const token of matches) {
        refs.add(token.toUpperCase());
      }
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectRefTokens(item, refs);
    return;
  }
  if (value && typeof value === "object") {
    for (const inner of Object.values(value as Record<string, unknown>)) {
      collectRefTokens(inner, refs);
    }
  }
}

function artifactRefsForBundle(bundle: AgentRunBundle): ArtifactRef[] {
  const sorted = [...bundle.artifacts_registry].sort((a, b) => {
    const seqA = typeof a.seq === "number" ? a.seq : Number.MAX_SAFE_INTEGER;
    const seqB = typeof b.seq === "number" ? b.seq : Number.MAX_SAFE_INTEGER;
    if (seqA !== seqB) return seqA - seqB;
    const idA = typeof a.event_id === "number" ? a.event_id : Number.MAX_SAFE_INTEGER;
    const idB = typeof b.event_id === "number" ? b.event_id : Number.MAX_SAFE_INTEGER;
    return idA - idB;
  });

  return sorted.map((artifact, idx) => {
    const label = `REF-${idx + 1}`;
    return {
      label,
      anchorId: `trace-artifact-${label}`,
      artifact,
    };
  });
}

function refsUsedInFinalAnswer(events: AgentRunExportEvent[]): Set<string> {
  const refs = new Set<string>();
  for (const event of events) {
    collectRefTokens(event.payload, refs);
  }
  return refs;
}

function artifactLabel(artifact: RunBundleArtifact): string {
  if (typeof artifact.tool === "string" && artifact.tool) return artifact.tool;
  if (typeof artifact.artifact_type === "string" && artifact.artifact_type) return artifact.artifact_type;
  return "artifact";
}

function eventRefs(event: AgentRunExportEvent): string[] {
  const refs = new Set<string>();
  collectRefTokens(event.payload, refs);
  return [...refs].sort((a, b) => {
    const aNum = Number(a.replace("REF-", ""));
    const bNum = Number(b.replace("REF-", ""));
    if (Number.isFinite(aNum) && Number.isFinite(bNum)) return aNum - bNum;
    return a.localeCompare(b);
  });
}

function summaryPayload(payload: Record<string, unknown> | null): string {
  if (!payload) return "{}";
  const clean = JSON.stringify(payload);
  if (clean.length <= 220) return clean;
  return `${clean.slice(0, 220)}…`;
}

export function TraceViewer({ runId, bundle }: TraceViewerProps) {
  const refMap = artifactRefsForBundle(bundle);
  const usedRefs = refsUsedInFinalAnswer(bundle.event_timeline);
  const refByLabel = new Map(refMap.map((entry) => [entry.label, entry]));

  const rows = Object.entries(bundle.context_snapshots) as Array<[string, Array<{ id: number; category: string; citations: unknown[]; tags: string[]; created_at: string | null }>]>;

  return (
    <section className="trace-viewer" aria-label="Run trace viewer">
      <div className="trace-viewer-header">
        <div className="trace-viewer-title">Trace Viewer</div>
        <div className="trace-viewer-subtitle">run {runId}</div>
      </div>

      <div className="trace-panel">
        <div className="trace-panel-title">Timeline</div>
        <div className="trace-timeline" role="list" aria-label="Run timeline">
          {bundle.event_timeline.map((event) => {
            const payload = event.payload;
            const duration = formatDuration(payload?.duration_ms);
            const tool = typeof payload?.tool === "string" ? payload.tool : null;
            const refs = eventRefs(event);
            return (
              <div key={event.id} className="trace-row" role="listitem">
                <div className="trace-line" aria-hidden="true" />
                <div className="trace-row-body">
                  <div className="trace-row-head">
                    <span className="trace-seq">#{event.seq}</span>
                    <span className="trace-type">{event.type}</span>
                    <span className="trace-time">{formatTimestamp(event.created_at)}</span>
                    {tool && <span className="trace-chip">{tool}</span>}
                    {duration && <span className="trace-chip trace-chip-duration">{duration}</span>}
                  </div>
                  <div className="trace-row-payload">{summaryPayload(payload)}</div>
                  {refs.length > 0 && (
                    <div className="trace-ref-links">
                      {refs.map((ref) => {
                        const entry = refByLabel.get(ref);
                        if (!entry) {
                          return (
                            <span key={ref} className="trace-ref-unresolved" title="Artifact not found in bundle registry">
                              {ref}
                            </span>
                          );
                        }
                        return (
                          <a key={ref} href={`#${entry.anchorId}`} className="trace-ref-link">
                            {ref}
                          </a>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="trace-grid">
        <div className="trace-panel">
          <div className="trace-panel-title">Artifacts</div>
          {refMap.length === 0 && <div className="trace-empty">No artifacts in this bundle.</div>}
          {refMap.map((entry) => {
            const artifact = entry.artifact;
            const duration = formatDuration((artifact.result_envelope as Record<string, unknown> | undefined)?.duration_ms);
            return (
              <div key={entry.anchorId} id={entry.anchorId} className="trace-artifact-row">
                <div className="trace-artifact-head">
                  <span className="trace-ref-tag">{entry.label}</span>
                  <span className="trace-artifact-name">{artifactLabel(artifact)}</span>
                  {typeof artifact.seq === "number" && <span className="trace-chip">#{artifact.seq}</span>}
                  {duration && <span className="trace-chip trace-chip-duration">{duration}</span>}
                </div>
                <div className="trace-row-payload">{summaryPayload((artifact.result_envelope as Record<string, unknown> | null) ?? null)}</div>
              </div>
            );
          })}
        </div>

        <div className="trace-panel">
          <div className="trace-panel-title">References & Context</div>
          <div className="trace-context-summary">
            <span className="trace-chip">Citations: {bundle.citations.length}</span>
            <span className="trace-chip">Used refs: {usedRefs.size}</span>
          </div>
          {usedRefs.size > 0 && (
            <div className="trace-ref-links">
              {[...usedRefs]
                .sort((a, b) => Number(a.replace("REF-", "")) - Number(b.replace("REF-", "")))
                .map((ref) => {
                  const entry = refByLabel.get(ref);
                  if (!entry) {
                    return <span key={ref} className="trace-ref-unresolved">{ref}</span>;
                  }
                  return (
                    <a key={ref} href={`#${entry.anchorId}`} className="trace-ref-link">
                      {ref}
                    </a>
                  );
                })}
            </div>
          )}

          <div className="trace-context-list">
            {rows.map(([key, items]) => (
              <div key={key} className="trace-context-row">
                <div className="trace-context-head">
                  <span>{key}</span>
                  <span>{items.length}</span>
                </div>
                {items.slice(0, 3).map((item) => (
                  <div key={`${key}-${item.id}`} className="trace-context-item">
                    <span>{item.category}</span>
                    <span>{formatTimestamp(item.created_at)}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
