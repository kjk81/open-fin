## Updated Coding Agent Prompts

Here is the revised 4-phase prompt chain for Claude Code or GitHub Copilot.

### Phase 1: Monorepo Scaffold & Data Foundation

**Mode: Plan**

You are an expert full-stack engineer.

> "We are building a desktop financial AI co-pilot with a hybrid data architecture. Set up a monorepo with an Electron/React frontend and a Python FastAPI backend.
> 1. **Backend (`/backend`):** Set up a FastAPI server. Integrate SQLite using SQLAlchemy with three tables: `UserPortfolio` (holding cached positions), `ChatHistory`, and `ReportCache` (columns: ticker, report_text, generated_at). Integrate `alpaca-trade-api` to fetch paper trading positions on startup. Integrate `yfinance` to fetch basic ticker info (price, market cap, P/E).
> 2. **Frontend (`/frontend`):** Initialize an Electron app with React (Vite). Set up IPC bridges to start/stop the FastAPI server and make HTTP requests.
> 3. **Goal:** I want to launch the app, see a blank React screen, and verify the backend is running, has synced a mock Alpaca portfolio, and can successfully fetch Apple's current price via a test `yfinance` endpoint."
> 
> 

### Phase 2: LangGraph Brain & The Cache Router

**Mode: Edit**

> "In the `/backend` directory, implement the agentic core using `langgraph`. We will use a fallback LLM API scheme, where the user can rank OpenRouter, Hugging Face Inference, Groq, OpenAI, Gemini or Ollama (local).
> 1. Create a LangGraph state graph with these nodes:
> * **IntentRouter:** Determines if the user wants general chat, a trade recommendation, or a ticker deep-dive.
> * **ContextInjector:** Reads the prompt for `context_refs` (like `["user_portfolio"]`) and pulls current holdings from SQLite to inject into the system prompt.
> * **TickerLookupNode:** Uses `yfinance` to pull live fundamental data. It then queries the `ReportCache` SQLite table. If a report is < 7 days old, it returns it. If not, it triggers the LLM to generate a new synthesis report based on the `yfinance` data and saves it to the DB.
> * **GenerationNode:** The final LLM call. Ensure it is prompted to return trades in this strict format: `[TRADE: {"action": "BUY", "ticker": "AAPL", "qty": 10}]`.
> 
> 
> 2. Expose a `POST /api/chat` endpoint to stream this output back to the client."
> 
> 

### Phase 3: The UI, Attachments, and Ticker Dashboard

> "Move to the `/frontend` React code to build the UI layout.
> 1. Create a three-pane layout: A `ChatBox` (center), a `PortfolioSidebar` (left), and a `TickerDashboard` (right).
> 2. In the `ChatBox`, implement an `@` mention system. Typing `@` shows a popover for `portfolio` or a ticker search. Pass these tags as `context_refs` to the `/api/chat` endpoint.
> 3. When a user queries a specific ticker (e.g., via chat or search bar), the `TickerDashboard` should immediately call a backend endpoint to fetch and display the raw `yfinance` stats (Price, P/E, etc.).
> 4. Below the stats in the `TickerDashboard`, show a loading spinner that says 'Checking report cache...' while waiting for the LangGraph agent to either pull the cached text or generate a new one."
> 
> 

### Phase 4: Markdown Parsing & Trade Execution

> "Update the frontend to handle the Human-In-The-Loop trade execution.
> 1. Write a custom markdown parser for the chat messages. When it detects `[TRADE: {"action": "BUY", "ticker": "AAPL", "qty": 10}]`, do *not* render the JSON. Render a highly visible button: 'Review Trade: BUY 10 AAPL'.
> 2. Create a `TradeTicket` modal or UI component.
> 3. When the 'Review Trade' button is clicked, auto-populate the `TradeTicket` with the parsed JSON data.
> 4. Add a 'Confirm Execution' button in the ticket. When clicked, hit a `POST /api/execute_trade` endpoint on the backend that uses the Alpaca SDK to place the paper market order.
> 5. On success, post a system message back into the chat UI ('Trade Executed: BUY 10 AAPL') so the LLM has context of the action."
> 

### Phase 5: Knowledge Graph Visualizer

