import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { initApiBase } from "./api";

// Resolve the backend port from Electron IPC before rendering the app.
// In plain-browser / test environments this is a no-op that resolves immediately.
initApiBase().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
