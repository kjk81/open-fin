import { useState, useEffect, useCallback } from "react";
import { createTickerNote, deleteTickerNote, fetchGraphConnections, fetchTickerNotes } from "../api";
import { useAppContext } from "../context/AppContext";
import { AnalysisPanel } from "./AnalysisPanel";
import { RecentEventTimeline } from "./RecentEventTimeline";
import { Spinner } from "./Spinner";
import { TickerCard } from "./TickerCard";
import type { GraphConnectionsSummary, TickerNote } from "../types";

export function TickerDashboard() {
  const { state, toggleWatchlist, navigateToDashboard } = useAppContext();
  const {
    activeTicker,
    activeTickerLoading,
    activeTickerError,
    selectedSymbol,
    tickerAnalysis,
    watchlist,
  } = state;
  const [starring, setStarring] = useState(false);
  const [connections, setConnections] = useState<GraphConnectionsSummary | null>(null);
  const [connectionsLoading, setConnectionsLoading] = useState(false);
  const [connectionsError, setConnectionsError] = useState<string | null>(null);

  const [notes, setNotes] = useState<TickerNote[]>([]);
  const [notesTotal, setNotesTotal] = useState(0);
  const [notesLoading, setNotesLoading] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);
  const [deletingNoteId, setDeletingNoteId] = useState<number | null>(null);

  const isWatched = selectedSymbol != null && watchlist.some((w) => w.ticker === selectedSymbol);

  const handleStar = async () => {
    if (!selectedSymbol || starring) return;
    setStarring(true);
    try {
      await toggleWatchlist(selectedSymbol);
    } finally {
      setStarring(false);
    }
  };

  const loadConnections = useCallback(async () => {
    if (!selectedSymbol) {
      setConnections(null);
      setConnectionsError(null);
      return;
    }
    setConnectionsLoading(true);
    setConnectionsError(null);
    try {
      const data = await fetchGraphConnections(selectedSymbol);
      setConnections(data);
    } catch (error) {
      setConnections(null);
      setConnectionsError(String(error));
    } finally {
      setConnectionsLoading(false);
    }
  }, [selectedSymbol]);

  const loadNotes = useCallback(async () => {
    if (!selectedSymbol) {
      setNotes([]);
      setNotesTotal(0);
      setNotesError(null);
      return;
    }
    setNotesLoading(true);
    setNotesError(null);
    try {
      const page = await fetchTickerNotes(selectedSymbol, 0, 50);
      setNotes(page.items);
      setNotesTotal(page.total);
    } catch (error) {
      setNotes([]);
      setNotesTotal(0);
      setNotesError(String(error));
    } finally {
      setNotesLoading(false);
    }
  }, [selectedSymbol]);

  useEffect(() => {
    loadConnections();
    loadNotes();
  }, [loadConnections, loadNotes]);

  const handleSaveNote = async () => {
    if (!selectedSymbol || noteSaving) return;
    const content = noteDraft.trim();
    if (!content) return;

    setNoteSaving(true);
    setNotesError(null);
    try {
      await createTickerNote(selectedSymbol, content);
      setNoteDraft("");
      await loadNotes();
    } catch (error) {
      setNotesError(String(error));
    } finally {
      setNoteSaving(false);
    }
  };

  const handleDeleteNote = async (noteId: number) => {
    if (!selectedSymbol || deletingNoteId != null) return;
    setDeletingNoteId(noteId);
    setNotesError(null);
    try {
      await deleteTickerNote(selectedSymbol, noteId);
      await loadNotes();
    } catch (error) {
      setNotesError(String(error));
    } finally {
      setDeletingNoteId(null);
    }
  };

  return (
    <aside className="pane-dashboard">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <button className="ticker-back-btn" onClick={navigateToDashboard}>
            ← Back
          </button>
          <h2 className="pane-title">Ticker</h2>
        </div>
        {selectedSymbol && (
          <button
            className="btn-ghost"
            onClick={handleStar}
            disabled={starring}
            title={isWatched ? "Remove from Watchlist" : "Add to Watchlist"}
            style={{ fontSize: "18px", lineHeight: 1, padding: "2px 6px", color: isWatched ? "var(--accent)" : "var(--text-muted)" }}
          >
            {starring ? <Spinner size={14} /> : isWatched ? "★" : "☆"}
          </button>
        )}
      </div>

      {/* Empty state */}
      {!selectedSymbol && !activeTickerLoading && (
        <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
          Click a portfolio position or mention a ticker in chat to see details here.
        </p>
      )}

      {/* Loading ticker data */}
      {activeTickerLoading && (
        <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--text-muted)", fontSize: "13px" }}>
          <Spinner />
          Loading {selectedSymbol}...
        </div>
      )}

      {/* Error */}
      {activeTickerError && !activeTickerLoading && (
        <div style={{ color: "var(--red)", fontSize: "13px" }}>
          {activeTickerError}
        </div>
      )}

      {/* Ticker stats + analysis panel */}
      {activeTicker && !activeTickerLoading && (
        <>
          <TickerCard info={activeTicker} />

          <div style={{ borderTop: "1px solid var(--border)", marginTop: "20px", paddingTop: "16px" }}>
            <h3 style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "12px" }}>
              AI Analysis
            </h3>
            <AnalysisPanel analysis={tickerAnalysis} />
          </div>

          {selectedSymbol && (
            <div className="ticker-side-section">
              <h3 className="ticker-side-section-title">Graph Connections</h3>

              {connectionsLoading && <p className="ticker-side-muted">Loading graph connections...</p>}
              {connectionsError && !connectionsLoading && (
                <p className="ticker-side-error">{connectionsError}</p>
              )}

              {!connectionsLoading && !connectionsError && connections && (
                <>
                  <div className="ticker-connections-row">
                    <span className="ticker-side-muted">Total</span>
                    <span>{connections.total_connections}</span>
                  </div>
                  <div className="ticker-connections-kinds">
                    <span className="kg-badge kg-badge--IN_SECTOR">
                      IN_SECTOR: {connections.by_kind.IN_SECTOR ?? 0}
                    </span>
                    <span className="kg-badge kg-badge--IN_INDUSTRY">
                      IN_INDUSTRY: {connections.by_kind.IN_INDUSTRY ?? 0}
                    </span>
                    <span className="kg-badge kg-badge--CO_MENTION">
                      CO_MENTION: {connections.by_kind.CO_MENTION ?? 0}
                    </span>
                  </div>
                  <ul className="ticker-connections-neighbors">
                    {connections.neighbors.slice(0, 12).map((neighbor, idx) => (
                      <li key={`${neighbor.name}-${neighbor.edge_kind}-${idx}`}>
                        <span>{neighbor.name}</span>
                        <span className={`kg-badge kg-badge--${neighbor.edge_kind}`}>{neighbor.edge_kind}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}

          {selectedSymbol && (
            <div className="ticker-side-section">
              <h3 className="ticker-side-section-title">Notes</h3>

              <div className="ticker-notes-compose">
                <textarea
                  className="ticker-notes-input"
                  placeholder={`Add note for ${selectedSymbol}...`}
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                />
                <button className="btn-ghost" onClick={handleSaveNote} disabled={noteSaving || !noteDraft.trim()}>
                  {noteSaving ? "Saving..." : "Save"}
                </button>
              </div>

              {notesLoading && <p className="ticker-side-muted">Loading notes...</p>}
              {notesError && !notesLoading && <p className="ticker-side-error">{notesError}</p>}
              {!notesLoading && !notesError && notesTotal === 0 && (
                <p className="ticker-side-muted">No notes yet.</p>
              )}

              {!notesLoading && notes.length > 0 && (
                <ul className="ticker-notes-list">
                  {notes.map((note) => (
                    <li key={note.id}>
                      <div className="ticker-notes-content">{note.content}</div>
                      <div className="ticker-notes-meta">
                        <span>
                          {note.created_at ? new Date(note.created_at).toLocaleString() : "—"}
                        </span>
                        <button
                          className="kg-table-action"
                          onClick={() => handleDeleteNote(note.id)}
                          disabled={deletingNoteId === note.id}
                        >
                          {deletingNoteId === note.id ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {selectedSymbol && <RecentEventTimeline symbol={selectedSymbol} />}
        </>
      )}
    </aside>
  );
}
