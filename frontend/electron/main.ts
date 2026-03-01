import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { execSync, spawn, ChildProcess } from "child_process";
import { createHash } from "crypto";
import * as path from "path";
import * as fs from "fs";
import * as http from "http";
import * as net from "net";

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
    fastembedCache: path.join(base, "fastembed_cache"),
  };
}

/** Shared environment variables passed to both backend and worker processes. */
function backendEnv(): NodeJS.ProcessEnv {
  const { dbPath, kgPath, envPath, faissDir, fastembedCache } = userDataPaths();
  return {
    ...process.env,
    OPEN_FIN_DB_PATH: dbPath,
    OPEN_FIN_KG_PATH: kgPath,
    OPEN_FIN_ENV_PATH: envPath,
    OPEN_FIN_FAISS_DIR: faissDir,
    // FastEmbed model cache — keeps downloads out of Windows system temp
    // directories that may require elevated symlink privileges.
    FASTEMBED_CACHE_PATH: fastembedCache,
    // Suppress the huggingface_hub symlink warning on Windows.
    HF_HUB_DISABLE_SYMLINKS_WARNING: "1",
    // Preferred API port; backend will increment if occupied.
    OPEN_FIN_PREFERRED_PORT: String(PREFERRED_PORT),
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
  const { pip, python } = venvBinaries(venvDir);

  const requirementsPath = path.join(BACKEND_DIR, "requirements.txt");
  const markerPath = path.join(venvDir, "requirements.sha256");

  if (!fs.existsSync(BACKEND_DIR) || !fs.existsSync(requirementsPath)) {
    throw new Error(`Backend not found at ${BACKEND_DIR}`);
  }

  if (!fs.existsSync(venvDir) || !fs.existsSync(pip) || !fs.existsSync(python)) {
    if (fs.existsSync(venvDir)) {
      console.log("[Electron] Venv is incomplete (missing binaries). Recreating...");
      fs.rmSync(venvDir, { recursive: true, force: true });
    }
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
let venvReady = false;

/** The TCP port the backend process is actually listening on. */
let backendPort: number = PREFERRED_PORT;

/** Default preferred port — overridden by the backend's stdout sentinel. */
const PREFERRED_PORT = 8000;

/**
 * Probe TCP ports starting at *preferred* and return the first one that is
 * not currently bound on the loopback interface.  Tries up to 10 candidates
 * before throwing.
 */
function findFreePort(preferred: number): Promise<number> {
  return new Promise((resolve, reject) => {
    let candidate = preferred;
    const MAX = preferred + 10;

    function tryNext(): void {
      if (candidate >= MAX) {
        reject(
          new Error(
            `All ports ${preferred}–${MAX - 1} are in use. ` +
              "Kill leftover Open-Fin processes and restart.",
          ),
        );
        return;
      }
      const srv = net.createServer();
      srv.once("error", () => { candidate++; tryNext(); });
      srv.listen(candidate, "127.0.0.1", () => {
        srv.close(() => resolve(candidate));
      });
    }
    tryNext();
  });
}

// ---------------------------------------------------------------------------
// Windows orphan-process cleanup
// ---------------------------------------------------------------------------

/**
 * Kill any process currently listening on *port* on the loopback interface.
 * Uses `netstat + taskkill` on Windows, `lsof + kill` elsewhere.
 * Swallows all errors so that a missing process is not fatal.
 */
function killPortProcess(port: number): void {
  try {
    if (IS_WIN) {
      // netstat -ano output includes lines like:
      //   TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    <PID>
      const out = execSync(
        `netstat -ano | findstr :${port} | findstr LISTENING`,
        { encoding: "utf-8" },
      );
      const pids = new Set(
        out
          .split(/\r?\n/)
          .map((line) => line.trim().split(/\s+/).pop())
          .filter((p): p is string => Boolean(p) && /^\d+$/.test(p)),
      );
      for (const pid of pids) {
        console.log(`[Electron] Killing orphaned process on port ${port} (PID ${pid})`);
        execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
      }
    } else {
      const pid = execSync(`lsof -ti :${port}`, { encoding: "utf-8" }).trim();
      if (pid) {
        console.log(`[Electron] Killing orphaned process on port ${port} (PID ${pid})`);
        execSync(`kill -9 ${pid}`, { stdio: "ignore" });
      }
    }
  } catch {
    // No process on that port, or the command failed — either way we proceed.
  }
}

function pipeOutput(child: ChildProcess, label: string): void {
  child.stdout?.on("data", (data: Buffer) => {
    process.stdout.write(`[${label}] ${data}`);
  });
  child.stderr?.on("data", (data: Buffer) => {
    process.stderr.write(`[${label}] ${data}`);
  });
}

async function startBackend(): Promise<void> {
  if (backendProcess) return;

  // Kill any stale process that may be holding the preferred port from a
  // previous crashed instance before we spawn a fresh one.
  killPortProcess(PREFERRED_PORT);

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
      venvReady = true;
    } catch (err) {
      console.error("[Electron] Failed to prepare backend:", err);
      return;
    }

    // Determine the actual free port before spawning so we can tell uvicorn
    // exactly which port to bind (avoids the chicken-and-egg problem of
    // parsing the port from uvicorn's log output).
    let devPort: number;
    try {
      devPort = await findFreePort(PREFERRED_PORT);
    } catch (err) {
      console.error("[Electron] No free port available:", err);
      dialog.showErrorBox(
        "Open-Fin — Port Unavailable",
        String(err),
      );
      app.quit();
      return;
    }
    backendPort = devPort;
    console.log(`[Electron] Starting uvicorn (dev) on port ${devPort}...`);

    backendProcess = spawn(
      python,
      ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port",
        String(devPort)],
      { cwd: BACKEND_DIR, stdio: "pipe", env },
    );
  }

  // Parse the machine-readable port sentinel emitted by entry_api.py
  // (frozen mode only; dev mode sets backendPort synchronously above).
  backendProcess.stdout?.on("data", (data: Buffer) => {
    const text = data.toString();
    const match = text.match(/OPEN_FIN_PORT=(\d+)/);
    if (match) {
      backendPort = parseInt(match[1], 10);
      console.log(`[Electron] Backend selected port ${backendPort}`);
    }
    process.stdout.write(`[backend] ${text}`);
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

    if (!venvReady) {
      console.error("[Electron] Skipping worker start: venv is not ready.");
      return;
    }

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

/**
 * Poll GET http://127.0.0.1:<port>/api/health every 500 ms until the server
 * responds with HTTP 200, or until *timeoutMs* elapses.
 *
 * Resolves with true on success, false on timeout.
 */
function waitForBackend(
  port: number,
  timeoutMs = 60_000,
): Promise<boolean> {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    const INTERVAL = 500;

    function probe(): void {
      if (Date.now() > deadline) {
        resolve(false);
        return;
      }
      const req = http.get(
        `http://127.0.0.1:${port}/api/health`,
        (res) => {
          if (res.statusCode === 200) {
            resolve(true);
          } else {
            setTimeout(probe, INTERVAL);
          }
          res.resume();
        },
      );
      req.on("error", () => setTimeout(probe, INTERVAL));
      req.setTimeout(400, () => { req.destroy(); setTimeout(probe, INTERVAL); });
    }
    setTimeout(probe, INTERVAL);
  });
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

ipcMain.handle("get-backend-port", () => backendPort);

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  await startBackend();

  // Wait for the backend to become responsive before opening the window and
  // starting the worker.  This prevents the renderer from hitting a blank API
  // and the worker from racing against an unavailable DB.
  const ready = await waitForBackend(backendPort);
  if (!ready) {
    dialog.showErrorBox(
      "Open-Fin — Backend Startup Failed",
      `The backend server did not become ready within 60 seconds on port ${backendPort}.\n\n` +
        "Please check the developer console for errors and restart the application.",
    );
    app.quit();
    return;
  }

  console.log(`[Electron] Backend is ready on port ${backendPort}.`);
  startWorker();
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
