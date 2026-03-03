import { useEffect, useState } from "react";
import { fetchRun, fetchRunEvents } from "../api";
import type { AgentRunEvent, AgentRunSummary } from "../types";

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

export function RunExplorerModal({ runId, onClose }: Props) {
  const [run, setRun] = useState<AgentRunSummary | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card run-explorer-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Run Explorer</div>
        <div className="run-explorer-meta">
          <div><strong>Run ID:</strong> {runId}</div>
          {run && <div><strong>Status:</strong> {run.status}</div>}
          {run && <div><strong>Mode:</strong> {run.mode}</div>}
        </div>

        {loading && <div className="run-explorer-loading">Loading run events…</div>}
        {error && <div className="run-explorer-error">{error}</div>}

        {!loading && !error && (
          <div className="run-explorer-events" role="log" aria-label="Run events">
            {events.map((event) => (
              <div key={event.id} className="run-event-row">
                <div className="run-event-header">
                  <span>#{event.seq}</span>
                  <span>{event.type}</span>
                </div>
                <pre className="run-event-payload">{eventPayload(event)}</pre>
              </div>
            ))}
            {events.length === 0 && <div className="run-explorer-loading">No events found.</div>}
          </div>
        )}

        <div className="modal-actions">
          <button className="btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
