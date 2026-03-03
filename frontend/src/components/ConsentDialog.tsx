import { useState } from "react";
import type { ConsentProposal } from "../types";
import { confirmMemoryProposal } from "../api";
import { useAppContext } from "../context/AppContext";

interface Props {
  proposal: ConsentProposal;
}

function formatExpiry(isoString: string): string {
  try {
    return new Date(isoString).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return isoString;
  }
}

export function ConsentDialog({ proposal }: Props) {
  const { clearConsentProposal } = useAppContext();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDecision = async (decision: "confirm" | "discard") => {
    setLoading(true);
    setError(null);
    try {
      await confirmMemoryProposal(proposal.proposal_id, decision);
      clearConsentProposal();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={() => !loading && handleDecision("discard")}>
      <div className="modal-card consent-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Memory Persistence Approval</div>

        <div className="consent-body">
          <p className="consent-description">
            The agent wants to save{" "}
            <strong>{proposal.tool_result_count}</strong> research result(s) and{" "}
            <strong>{proposal.source_count}</strong> source(s) to long-term memory.
          </p>

          <div className="consent-detail-row">
            <span className="consent-label">Reason</span>
            <span className="consent-value">{proposal.reason.replace(/_/g, " ")}</span>
          </div>
          <div className="consent-detail-row">
            <span className="consent-label">Expires</span>
            <span className="consent-value consent-expiry">at {formatExpiry(proposal.expires_at)}</span>
          </div>

          {error && <div className="consent-error">{error}</div>}
        </div>

        <div className="modal-actions">
          <button
            className="btn-ghost"
            onClick={() => handleDecision("discard")}
            disabled={loading}
          >
            Discard
          </button>
          <button
            className="btn-send"
            onClick={() => handleDecision("confirm")}
            disabled={loading}
          >
            {loading ? "Saving…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
