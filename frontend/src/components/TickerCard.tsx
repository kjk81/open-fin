import type { TickerInfo } from "../types";
import { fmt, fmtCap } from "../utils";
import { Stat } from "./Stat";

export function TickerCard({ info }: { info: TickerInfo }) {
  const divYield = info.dividend_yield != null
    ? `${(info.dividend_yield * 100).toFixed(2)}%`
    : "—";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div>
        <div style={{ fontSize: "28px", fontWeight: 700, color: "var(--accent)" }}>
          {info.price != null ? `$${fmt(info.price)}` : "—"}
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: "13px" }}>{info.name ?? info.symbol}</div>
        {info.sector && (
          <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>
            {info.sector}{info.industry ? ` · ${info.industry}` : ""}
          </div>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
        <Stat label="Market Cap" value={fmtCap(info.market_cap)} />
        <Stat label="P/E (TTM)" value={fmt(info.pe_ratio)} />
        <Stat label="Forward P/E" value={fmt(info.forward_pe)} />
        <Stat label="Beta" value={fmt(info.beta)} />
        <Stat label="52W High" value={info.fifty_two_week_high != null ? `$${fmt(info.fifty_two_week_high)}` : "—"} />
        <Stat label="52W Low" value={info.fifty_two_week_low != null ? `$${fmt(info.fifty_two_week_low)}` : "—"} />
        <Stat label="Div. Yield" value={divYield} />
      </div>
    </div>
  );
}
