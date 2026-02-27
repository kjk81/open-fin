### 1. Project Overview

A local, privacy-first desktop application functioning as a quantitative financial co-pilot. It uses a hybrid data retrieval architecture: relying on external APIs (`yfinance`, `alpaca`) for instant, raw quantitative data (prices, balance sheets), while leveraging a local Knowledge Graph and SQLite database to map market relationships and cache computationally expensive LLM reports. It strictly enforces a Human-In-The-Loop (HITL) workflow for trade execution.

This repo now includes:
- **Phase 5:** a dual-view Knowledge Graph Explorer (Sigma WebGL network + virtualized table) with progressive graph loading.
- **Phase 6:** an isolated Automated Loadouts worker (`worker.py`) for scheduled strategy execution with strict validation and audit logging.

### 2. Technology Stack

* **Frontend:** Electron, React (Vite), Tailwind CSS.
* **Backend:** Python, FastAPI, Uvicorn.
* **Database (Local):** SQLite (for settings, portfolio state, and cached text reports), NetworkX/Neo4j (for the Knowledge Graph relationships).
* **AI/Agent Core:** LangGraph, LangChain, Ollama (or selected cloud API with auto fallback (at least OpenRouter, Gemini, Groq, Hugging Face) for inference).
* **Data Providers:** Alpaca SDK (paper trading/portfolio sync), `yfinance` (instant historical and fundamental market data).
* **Graph UI:** `@react-sigma/core`, `@react-sigma/layout-forceatlas2`, `graphology`, `@tanstack/react-table`, `@tanstack/react-virtual`.
* **Background Scheduling:** APScheduler + isolated worker process.

### 3. Key User Workflows

* **The Copilot Chat:** Users can type `@portfolio` or `@AAPL` to inject specific, graph-enhanced context into the LLM's system prompt before asking questions.
* **Instant Ticker Lookup (The Hybrid Flow):**
1. User clicks or searches a ticker (e.g., `TSLA`).
2. The UI instantly displays price, P/E, and fundamental stats pulled directly via `yfinance`.
3. Simultaneously, the backend checks the local DB/KG: *"Do we have a deep-dive report generated in the last 7 days?"*
4. If yes (Cache Hit), it displays instantly. If no, the LangGraph agent is triggered in the background to write one, saving the new report and mapping newly discovered relationships (e.g., supply chain risks) into the Knowledge Graph for future use.


* **Trade Execution (HITL):** The agent outputs proposed trades as strictly formatted JSON. The UI renders this as a clickable "Review Trade" button, which populates a standalone Trade Ticket pane for the user to manually execute via Alpaca.

* **Knowledge Graph Explorer (Phase 5):**
1. Open the **Knowledge Graph** tab.
2. Use summary + ego expansion (`/api/graph/summary`, `/api/graph/ego`) for progressive loading.
3. Toggle between **Network View** (Sigma) and **Table View** (virtualized nodes/edges).
4. Search dims non-matching nodes (instead of deleting them) and camera-focuses matching neighborhoods.
5. Enable **Low Resource Mode** to stop continuous ForceAtlas2 and lower render pressure.

* **Automated Loadouts (Phase 6):**
1. Create a loadout (defaults to `is_active=false`).
2. Activation requires double confirmation in UI.
3. Worker executes strategies in a process pool with timeout + schema validation.
4. All runs are recorded in `loadout_executions` and displayed in the execution log.
5. Worker heartbeat drives the UI "Worker Online" status.

### 4. API Surface (Phase 5/6)

* **Knowledge Graph**
	* `GET /api/graph/summary`
	* `GET /api/graph/ego?ticker=AAPL&depth=2`
	* `GET /api/graph/nodes?kind=&search=&offset=&limit=`
	* `GET /api/graph/edges?kind=&source=&offset=&limit=`

* **Loadouts / Worker**
	* `GET /api/loadouts`
	* `POST /api/loadouts`
	* `PATCH /api/loadouts/{id}`
	* `DELETE /api/loadouts/{id}`
	* `GET /api/loadouts/{id}/executions`
	* `GET /api/worker/status`
	* `GET /api/strategies`

### 5. Reliability & Safety Notes

* FastAPI and worker communicate only through SQLite.
* SQLite is configured with:
	* `PRAGMA journal_mode=WAL`
	* `PRAGMA synchronous=NORMAL`
	* `PRAGMA busy_timeout=5000`
* Worker singleton safety is enforced via OS file lock (`open_fin_worker.lock`).
* Strategy output is validated against strict schema (`action`, `ticker`, `qty`, `confidence`) before any trade call.
* Worker rejects strategy outputs that attempt to trade a ticker different from the loadout ticker.
* Use separate worker credentials via `ALPACA_WORKER_API_KEY` / `ALPACA_WORKER_API_SECRET`.

### 6. Production / Rollout Notes

* **Packaged app resources:** The Electron build copies the Python backend into the app's `resources/backend` directory.
* **Writable storage:** In packaged builds, the SQLite DB and Knowledge Graph are stored under the OS user data directory (Electron `userData`) via:
	* `OPEN_FIN_DB_PATH` (SQLite DB)
	* `OPEN_FIN_KG_PATH` (Knowledge Graph JSON)
* **LLM configuration:** For packaged builds, the backend loads environment variables from `OPEN_FIN_ENV_PATH` (defaults to a user-writable `.env` under `userData`).
	* Hugging Face requires an OpenAI-compatible endpoint URL in `HF_BASE_URL` in addition to `HF_API_TOKEN`.

### 7. Run / QA Commands

From repo root:

* **Backend tests**
	* `cd backend`
	* `../.venv/Scripts/python.exe -m pytest -q`

* **Frontend production checks**
	* `cd frontend`
	* `npm run build:renderer`
	* `npm run build:electron`

* **Full package build (may require elevated symlink privileges on Windows)**
	* `npm run build`

### 8. Other Documentation


1. `frontend/README.md` - Style Guide
2. `.claude/AGENT.md` - Phase/Feature Implementation Record