### 1. Project Overview

A local, privacy-first desktop application functioning as a quantitative financial co-pilot. It uses a hybrid data retrieval architecture: relying on external APIs (`yfinance`, `alpaca`) for instant, raw quantitative data (prices, balance sheets), while leveraging a local Knowledge Graph and SQLite database to map market relationships and cache computationally expensive LLM reports. It strictly enforces a Human-In-The-Loop (HITL) workflow for trade execution.

### 2. Technology Stack

* **Frontend:** Electron, React (Vite), Tailwind CSS.
* **Backend:** Python, FastAPI, Uvicorn.
* **Database (Local):** SQLite (for settings, portfolio state, and cached text reports), NetworkX/Neo4j (for the Knowledge Graph relationships).
* **AI/Agent Core:** LangGraph, LangChain, Ollama (or selected cloud API with auto fallback (at least OpenRouter, Gemini, Groq, Hugging Face) for inference).
* **Data Providers:** Alpaca SDK (paper trading/portfolio sync), `yfinance` (instant historical and fundamental market data).

### 3. Key User Workflows

* **The Copilot Chat:** Users can type `@portfolio` or `@AAPL` to inject specific, graph-enhanced context into the LLM's system prompt before asking questions.
* **Instant Ticker Lookup (The Hybrid Flow):**
1. User clicks or searches a ticker (e.g., `TSLA`).
2. The UI instantly displays price, P/E, and fundamental stats pulled directly via `yfinance`.
3. Simultaneously, the backend checks the local DB/KG: *"Do we have a deep-dive report generated in the last 7 days?"*
4. If yes (Cache Hit), it displays instantly. If no, the LangGraph agent is triggered in the background to write one, saving the new report and mapping newly discovered relationships (e.g., supply chain risks) into the Knowledge Graph for future use.


* **Trade Execution (HITL):** The agent outputs proposed trades as strictly formatted JSON. The UI renders this as a clickable "Review Trade" button, which populates a standalone Trade Ticket pane for the user to manually execute via Alpaca.