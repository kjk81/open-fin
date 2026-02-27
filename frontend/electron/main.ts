import { app, BrowserWindow, ipcMain } from "electron";
import { execSync, spawn, ChildProcess } from "child_process";
import { createHash } from "crypto";
import * as path from "path";
import * as fs from "fs";

function backendDir(): string {
  // In dev, app.getAppPath() points at frontend/.
  // In packaged, backend is copied into resources/backend via extraResources.
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend");
  }
  return path.join(app.getAppPath(), "..", "backend");
}

function userDataPaths() {
  const base = app.getPath("userData");
  return {
    venvDir: path.join(base, "python-venv"),
    dbPath: path.join(base, "open_fin.db"),
    kgPath: path.join(base, "open_fin_kg.json"),
    envPath: path.join(base, ".env"),
  };
}

function runtimePaths() {
  const BACKEND_DIR = backendDir();
  const { venvDir, dbPath, kgPath, envPath } = userDataPaths();
  return {
    BACKEND_DIR,
    VENV_DIR: venvDir,
    DB_PATH: dbPath,
    KG_PATH: kgPath,
    ENV_PATH: envPath,
  };
}

const IS_WIN = process.platform === "win32";

function venvBinaries(venvDir: string) {
  return {
    python: path.join(venvDir, IS_WIN ? "Scripts/python.exe" : "bin/python"),
    pip: path.join(venvDir, IS_WIN ? "Scripts/pip.exe" : "bin/pip"),
  };
}

let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

function sha256File(filePath: string): string {
  const buf = fs.readFileSync(filePath);
  return createHash("sha256").update(buf).digest("hex");
}

function ensureVenv(): void {
  const { BACKEND_DIR, VENV_DIR } = runtimePaths();
  const { pip } = venvBinaries(VENV_DIR);

  const requirementsPath = path.join(BACKEND_DIR, "requirements.txt");
  const markerPath = path.join(VENV_DIR, "requirements.sha256");

  if (!fs.existsSync(BACKEND_DIR) || !fs.existsSync(requirementsPath)) {
    throw new Error(`Backend not found at ${BACKEND_DIR}`);
  }

  if (!fs.existsSync(VENV_DIR)) {
    console.log("[Electron] Creating Python venv at", VENV_DIR);
    fs.mkdirSync(VENV_DIR, { recursive: true });
    execSync(`python -m venv "${VENV_DIR}"`, { cwd: BACKEND_DIR, stdio: "inherit" });
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

function startBackend(): void {
  if (backendProcess) return;

  const { BACKEND_DIR, VENV_DIR, DB_PATH, KG_PATH, ENV_PATH } = runtimePaths();
  const { python } = venvBinaries(VENV_DIR);

  try {
    ensureVenv();
  } catch (err) {
    console.error("[Electron] Failed to prepare backend:", err);
    return;
  }

  console.log("[Electron] Starting uvicorn...");
  backendProcess = spawn(
    python,
    ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
    {
      cwd: BACKEND_DIR,
      stdio: "pipe",
      env: {
        ...process.env,
        OPEN_FIN_DB_PATH: DB_PATH,
        OPEN_FIN_KG_PATH: KG_PATH,
        OPEN_FIN_ENV_PATH: ENV_PATH,
      },
    },
  );

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

  backendProcess.on("error", (err) => {
    console.error("[Electron] Backend process error:", err);
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
