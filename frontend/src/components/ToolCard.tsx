import type { ToolCardMessage, ToolResultEnvelope } from "../types";

interface Props {
  card: ToolCardMessage;
  citationIndex: number;
  anchorId: string;
  highlighted?: boolean;
}

type ToolCardKind = "price_quote" | "filing_excerpt" | "kg_subgraph" | "portfolio_snapshot" | "generic";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function detectKind(card: ToolCardMessage): ToolCardKind {
  const tool = card.tool.toLowerCase();
  const envelope = card.resultEnvelope;
  const data = asRecord(envelope?.data);

  if (tool.includes("quote") || (typeof data.price === "number" && typeof data.symbol === "string")) {
    return "price_quote";
  }
  if (tool.includes("filing") || typeof data.excerpt === "string" || Array.isArray(data.filings)) {
    return "filing_excerpt";
  }
  if (tool.includes("subgraph") || (Array.isArray(data.nodes) && Array.isArray(data.edges))) {
    return "kg_subgraph";
  }
  if (tool.includes("portfolio") || Array.isArray(data.positions)) {
    return "portfolio_snapshot";
  }
  return "generic";
}

function formatTimestamp(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function renderPriceQuote(data: Record<string, unknown>) {
  const symbol = typeof data.symbol === "string" ? data.symbol : "—";
  const price = typeof data.price === "number" ? data.price.toFixed(2) : "—";
  const changePct = typeof data.change_percent === "number" ? `${data.change_percent.toFixed(2)}%` : undefined;
  return (
    <div className="tool-card-body">
      <div className="tool-card-row"><span>Symbol</span><strong>{symbol}</strong></div>
      <div className="tool-card-row"><span>Price</span><strong>{price}</strong></div>
      {changePct && <div className="tool-card-row"><span>Change</span><strong>{changePct}</strong></div>}
    </div>
  );
}

function renderFilingExcerpt(data: Record<string, unknown>) {
  const excerpt = typeof data.excerpt === "string"
    ? data.excerpt
    : typeof data.summary === "string"
      ? data.summary
      : "No excerpt available.";
  const formType = typeof data.form_type === "string" ? data.form_type : undefined;
  const filingDate = typeof data.filing_date === "string" ? data.filing_date : undefined;

  return (
    <div className="tool-card-body">
      {(formType || filingDate) && (
        <div className="tool-card-row">
          <span>Filing</span>
          <strong>{[formType, filingDate].filter(Boolean).join(" · ") || "—"}</strong>
        </div>
      )}
      <p className="tool-card-text">{excerpt}</p>
    </div>
  );
}

function renderKgSubgraph(data: Record<string, unknown>) {
  const nodes = asArray(data.nodes).length;
  const edges = asArray(data.edges).length;
  const center = typeof data.center === "string" ? data.center : undefined;
  return (
    <div className="tool-card-body">
      <div className="tool-card-row"><span>Nodes</span><strong>{nodes}</strong></div>
      <div className="tool-card-row"><span>Edges</span><strong>{edges}</strong></div>
      {center && <div className="tool-card-row"><span>Center</span><strong>{center}</strong></div>}
    </div>
  );
}

function renderPortfolioSnapshot(data: Record<string, unknown>) {
  const positions = asArray(data.positions);
  const totalValue = typeof data.total_value === "number" ? data.total_value.toFixed(2) : undefined;
  const cash = typeof data.cash === "number" ? data.cash.toFixed(2) : undefined;
  return (
    <div className="tool-card-body">
      <div className="tool-card-row"><span>Positions</span><strong>{positions.length}</strong></div>
      {totalValue && <div className="tool-card-row"><span>Total Value</span><strong>{totalValue}</strong></div>}
      {cash && <div className="tool-card-row"><span>Cash</span><strong>{cash}</strong></div>}
    </div>
  );
}

function renderGeneric(envelope?: ToolResultEnvelope) {
  const data = envelope?.data;
  if (data == null) {
    return <div className="tool-card-body"><p className="tool-card-text">No structured payload available.</p></div>;
  }
  const asText = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <div className="tool-card-body">
      <pre className="tool-card-json">{asText}</pre>
    </div>
  );
}

function renderKind(card: ToolCardMessage) {
  const envelope = card.resultEnvelope;
  const data = asRecord(envelope?.data);
  switch (detectKind(card)) {
    case "price_quote":
      return renderPriceQuote(data);
    case "filing_excerpt":
      return renderFilingExcerpt(data);
    case "kg_subgraph":
      return renderKgSubgraph(data);
    case "portfolio_snapshot":
      return renderPortfolioSnapshot(data);
    default:
      return renderGeneric(envelope);
  }
}

export function ToolCard({ card, citationIndex, anchorId, highlighted = false }: Props) {
  const envelope = card.resultEnvelope;
  const provenance = envelope?.provenance;
  const sources = envelope?.sources ?? [];
  const primarySource = typeof provenance?.source === "string" ? provenance.source : "unknown";
  const retrievedAt = formatTimestamp(provenance?.retrieved_at);
  const fetchedAt = sources
    .map((src) => (typeof src.fetched_at === "string" ? src.fetched_at : ""))
    .find(Boolean);

  return (
    <article
      className={`tool-card ${highlighted ? "tool-card--highlight" : ""}`.trim()}
      data-citation-index={citationIndex}
      id={anchorId}
    >
      <header className="tool-card-header">
        <div className="tool-card-title">[{citationIndex}] {card.tool.replaceAll("_", " ")}</div>
        <div className={`tool-card-status tool-card-status--${card.status}`}>{card.status}</div>
      </header>
      {renderKind(card)}
      <footer className="tool-card-meta">
        <span>Source: {primarySource}</span>
        <span>Retrieved: {retrievedAt}</span>
        <span>Fetched: {formatTimestamp(fetchedAt)}</span>
      </footer>
    </article>
  );
}
