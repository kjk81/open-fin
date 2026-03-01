/**
 * ErrorBoundary — catches unhandled React render errors so a crash in one
 * panel does not unmount the entire application shell.
 *
 * Usage (wrapping the whole layout):
 *   <ErrorBoundary>
 *     <Layout />
 *   </ErrorBoundary>
 *
 * Usage (wrapping one panel for granular isolation):
 *   <ErrorBoundary label="Ticker Dashboard">
 *     <TickerDashboard />
 *   </ErrorBoundary>
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  children: ReactNode;
  /** Optional label shown in the error card header (e.g. "Ticker Dashboard"). */
  label?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  componentStack: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, componentStack: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, componentStack: null };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? null });
    // Surface in the browser console for debugging without crashing the shell
    console.error("[ErrorBoundary] Uncaught render error:", error, info);
  }

  private handleReload = (): void => {
    // Full page reload — safest recovery for Electron + React apps
    window.location.reload();
  };

  private handleDismiss = (): void => {
    this.setState({ hasError: false, error: null, componentStack: null });
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const { label = "Panel" } = this.props;
    const { error, componentStack } = this.state;

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          minHeight: "200px",
          padding: "24px",
          gap: "16px",
        }}
      >
        <div
          style={{
            width: "100%",
            maxWidth: "540px",
            background: "var(--surface)",
            border: "1px solid var(--red)",
            borderRadius: "8px",
            padding: "20px 24px",
          }}
        >
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "12px",
            }}
          >
            <span style={{ color: "var(--red)", fontSize: "16px" }}>⚠</span>
            <span
              style={{
                color: "var(--text)",
                fontWeight: 600,
                fontSize: "14px",
              }}
            >
              {label} crashed
            </span>
          </div>

          {/* Error message */}
          <p
            style={{
              color: "var(--text-muted)",
              fontSize: "13px",
              lineHeight: 1.6,
              marginBottom: "8px",
            }}
          >
            An unexpected error occurred while rendering this panel. This is
            likely caused by malformed data or a missing API key.
          </p>

          {error && (
            <pre
              style={{
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                padding: "10px 12px",
                fontSize: "11px",
                fontFamily: "monospace",
                color: "var(--red)",
                overflowX: "auto",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                marginBottom: "16px",
                maxHeight: "120px",
                overflowY: "auto",
              }}
            >
              {error.message}
              {componentStack && (
                <span style={{ color: "var(--text-muted)", display: "block", marginTop: "6px" }}>
                  {componentStack.slice(0, 400)}
                  {componentStack.length > 400 ? "…" : ""}
                </span>
              )}
            </pre>
          )}

          {/* Actions */}
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              onClick={this.handleDismiss}
              style={{
                padding: "6px 14px",
                fontSize: "12px",
                background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                color: "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              Dismiss
            </button>
            <button
              onClick={this.handleReload}
              style={{
                padding: "6px 14px",
                fontSize: "12px",
                background: "var(--accent)",
                border: "none",
                borderRadius: "4px",
                color: "#fff",
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              Reload app
            </button>
          </div>
        </div>
      </div>
    );
  }
}
