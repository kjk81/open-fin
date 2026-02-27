import { useState } from "react";
import { AppProvider, useAppContext } from "./context/AppContext";
import { StatusBadge } from "./components/StatusBadge";
import { Spinner } from "./components/Spinner";
import { PortfolioSidebar } from "./components/PortfolioSidebar";
import { ChatBox } from "./components/ChatBox";
import { TickerDashboard } from "./components/TickerDashboard";
import { LlmSettingsPanel } from "./components/LlmSettingsPanel";
import { KnowledgeGraphExplorer } from "./components/kg/KnowledgeGraphExplorer";

export default function App() {
  return (
    <AppProvider>
      <Layout />
    </AppProvider>
  );
}

type Tab = "copilot" | "kg";

function Layout() {
  const { state } = useAppContext();
  const { backendStatus } = state;
  const [activeTab, setActiveTab] = useState<Tab>("copilot");

  return (
    <div className="app-layout">
      {/* Header — spans all columns */}
      <header className="app-header">
        <h1 style={{ fontSize: "18px", fontWeight: 700, letterSpacing: "-0.02em" }}>
          Open-Fin
        </h1>
        <nav className="tab-bar">
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
        </nav>
        <LlmSettingsPanel />
        <StatusBadge status={backendStatus} />
      </header>

      {/* Connecting overlay */}
      {backendStatus !== "running" ? (
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
      ) : activeTab === "copilot" ? (
        <>
          <PortfolioSidebar />
          <ChatBox />
          <TickerDashboard />
        </>
      ) : (
        <KnowledgeGraphExplorer />
      )}
    </div>
  );
}
