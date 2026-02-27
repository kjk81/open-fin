export function WorkerStatusBadge({ online }: { online: boolean }) {
  const color = online ? "var(--green)" : "var(--text-muted)";
  const label = online ? "Worker Online" : "Worker Offline";

  return (
    <span style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" }} />
      {label}
    </span>
  );
}