"We need to build a high-performance 'Knowledge Graph Explorer' tab in our React/Electron frontend, capable of scaling to tens of thousands of nodes. Plan this out using a dual-view paradigm (Table vs. Network) with strict performance safeguards.

1. State & Libraries: > * Plan to use react-sigma (v3) and graphology for the WebGL graph.

The graphology instance must be the Single Source of Truth (SSOT). Do not copy massive node/edge arrays into React component state; use Sigma's hooks/reducers to read directly from the graphology instance.

Use @tanstack/react-table combined with @tanstack/react-virtual for the Table View.

2. Data Loading & Backend Endpoints:

Do not load the entire graph on initialization. Plan FastAPI endpoints for Progressive Loading:

GET /api/graph/ego?ticker=AAPL&depth=2 (Returns a bounded subgraph/ego-network).

GET /api/graph/summary (Returns high-level community clusters for the initial zoomed-out view).

3. View Requirements:

Virtualized Table View: Render the CRUD data grid. Virtualization is mandatory so the DOM doesn't crash when displaying 10,000+ entities/relationships.

Network View (Sigma.js): Run the @react-sigma/layout-forceatlas2 algorithm strictly inside a Web Worker to keep the UI thread unblocked.

4. LOD & Interaction (Obsidian Style):

Search/Filter: When searching, use reducers to dim non-matching nodes to #27272a instead of removing them. Focus the camera on the matching neighborhood.

Level of Detail (LOD): Implement LOD rules in Sigma. Text labels and minor edge lines should dynamically disappear when the user zooms out past a certain threshold to save GPU cycles.

5. Fallbacks & Testing Plan:

Include a 'Low Resource Mode' toggle in the UI that disables the continuous ForceAtlas2 layout (runs it once and stops) and reduces node texture resolution.

Include a brief matrix in your plan for how we will test 1k, 10k, and 50k node payloads.

Please write out the step-by-step implementation plan, the FastAPI subgraph route structures, and the React component tree before writing any code."

## Phase 6: Automated Loadouts
"We are building an 'Automated Loadouts' background engine to execute scheduled and event-driven trading algorithms. This system must have strict process isolation from our FastAPI chat server. Plan this out with a focus on safety, observability, concurrency, and strict data contracts.

1. Process Isolation & Multi-Worker Safety:

Plan a standalone worker.py entry point running APScheduler in a separate OS process from the FastAPI web server.

Concurrency & Safety: The FastAPI app and worker will communicate exclusively via SQLite. Specify the exact SQLite PRAGMA settings required for concurrent read/writes (e.g., WAL mode, normal synchronous).

Worker Lock: Define a file-lock or DB-lease mechanism (or explicitly enforce a single-worker architecture) so we never double-execute trades if multiple worker instances accidentally start.

2. Database Schema (Exact SQL):

Provide the exact SQL CREATE TABLE statements for two tables:

Loadouts: ticker, strategy_name, schedule, is_active, parameters (JSON).

LoadoutExecutions: timestamp, loadout_id, action, quantity, status, error_trace.

3. Sandboxed Execution & Strict Schemas:

Strategies must run in a subprocess/ProcessPool with strict timeouts.

The JSON Contract: Define a strict JSON schema for strategy outputs (e.g., {"action": "BUY", "ticker": "MSFT", "qty": 10, "confidence": 0.95}).

Trade Validator: Plan a validator step that enforces max quantity limits, checks the JSON schema, and supports a dry_run flag before the Alpaca SDK is ever called.

4. Observability & Secure Storage:

Health Metrics: Plan an explicit observability requirement (e.g., the worker writes a heartbeat timestamp to a WorkerStatus table or exposes a loopback port) so the React UI can display a 'Worker: Online' indicator.

Credentials: Provide guidance on securely storing and loading the separate Alpaca background-trading API keys (do not hardcode).

5. UI UX & Testing:

UI Default: New loadouts default to is_active = false with a double-confirm to enable. Show the execution audit log in the UI.

CI/Tests: Outline integration test steps, specifically including a test that asserts worker.py does not import langgraph or any memory-heavy chat modules.

Please write out the comprehensive architectural plan, the SQL statements, and the JSON schemas before writing any code."