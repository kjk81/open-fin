import { useState } from "react";
import type { TradeOrder } from "../types";
import { executeTrade } from "../api";

interface Props {
  trade: TradeOrder;
  onClose: () => void;
  onSuccess: (trade: TradeOrder, orderId: string) => Promise<void>;
}

export function TradeTicket({ trade, onClose, onSuccess }: Props) {
  const [status, setStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const handleConfirm = async () => {
    setStatus("submitting");
    setErrorMsg("");
    try {
      const result = await executeTrade(trade);
      await onSuccess(trade, result.order_id);
    } catch (err) {
      setStatus("error");
      setErrorMsg(String(err));
    }
  };

  return (
    <div className="modal-overlay" onClick={() => status !== "submitting" && onClose()}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">Review Trade</h3>

        <div className="trade-detail">
          <div className="trade-detail-row">
            <span className="trade-label">Action</span>
            <span className={`trade-value trade-value--${trade.action.toLowerCase()}`}>
              {trade.action}
            </span>
          </div>
          <div className="trade-detail-row">
            <span className="trade-label">Ticker</span>
            <span className="trade-value">{trade.ticker}</span>
          </div>
          <div className="trade-detail-row">
            <span className="trade-label">Quantity</span>
            <span className="trade-value">{trade.qty}</span>
          </div>
          <div className="trade-detail-row">
            <span className="trade-label">Order Type</span>
            <span className="trade-value">Market</span>
          </div>
          <div className="trade-detail-row">
            <span className="trade-label">Account</span>
            <span className="trade-value">Paper Trading</span>
          </div>
        </div>

        {status === "error" && <div className="trade-error">{errorMsg}</div>}

        <div className="modal-actions">
          <button className="btn-ghost" onClick={onClose} disabled={status === "submitting"}>
            Cancel
          </button>
          <button
            className={`btn-send trade-confirm-btn trade-confirm-btn--${trade.action.toLowerCase()}`}
            onClick={handleConfirm}
            disabled={status === "submitting"}
          >
            {status === "submitting" ? "Executing..." : "Confirm Execution"}
          </button>
        </div>
      </div>
    </div>
  );
}
