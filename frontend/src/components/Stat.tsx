export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: "var(--bg)", borderRadius: "8px", padding: "10px 12px" }}>
      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "2px" }}>{label}</div>
      <div style={{ fontWeight: 600, fontSize: "14px" }}>{value}</div>
    </div>
  );
}
