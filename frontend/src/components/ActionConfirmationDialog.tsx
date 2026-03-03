import { useState } from "react";
import type { ActionPreviewEvent, UnconfirmedAction } from "../types";

interface Props {
  preview: ActionPreviewEvent;
  onConfirm: (confirmedActionIds: string[]) => void;
  onDismiss: () => void;
}

function ActionPreviewRow({
  action,
  checked,
  onToggle,
  disabled,
}: {
  action: UnconfirmedAction;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  return (
    <label className={`action-preview-row${checked ? " action-preview-row--checked" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} disabled={disabled} />
      <div className="action-preview-content">
        <div className="action-preview-header">
          <span className={`action-category-badge action-category-badge--${action.category.toLowerCase()}`}>
            {action.category.replace(/_/g, " ")}
          </span>
          <span className="action-preview-tool">{action.tool}</span>
        </div>
        <div className="action-preview-delta">{action.delta_preview}</div>
        {Object.keys(action.args).length > 0 && (
          <div className="action-preview-args">
            {Object.entries(action.args).map(([k, v]) => (
              <span key={k} className="action-arg-chip">
                {k}: {JSON.stringify(v)}
              </span>
            ))}
          </div>
        )}
        {action.justification_citations.length > 0 && (
          <div className="action-preview-citations">
            {action.justification_citations.map((ref) => (
              <span key={ref} className="action-citation-tag">{ref}</span>
            ))}
          </div>
        )}
      </div>
    </label>
  );
}

export function ActionConfirmationDialog({ preview, onConfirm, onDismiss }: Props) {
  const [checkedIds, setCheckedIds] = useState<Set<string>>(
    () => new Set(preview.unconfirmed_actions.map((a) => a.action_id)),
  );
  const [confirming, setConfirming] = useState(false);

  const toggle = (id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleConfirm = () => {
    setConfirming(true);
    onConfirm([...checkedIds]);
  };

  return (
    <div className="modal-overlay" onClick={() => !confirming && onDismiss()}>
      <div className="modal-card action-confirmation-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Action Confirmation Required</div>

        <p className="action-confirm-description">
          The agent wants to perform the following write operation(s). Review carefully before confirming.
        </p>

        <div className="action-confirm-list">
          {preview.unconfirmed_actions.map((action) => (
            <ActionPreviewRow
              key={action.action_id}
              action={action}
              checked={checkedIds.has(action.action_id)}
              onToggle={() => toggle(action.action_id)}
              disabled={confirming}
            />
          ))}
        </div>

        <div className="modal-actions">
          <button className="btn-ghost" onClick={onDismiss} disabled={confirming}>
            Dismiss
          </button>
          <button
            className="btn-send"
            onClick={handleConfirm}
            disabled={confirming || checkedIds.size === 0}
          >
            {confirming ? "Executing…" : `Confirm ${checkedIds.size} Action(s)`}
          </button>
        </div>
      </div>
    </div>
  );
}
