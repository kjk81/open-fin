# Open-Fin

Open-Fin is a local, privacy-first desktop application functioning as a quantitative financial co-pilot. It combines real-time market data with local LLM intelligence and a knowledge graph to help users research, analyze, and execute trades.

## 🚀 Features

- **Hybrid Intelligence:** Merges instant market data (yfinance) with deep-dive LLM reports.
- **Privacy-First:** Local SQLite database and knowledge graph (NetworkX) ensure your strategies and research stay on your machine.
- **Chat Interface:** "Claude Code" style chat for querying portfolio status or ticker details (`@AAPL`).
- **Knowledge Graph:** Visual exploration of market relationships and hidden risks using React Sigma.
- **Automated Loadouts:** Background worker for executing trading strategies (Momentum, etc.) with strict schema validation.
- **Trade Execution:** Human-In-The-Loop (HITL) workflow for reviewing and executing trades via Alpaca.

## 🛠️ Tech Stack

### Frontend
- **Core:** Electron, React, TypeScript, Vite
- **Styling:** Tailwind CSS
- **Visualization:** React Sigma (Graph), TanStack Table

### Backend
- **API:** Python, FastAPI, Uvicorn
- **Database:** SQLite (SQLAlchemy), NetworkX
- **AI/Agent:** LangChain, LangGraph, Ollama (or cloud providers like Anthropic/OpenAI)
- **Data:** yfinance, Alpaca SDK
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
# ..\.venv\Scripts\activate
# Unix/MacOS:
# source ../.venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in `backend/` based on `.env.example` and add your API keys (Alpaca, OpenAI/Anthropic, etc.).

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

## 🧪 Testing

**Backend:**
```bash
cd backend
pytest
```

**Frontend:**
```bash
cd frontend
npm run typecheck
```

## 🤝 Contributing
Contributions are welcome! Please check out the [Frontend Style Guide](frontend/README.md) for UI/UX guidelines.
