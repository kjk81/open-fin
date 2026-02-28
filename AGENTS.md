# Contributing to Open-Fin

Open-Fin is a local-first, privacy-focused financial co-pilot desktop application. It merges real-time market data with local LLM intelligence and knowledge graphs to assist in research and trading.

## 🏗 Architecture

### Frontend (Electron + React)
- **Framework**: React 18 + TypeScript + Vite.
- **State**: React Context (`AppContext.tsx`).
- **Styling**: Tailwind CSS.
- **Visualization**: `@react-sigma/core` for graphs, `@tanstack/react-table` for data.
- **Entry**: `frontend/electron/main.ts` (Main Process), `frontend/src/main.tsx` (Renderer).

### Backend (Python FastAPI)
- **API**: FastAPI (`backend/main.py`).
- **Database**: SQLite with SQLAlchemy (`backend/database.py`).
- **Graph**: NetworkX for knowledge graph relationships.
- **AI/Agents**: LangChain & LangGraph (`backend/agent/`).
- **Data Sources**: `yfinance` (market data), `alpaca-trade-api` (brokerage).

## 📂 Key Directories

| Path | Description |
| :--- | :--- |
| `backend/agent/` | **Core Logic**. `graph.py` (LangGraph workflow), `nodes.py` (agent actions), `knowledge_graph.py`. |
| `backend/routers/` | **API Endpoints**. `chat.py` (LLM interaction), `portfolio.py` (Alpaca sync), `trade.py` (execution). |
| `backend/strategies/` | **Algo Trading**. Base classes and implementations (e.g., `momentum.py`). |
| `frontend/src/components/` | **UI**. `KG*.tsx` (Graph views), `Chat*.tsx` (LLM chat), `Ticker*.tsx` (Market data). |
| `frontend/electron/` | **System**. Native integration handling. |

## 🧠 Core Concepts

1.  **Hybrid Intelligence**: The system combines deterministic data (stock prices) with probabilistic AI (market sentiment analysis).
2.  **Knowledge Graph**: Entities (Tickers, Concepts) are nodes; relationships are edges. Visualized via React Sigma.
3.  **Agent Workflow**:
    -   **Input**: User query via Chat.
    -   **Process**: LangGraph routes to tools (Market Data, KG Search, Calculator).
    -   **Output**: Structured response + UI updates.

## 🛠 Development Standards

-   **Code Style**:
    -   **Python**: PEP 8. Use type hints extensively.
    -   **TypeScript**: Functional components, Hooks, Strict typing.
-   **Testing**:
    -   Backend: `pytest` in `backend/tests/`.
    -   Frontend: `npm run typecheck` (currently).
-   **Commits**: Use semantic messages (e.g., `feat: add bollinger bands`, `fix: graph rendering`).

## 🚀 Quick Start for Agents

1.  **Check Context**: Read `backend/agent/graph.py` to understand the decision flow.
2.  **Add Tool**: Create a new tool in `backend/agent/nodes.py` and register in `graph.py`.
3.  **Add UI**: Create a component in `frontend/src/components/` and route data via `backend/routers/`. Visit `frontend/STYLE.md` for style guide.
