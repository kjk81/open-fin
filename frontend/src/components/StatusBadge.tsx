import type { BackendStatus } from "../types";

const CONFIG: Record<BackendStatus, { color: string; label: string }> = {
  connecting: { color: "var(--yellow)", label: "Connecting..." },
  running: { color: "var(--green)", label: "Backend Running" },
  error: { color: "var(--red)", label: "Backend Error" },
};

export function StatusBadge({ status }: { status: BackendStatus }) {
  const { color, label } = CONFIG[status];
  return (
    <span style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" }} />
      {label}
    </span>
  );
}
