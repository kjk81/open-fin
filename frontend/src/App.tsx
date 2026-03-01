import { useState, useEffect, useCallback } from "react";
import { AppProvider, useAppContext } from "./context/AppContext";
import { AgentTerminal } from "./components/AgentTerminal";
import { StatusBadge } from "./components/StatusBadge";
import { Spinner } from "./components/Spinner";
import { PortfolioSidebar } from "./components/PortfolioSidebar";
import { ChatBox } from "./components/ChatBox";
import { TickerDashboard } from "./components/TickerDashboard";
import { KnowledgeGraphExplorer } from "./components/kg/KnowledgeGraphExplorer";
import { WorkerStatusBadge } from "./components/WorkerStatusBadge";
import { LoadoutsPanel } from "./components/LoadoutsPanel";
import { SettingsPage } from "./components/SettingsPage";
import { SettingsGearButton } from "./components/SettingsGearButton";
import { TitleBar } from "./components/TitleBar";
import { MigrationErrorModal } from "./components/MigrationErrorModal";

export default function App() {
  return (
    <AppProvider>
      <TitleBar />
      <Layout />
    </AppProvider>
  );
}

type Tab = "copilot" | "kg" | "loadouts" | "settings";

function Layout() {
  const { state, toggleTerminal } = useAppContext();
  const { backendStatus, workerOnline } = state;
  const [activeTab, setActiveTab] = useState<Tab>("copilot");

  // Ctrl+` toggles the agent terminal (VS Code convention)
  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "`") {
        e.preventDefault();
        toggleTerminal();
      }
    },
    [toggleTerminal]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  return (
    <div
      className="app-layout"
      style={state.terminalOpen ? { gridTemplateRows: "auto 1fr auto" } : undefined}
    >
      {/* Header — spans all columns */}
      <header className="app-header">
        <nav className="tab-bar" style={{ marginLeft: 0 }}>
          <button
            className={`tab${activeTab === "copilot" ? " active" : ""}`}
            onClick={() => setActiveTab("copilot")}
          >
            Co-Pilot
          </button>
          <button
            className={`tab${activeTab === "kg" ? " active" : ""}`}
            onClick={() => setActiveTab("kg")}
          >
            Knowledge Graph
          </button>
          <button
            className={`tab${activeTab === "loadouts" ? " active" : ""}`}
            onClick={() => setActiveTab("loadouts")}
          >
            Loadouts
          </button>
        </nav>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto" }}>
          <WorkerStatusBadge online={workerOnline} />
          <StatusBadge status={backendStatus} />
          <button
            onClick={toggleTerminal}
            title="Toggle agent terminal (Ctrl+`)"
            style={{
              background: state.terminalOpen ? "var(--accent)" : "transparent",
              border: "1px solid var(--border)",
              borderRadius: "4px",
              color: state.terminalOpen ? "#fff" : "var(--text-muted)",
              padding: "3px 8px",
              fontSize: "11px",
              fontFamily: "monospace",
              cursor: "pointer",
              WebkitAppRegion: "no-drag",
            } as React.CSSProperties}
          >
            &gt;_ Terminal
          </button>
          <SettingsGearButton onClick={() => setActiveTab("settings")} />
        </div>
      </header>

      {/* Migration error modal — shown above everything */}
      {backendStatus === "migration_error" && (
        <MigrationErrorModal error={state.migrationError} />
      )}

      {/* Connecting overlay */}
      {backendStatus !== "running" && backendStatus !== "migration_error" ? (
        <div
          style={{
            gridColumn: "1 / -1",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "12px",
            color: "var(--text-muted)",
            fontSize: "14px",
          }}
        >
          {backendStatus === "connecting" ? (
            <>
              <Spinner size={20} />
              Waiting for backend to start...
            </>
          ) : (
            <span style={{ color: "var(--red)" }}>
              Backend failed to start. Check the console.
            </span>
          )}
        </div>
      ) : backendStatus === "migration_error" ? null
      : activeTab === "copilot" ? (
        <>
          <PortfolioSidebar onOpenSettings={() => setActiveTab("settings")} />
          <ChatBox />
          <TickerDashboard />
          {state.terminalOpen && (
            <div style={{ gridColumn: "1 / -1" }}>
              <AgentTerminal />
            </div>
          )}
        </>
      ) : activeTab === "kg" ? (
        <KnowledgeGraphExplorer />
      ) : activeTab === "settings" ? (
        <SettingsPage onBack={() => setActiveTab("copilot")} />
      ) : (
        <LoadoutsPanel />
      )}
    </div>
  );
}
