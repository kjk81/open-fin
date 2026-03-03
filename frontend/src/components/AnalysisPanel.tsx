import type { TickerAnalysis, AnalysisSectionName, AnalysisSectionData } from "../types";

const SECTION_TITLES: Record<AnalysisSectionName, string> = {
  fundamentals: "Fundamentals",
  sentiment: "Sentiment",
  technical: "Technical",
};

const SECTION_ORDER: AnalysisSectionName[] = ["fundamentals", "sentiment", "technical"];

function ratingClass(rating: string): string {
  const r = rating.toLowerCase();
  const positive = ["strong", "bullish", "uptrend", "buy"];
  const negative = ["weak", "bearish", "downtrend", "sell"];
  if (positive.some((w) => r.includes(w))) return "rating-badge--positive";
  if (negative.some((w) => r.includes(w))) return "rating-badge--negative";
  return "rating-badge--neutral";
}

function AnalysisSectionCard({
  title,
  section,
}: {
  title: string;
  section: AnalysisSectionData | undefined;
}) {
  const isLoading = !section || (section.loading && !section.content);
  const hasContent = section && section.content;

  return (
    <div className="analysis-section">
      <div className="analysis-section-header">
        <span className="analysis-section-title">{title}</span>
        {section?.rating && (
          <span className={`rating-badge ${ratingClass(section.rating)}`}>
            {section.rating}
          </span>
        )}
      </div>
      <div className="analysis-section-content">
        {isLoading && (
          <div className="analysis-section-skeleton">
            <div className="skeleton-line" />
            <div className="skeleton-line skeleton-line--short" />
          </div>
        )}
        {hasContent && (
          <p style={{ margin: 0, fontSize: "13px", lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
            {section.content}
          </p>
        )}
        {section?.source && section.source !== "error" && hasContent && (
          <span
            style={{
              display: "inline-block",
              marginTop: "6px",
              fontSize: "10px",
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            source: {section.source}
          </span>
        )}
      </div>
    </div>
  );
}

export function AnalysisPanel({ analysis }: { analysis: TickerAnalysis }) {
  return (
    <div className="analysis-panel">
      {analysis.overallRating && (
        <div style={{ marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            style={{
              fontSize: "11px",
              fontWeight: 600,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Overall
          </span>
          <span className={`rating-badge ${ratingClass(analysis.overallRating)}`}>
            {analysis.overallRating}
          </span>
        </div>
      )}

      {SECTION_ORDER.map((name) => (
        <AnalysisSectionCard
          key={name}
          title={SECTION_TITLES[name]}
          section={
            analysis.sections[name] ??
            (analysis.loading ? { content: "", rating: "", source: "", loading: true } : undefined)
          }
        />
      ))}

      {analysis.error && (
        <div style={{ color: "var(--red)", fontSize: "13px", marginTop: "8px" }}>
          {analysis.error}
        </div>
      )}
    </div>
  );
}
