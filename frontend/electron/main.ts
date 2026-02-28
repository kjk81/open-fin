import { app, BrowserWindow, ipcMain } from "electron";
import { execSync, spawn, ChildProcess } from "child_process";
import { createHash } from "crypto";
import * as path from "path";
import * as fs from "fs";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

const IS_WIN = process.platform === "win32";
const EXE_EXT = IS_WIN ? ".exe" : "";

function userDataPaths() {
  const base = app.getPath("userData");
  return {
    venvDir: path.join(base, "python-venv"),
    dbPath: path.join(base, "open_fin.db"),
    kgPath: path.join(base, "open_fin_kg.json"),
    envPath: path.join(base, ".env"),
    faissDir: path.join(base, "faiss_data"),
  };
}

/** Shared environment variables passed to both backend and worker processes. */
function backendEnv(): NodeJS.ProcessEnv {
  const { dbPath, kgPath, envPath, faissDir } = userDataPaths();
  return {
    ...process.env,
    OPEN_FIN_DB_PATH: dbPath,
    OPEN_FIN_KG_PATH: kgPath,
    OPEN_FIN_ENV_PATH: envPath,
    OPEN_FIN_FAISS_DIR: faissDir,
  };
}

// ---------------------------------------------------------------------------
// Dev-mode helpers (venv workflow — only used when !app.isPackaged)
// ---------------------------------------------------------------------------

function devBackendDir(): string {
  return path.join(app.getAppPath(), "..", "backend");
}

function venvBinaries(venvDir: string) {
  return {
    python: path.join(venvDir, IS_WIN ? "Scripts/python.exe" : "bin/python"),
    pip: path.join(venvDir, IS_WIN ? "Scripts/pip.exe" : "bin/pip"),
  };
}

function sha256File(filePath: string): string {
  const buf = fs.readFileSync(filePath);
  return createHash("sha256").update(buf).digest("hex");
}

function ensureVenv(): void {
  const BACKEND_DIR = devBackendDir();
  const { venvDir } = userDataPaths();
  const { pip } = venvBinaries(venvDir);

  const requirementsPath = path.join(BACKEND_DIR, "requirements.txt");
  const markerPath = path.join(venvDir, "requirements.sha256");

  if (!fs.existsSync(BACKEND_DIR) || !fs.existsSync(requirementsPath)) {
    throw new Error(`Backend not found at ${BACKEND_DIR}`);
  }

  if (!fs.existsSync(venvDir)) {
    console.log("[Electron] Creating Python venv at", venvDir);
    fs.mkdirSync(venvDir, { recursive: true });
    execSync(`python -m venv "${venvDir}"`, { cwd: BACKEND_DIR, stdio: "inherit" });
  }

  const reqHash = sha256File(requirementsPath);
  const prevHash = fs.existsSync(markerPath) ? fs.readFileSync(markerPath, "utf-8").trim() : "";
  if (reqHash !== prevHash) {
    console.log("[Electron] Installing/updating backend dependencies...");
    execSync(`"${pip}" install -r requirements.txt`, {
      cwd: BACKEND_DIR,
      stdio: "inherit",
    });
    fs.writeFileSync(markerPath, reqHash, "utf-8");
    console.log("[Electron] Backend dependencies installed.");
  } else {
    console.log("[Electron] Backend dependencies already up to date.");
  }
}

// ---------------------------------------------------------------------------
// Packaged-mode helpers (frozen PyInstaller binaries)
// ---------------------------------------------------------------------------

function frozenApiExe(): string {
  return path.join(
    process.resourcesPath,
    "backend",
    "api",
    "open-fin-api",
    `open-fin-api${EXE_EXT}`,
  );
}

function frozenWorkerExe(): string {
  return path.join(
    process.resourcesPath,
    "backend",
    "worker",
    "open-fin-worker",
    `open-fin-worker${EXE_EXT}`,
  );
}

// ---------------------------------------------------------------------------
// Process management
// ---------------------------------------------------------------------------

let backendProcess: ChildProcess | null = null;
let workerProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

function pipeOutput(child: ChildProcess, label: string): void {
  child.stdout?.on("data", (data: Buffer) => {
    process.stdout.write(`[${label}] ${data}`);
  });
  child.stderr?.on("data", (data: Buffer) => {
    process.stderr.write(`[${label}] ${data}`);
  });
}

function startBackend(): void {
  if (backendProcess) return;

  const env = backendEnv();

  if (app.isPackaged) {
    // ---- Packaged mode: spawn the frozen PyInstaller binary ----
    const exe = frozenApiExe();
    if (!fs.existsSync(exe)) {
      console.error(`[Electron] Frozen API binary not found: ${exe}`);
      return;
    }
    console.log("[Electron] Starting frozen API server...");
    backendProcess = spawn(exe, [], { stdio: "pipe", env });
  } else {
    // ---- Dev mode: use Python venv + uvicorn ----
    const BACKEND_DIR = devBackendDir();
    const { venvDir } = userDataPaths();
    const { python } = venvBinaries(venvDir);

    try {
      ensureVenv();
    } catch (err) {
      console.error("[Electron] Failed to prepare backend:", err);
      return;
    }

    console.log("[Electron] Starting uvicorn (dev)...");
    backendProcess = spawn(
      python,
      ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
      { cwd: BACKEND_DIR, stdio: "pipe", env },
    );
  }

  pipeOutput(backendProcess, "backend");

  backendProcess.on("exit", (code) => {
    console.log(`[Electron] Backend exited with code ${code}`);
    backendProcess = null;
  });

  backendProcess.on("error", (err) => {
    console.error("[Electron] Backend process error:", err);
  });
}

function startWorker(): void {
  if (workerProcess) return;

  const env = backendEnv();

  if (app.isPackaged) {
    // ---- Packaged mode: spawn the frozen PyInstaller binary ----
    const exe = frozenWorkerExe();
    if (!fs.existsSync(exe)) {
      console.error(`[Electron] Frozen worker binary not found: ${exe}`);
      return;
    }
    console.log("[Electron] Starting frozen worker...");
    workerProcess = spawn(exe, [], { stdio: "pipe", env });
  } else {
    // ---- Dev mode: use Python venv ----
    const BACKEND_DIR = devBackendDir();
    const { venvDir } = userDataPaths();
    const { python } = venvBinaries(venvDir);

    console.log("[Electron] Starting worker (dev)...");
    workerProcess = spawn(
      python,
      ["worker.py"],
      { cwd: BACKEND_DIR, stdio: "pipe", env },
    );
  }

  pipeOutput(workerProcess, "worker");

  workerProcess.on("exit", (code) => {
    console.log(`[Electron] Worker exited with code ${code}`);
    workerProcess = null;
  });

  workerProcess.on("error", (err) => {
    console.error("[Electron] Worker process error:", err);
  });
}

function stopBackend(): void {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

function stopWorker(): void {
  if (workerProcess) {
    workerProcess.kill();
    workerProcess = null;
  }
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

ipcMain.handle("get-backend-status", () => ({
  running: backendProcess !== null && !backendProcess.killed,
}));

ipcMain.handle("get-worker-status", () => ({
  running: workerProcess !== null && !workerProcess.killed,
}));

ipcMain.handle("stop-backend", () => {
  stopBackend();
  return { stopped: true };
});

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  startBackend();
  setTimeout(() => startWorker(), 5000);
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopWorker();
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});
