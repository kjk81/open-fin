import { useCallback, useEffect, useRef, useState } from "react";
import type { VerificationReport, VerificationWarning } from "../types";

interface Props {
  report: VerificationReport;
}

function warningLabel(w: VerificationWarning): string {
  switch (w.type) {
    case "source_disagreement_range":
      return `Sources disagree on "${w.claim_key}"${
        w.min_value != null && w.max_value != null
          ? ` (${w.min_value} – ${w.max_value}${w.spread_pct != null ? `, ${w.spread_pct.toFixed(1)}% spread` : ""})`
          : ""
      }`;
    case "missing_as_of":
      return `Missing as-of date for "${w.claim_key}"`;
    case "missing_unit_or_currency":
      return `Missing unit/currency for "${w.claim_key}"`;
    case "derived_calc_missing_inputs":
      return `Derived calc missing inputs for "${w.claim_key}"`;
    case "contradictory_trade_signal":
      return `Contradictory trade signal on "${w.claim_key}"`;
    case "core_fundamental_variance":
      return `Core fundamental variance on "${w.claim_key}"`;
    case "mandatory_data_fetch_failure":
      return `Required data fetch failed: "${w.claim_key}"`;
    default:
      return `${w.type.replace(/_/g, " ")}: "${w.claim_key}"`;
  }
}

export function VerificationBadge({ report }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const toggle = useCallback(() => setOpen((o) => !o), []);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  const isCritical = report.status === "critical";
  const allIssues = [...report.critical, ...report.warnings];

  return (
    <div className="verification-badge-wrap" ref={ref}>
      <button
        className={`verification-badge ${isCritical ? "verification-badge--critical" : "verification-badge--warning"}`}
        onClick={toggle}
        title={`Verification ${report.status}: ${allIssues.length} issue(s)`}
        aria-expanded={open}
      >
        {isCritical ? "⚠ Critical" : "⚠ Warning"}
        <span className="verification-badge-count">{allIssues.length}</span>
      </button>

      {open && (
        <div className="verification-popover" role="tooltip">
          <div className="verification-popover-header">
            {isCritical ? "Critical Verification Issues" : "Verification Warnings"}
          </div>
          {report.critical.length > 0 && (
            <ul className="verification-list verification-list--critical">
              {report.critical.map((w, i) => (
                <li key={i} className="verification-item">{warningLabel(w)}</li>
              ))}
            </ul>
          )}
          {report.warnings.length > 0 && (
            <ul className="verification-list">
              {report.warnings.map((w, i) => (
                <li key={i} className="verification-item">{warningLabel(w)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
