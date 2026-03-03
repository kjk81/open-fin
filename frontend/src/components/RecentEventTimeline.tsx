import { useEffect, useState } from "react";

import { fetchTickerEvents } from "../api";
import type { TickerEventItem } from "../types";

export function RecentEventTimeline({ symbol }: { symbol: string }) {
  const [items, setItems] = useState<TickerEventItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchTickerEvents(symbol)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setItems([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div className="recent-events-wrap">
      <h3 className="recent-events-title">Recent Event Timeline</h3>
      {loading && <p className="recent-events-empty">Loading recent events...</p>}
      {error && <p className="recent-events-error">{error}</p>}
      {!loading && !error && items.length === 0 && (
        <p className="recent-events-empty">No recent events found.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <ul className="recent-events-list">
          {items.map((item) => (
            <li key={`${item.rank}-${item.url}`} className="recent-events-item">
              <div className="recent-events-dot" />
              <div className="recent-events-content">
                <a href={item.url} target="_blank" rel="noreferrer" className="recent-events-link">
                  {item.title}
                </a>
                <p className="recent-events-snippet">{item.snippet}</p>
                <div className="recent-events-meta">
                  <span>{item.provider}</span>
                  <span>#{item.rank}</span>
                  <span>{new Date(item.occurred_at).toLocaleString()}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
