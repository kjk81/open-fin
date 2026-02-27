import { app, BrowserWindow, ipcMain } from "electron";
import { execSync, spawn, ChildProcess } from "child_process";
import * as path from "path";
import * as fs from "fs";

const BACKEND_DIR = path.join(app.getAppPath(), "..", "backend");
const VENV_DIR = path.join(BACKEND_DIR, ".venv");
const IS_WIN = process.platform === "win32";
const VENV_PYTHON = path.join(VENV_DIR, IS_WIN ? "Scripts/python.exe" : "bin/python");
const VENV_PIP = path.join(VENV_DIR, IS_WIN ? "Scripts/pip.exe" : "bin/pip");
const VENV_UVICORN = path.join(VENV_DIR, IS_WIN ? "Scripts/uvicorn.exe" : "bin/uvicorn");

let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

function ensureVenv(): void {
  if (!fs.existsSync(VENV_DIR)) {
    console.log("[Electron] Creating Python venv at", VENV_DIR);
    execSync("python -m venv .venv", { cwd: BACKEND_DIR, stdio: "inherit" });
    console.log("[Electron] Installing backend dependencies...");
    execSync(`"${VENV_PIP}" install -r requirements.txt`, {
      cwd: BACKEND_DIR,
      stdio: "inherit",
    });
    console.log("[Electron] Backend dependencies installed.");
  } else {
    console.log("[Electron] Venv already exists, skipping install.");
  }
}

function startBackend(): void {
  if (backendProcess) return;

  ensureVenv();

  console.log("[Electron] Starting uvicorn...");
  backendProcess = spawn(VENV_UVICORN, ["main:app", "--port", "8000"], {
    cwd: BACKEND_DIR,
    stdio: "pipe",
  });

  backendProcess.stdout?.on("data", (data: Buffer) => {
    process.stdout.write(`[backend] ${data}`);
  });
  backendProcess.stderr?.on("data", (data: Buffer) => {
    process.stderr.write(`[backend] ${data}`);
  });

  backendProcess.on("exit", (code) => {
    console.log(`[Electron] Backend exited with code ${code}`);
    backendProcess = null;
  });
}

function stopBackend(): void {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    titleBarStyle: "hidden",
    backgroundColor: "#0f172a",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // In dev, load from Vite dev server; in production, load built file
  const devUrl = "http://localhost:5173";
  const prodFile = path.join(__dirname, "..", "renderer", "index.html");

  if (fs.existsSync(prodFile) && !process.argv.includes("--dev")) {
    mainWindow.loadFile(prodFile);
  } else {
    mainWindow.loadURL(devUrl);
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// IPC handlers
ipcMain.handle("get-backend-status", () => ({
  running: backendProcess !== null && !backendProcess.killed,
}));

ipcMain.handle("stop-backend", () => {
  stopBackend();
  return { stopped: true };
});

// App lifecycle
app.whenReady().then(() => {
  startBackend();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});
