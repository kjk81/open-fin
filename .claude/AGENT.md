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