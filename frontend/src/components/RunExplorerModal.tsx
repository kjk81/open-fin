import { useEffect, useMemo, useState } from "react";
import { downloadRunBundle, fetchRun, fetchRunBundle, fetchRunEvents } from "../api";
import { useAppContext } from "../context/AppContext";
import type { AgentRunBundle, AgentRunEvent, AgentRunSummary, StateWritePayload } from "../types";
import { TraceViewer } from "./TraceViewer";

interface Props {
  runId: string;
  onClose: () => void;
}

function eventPayload(event: AgentRunEvent): string {
  if (event.payload) {
    return JSON.stringify(event.payload, null, 2);
  }
  return event.payload_json || "{}";
}

function parseStateWritePayload(event: AgentRunEvent): StateWritePayload | null {
  if (event.type !== "state_write") return null;
  try {
    const payload = event.payload ?? JSON.parse(event.payload_json || "{}");
    if (payload && typeof payload.tool === "string" && typeof payload.rollback_hint === "string") {
      return payload as StateWritePayload;
    }
  } catch { /* ignore */ }
  return null;
}

function StateWriteEventRow({ event, payload }: { event: AgentRunEvent; payload: StateWritePayload }) {
  const [showRollback, setShowRollback] = useState(false);

  return (
    <div className="run-event-row state-write-row">
      <div className="run-event-header">
        <span>#{event.seq}</span>
        <span className="state-write-badge">STATE WRITE</span>
      </div>
      <div className="state-write-detail">
        <div className="state-write-tool">{payload.tool}</div>
        {payload.delta && <div className="state-write-delta">{payload.delta}</div>}
        {payload.result_summary && (
          <div className="state-write-result">{payload.result_summary}</div>
        )}
        {payload.args && Object.keys(payload.args).length > 0 && (
          <div className="state-write-args">
            {Object.entries(payload.args).map(([k, v]) => (
              <span key={k} className="action-arg-chip">
                {k}: {JSON.stringify(v)}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="state-write-rollback-row">
        <button
          className="btn-ghost state-write-rollback-btn"
          onClick={() => setShowRollback((s) => !s)}
        >
          {showRollback ? "Hide Undo Info" : "How to Undo"}
        </button>
        {showRollback && (
          <pre className="state-write-rollback-hint">{payload.rollback_hint}</pre>
        )}
      </div>
    </div>
  );
}

export function RunExplorerModal({ runId, onClose }: Props) {
  const { state } = useAppContext();
  const [run, setRun] = useState<AgentRunSummary | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [bundle, setBundle] = useState<AgentRunBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadStatus, setDownloadStatus] = useState<string | null>(null);

  const showTraceViewer = useMemo(
    () => import.meta.env.DEV || state.debugMode,
    [state.debugMode],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([fetchRun(runId), fetchRunEvents(runId)])
      .then(([runSummary, runEvents]) => {
        if (cancelled) return;
        setRun(runSummary);
        setEvents(runEvents.items);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => {
    if (!showTraceViewer) {
      setBundle(null);
      setBundleError(null);
      setBundleLoading(false);
      return;
    }

    let cancelled = false;
    setBundleLoading(true);
    setBundleError(null);

    fetchRunBundle(runId)
      .then((runBundle) => {
        if (cancelled) return;
        setBundle(runBundle);
      })
      .catch((err) => {
        if (cancelled) return;
        setBundleError(String(err));
      })
      .finally(() => {
        if (cancelled) return;
        setBundleLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId, showTraceViewer]);

  const handleDownloadBundle = async () => {
    setDownloadStatus(null);
    setDownloading(true);
    try {
      const result = await downloadRunBundle(runId);
      if (result.canceled) {
        setDownloadStatus("Download canceled.");
      } else if (result.path) {
        setDownloadStatus(`Saved: ${result.path}`);
      } else {
        setDownloadStatus("Bundle downloaded.");
      }
    } catch (err) {
      setDownloadStatus(`Download failed: ${String(err)}`);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card run-explorer-modal" onClick={(e) => e.stopPropagation()}>
        <div className="run-explorer-header">
          <div className="modal-title">Run Explorer</div>
          <button className="btn-ghost" onClick={handleDownloadBundle} disabled={downloading}>
            {downloading ? "Downloading…" : "Download Bundle"}
          </button>
        </div>
        <div className="run-explorer-meta">
          <div><strong>Run ID:</strong> {runId}</div>
          {run && <div><strong>Status:</strong> {run.status}</div>}
          {run && <div><strong>Mode:</strong> {run.mode}</div>}
        </div>
        {downloadStatus && <div className="run-explorer-loading">{downloadStatus}</div>}

        {loading && <div className="run-explorer-loading">Loading run events…</div>}
        {error && <div className="run-explorer-error">{error}</div>}

        {!loading && !error && (
          <div className="run-explorer-events" role="log" aria-label="Run events">
            {events.map((event) => {
              const stateWrite = parseStateWritePayload(event);
              if (stateWrite) {
                return <StateWriteEventRow key={event.id} event={event} payload={stateWrite} />;
              }
              return (
                <div key={event.id} className="run-event-row">
                  <div className="run-event-header">
                    <span>#{event.seq}</span>
                    <span>{event.type}</span>
                  </div>
                  <pre className="run-event-payload">{eventPayload(event)}</pre>
                </div>
              );
            })}
            {events.length === 0 && <div className="run-explorer-loading">No events found.</div>}
          </div>
        )}

        {showTraceViewer && bundleLoading && <div className="run-explorer-loading">Loading trace bundle…</div>}
        {showTraceViewer && bundleError && <div className="run-explorer-error">{bundleError}</div>}
        {showTraceViewer && bundle && <TraceViewer runId={runId} bundle={bundle} />}

        <div className="modal-actions">
          <button className="btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
