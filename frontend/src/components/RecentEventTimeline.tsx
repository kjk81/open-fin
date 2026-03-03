import { useEffect, useState } from "react";

import { fetchTickerEvents } from "../api";
import type { SentimentSnapshot, TickerEventItem } from "../types";

const BIAS_COLOR: Record<string, string> = {
  Bullish: "#4ade80",
  Bearish: "#f87171",
  Neutral: "#94a3b8",
  Mixed: "#fb923c",
};

function SentimentCard({ s }: { s: SentimentSnapshot }) {
  const color = BIAS_COLOR[s.overall_bias] ?? "#94a3b8";
  return (
    <div className="sentiment-card">
      <div className="sentiment-card-header">
        <span className="sentiment-bias" style={{ color }}>
          {s.overall_bias}
        </span>
        <span className="sentiment-confidence">Confidence: {s.confidence}</span>
      </div>
      <p className="sentiment-opinion">{s.majority_opinion}</p>
      {s.key_catalysts.length > 0 && (
        <ul className="sentiment-catalysts">
          {s.key_catalysts.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
      <div className="sentiment-sources">
        {s.reddit_summary && (
          <p><strong>Reddit:</strong> {s.reddit_summary}</p>
        )}
        {s.twitter_summary && (
          <p><strong>Twitter/X:</strong> {s.twitter_summary}</p>
        )}
      </div>
    </div>
  );
}

export function RecentEventTimeline({ symbol }: { symbol: string }) {
  const [sentiment, setSentiment] = useState<SentimentSnapshot | null>(null);
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
          setSentiment(data.sentiment);
          setItems(data.events);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setSentiment(null);
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
      {!loading && !error && sentiment && <SentimentCard s={sentiment} />}
      {!loading && !error && items.length === 0 && !sentiment && (
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
                {item.snippet && (
                  <p className="recent-events-snippet">{item.snippet}</p>
                )}
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
