# Open-Fin

Open-Fin is a local, privacy-first desktop application functioning as a quantitative financial co-pilot. It combines real-time market data with local LLM intelligence and a knowledge graph to help users research, analyze, and execute trades.

## 🚀 Features

- **Hybrid Intelligence:** Merges instant market data (yfinance) with deep-dive LLM reports.
- **Privacy-First:** Local SQLite database + knowledge graph tables + local vector index ensure your strategies and research stay on your machine.
- **Chat Interface:** "Claude Code" style chat for querying portfolio status or ticker details (`@AAPL`).
- **Knowledge Graph:** Visual exploration of market relationships and hidden risks using React Sigma.
- **Automated Loadouts:** Background worker for executing trading strategies (Momentum, etc.) with strict schema validation.
- **Trade Execution:** Human-In-The-Loop (HITL) workflow for reviewing and executing trades via Alpaca.

## 🛠️ Tech Stack

### Frontend
- **Core:** Electron, React, TypeScript, Vite
- **Styling:** Standard CSS with CSS variables (`frontend/src/index.css`)
- **Visualization:** React Sigma (Graph), TanStack Table

### Backend
- **API:** Python, FastAPI, Uvicorn
- **Database:** SQLite + SQLAlchemy
- **Knowledge Graph:** SQLite tables (`kg_nodes`, `kg_edges`) + FAISS (CPU) vector index
- **AI/Agent:** LangChain, LangGraph, Ollama (or cloud providers like Anthropic/OpenAI)
- **Data:** yfinance, Alpaca SDK, FMP
- **Scheduling:** APScheduler

## 📦 Prerequisites

- **Node.js** (v18+)
- **Python** (v3.10+)
- **Alpaca Account** (for trading data/execution; paper trading recommended)

## ⚡ Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/open-fin.git
cd open-fin
```

### 2. Backend Setup
Initialize the Python environment and install dependencies.

```bash
cd backend
python -m venv .venv
# Activate the virtual environment:
# Windows:
# .venv\Scripts\activate
# Unix/MacOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

For running tests locally:

```bash
pip install -r requirements-dev.txt
```

Create a `.env` file based on `backend/.env.example` and add your API keys (Alpaca, OpenAI/Anthropic, etc.). The backend reads env vars from the path in `OPEN_FIN_ENV_PATH`:

- If you run the backend directly, set `OPEN_FIN_ENV_PATH` to your `.env` file path.
- If you run via Electron (`npm run dev`), Electron sets `OPEN_FIN_ENV_PATH` automatically to a user-data `.env` location.

### 3. Frontend Setup
Install the Node.js dependencies.

```bash
cd ../frontend
npm install
```

### 4. Running the App
To start the application in development mode (starts both Vite and Electron):

```bash
# From the frontend directory
npm run dev
```

Note: Electron will also start the backend API (Uvicorn) and worker process, and will create/manage its own Python venv under the Electron user data directory on first run.

## 🧪 Testing

**Backend:**
```bash
cd backend
python -m pytest
```

**Frontend:**
```bash
cd frontend
npm run typecheck
```

## Next Steps
Additional API support (brokers)
Preset Loadouts (e.g. TradingAgents, AlphaQuanter, standard algos - and ensure compatability)

## 🤝 Contributing
Contributions are welcome! Please check out the [Frontend Style Guide](frontend/STYLE.md) for UI/UX guidelines.
