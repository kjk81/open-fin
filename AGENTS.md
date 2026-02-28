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
- **Database**: SQLite with SQLAlchemy (`backend/database.py`). WAL mode enabled.
- **Knowledge Graph**: Persistent SQLite tables (`kg_nodes`, `kg_edges`) + FAISS vector index (`backend/faiss_data/openfin.index`) for semantic search. Managed by `FaissManager` in `backend/agent/vector_store.py`.
- **Embeddings**: `fastembed` (ONNX runtime, CPU-only) with `BAAI/bge-small-en-v1.5` (384-dim). No PyTorch dependency.
- **AI/Agents**: LangChain & LangGraph (`backend/agent/`).
- **Data Sources**: `yfinance` (market data), `alpaca-trade-api` (brokerage).

## 📂 Key Directories

| Path | Description |
| :--- | :--- |
| `backend/agent/` | **Core Logic**. `graph.py` (LangGraph workflow), `nodes.py` (agent actions), `knowledge_graph.py` (SQLite+FAISS KG), `vector_store.py` (FaissManager). |
| `backend/routers/` | **API Endpoints**. `chat.py` (LLM interaction), `portfolio.py` (Alpaca sync), `trade.py` (execution), `graph.py` (KG queries via SQL+FAISS). |
| `backend/strategies/` | **Algo Trading**. Base classes and implementations (e.g., `momentum.py`). |
| `backend/scripts/` | **Utilities**. `migrate_kg_to_sqlite.py` (one-time migration from `open_fin_kg.json`). |
| `backend/faiss_data/` | **FAISS index files**. `openfin.index` + `openfin.index.lock`. Overridable via `OPEN_FIN_FAISS_DIR`. |
| `frontend/src/components/` | **UI**. `KG*.tsx` (Graph views), `Chat*.tsx` (LLM chat), `Ticker*.tsx` (Market data). |
| `frontend/electron/` | **System**. Native integration handling. |

## 🧠 Core Concepts

1.  **Hybrid Intelligence**: The system combines deterministic data (stock prices) with probabilistic AI (market sentiment analysis).
2.  **Knowledge Graph**: Entities (Tickers, Sectors, Industries) are `KGNode` rows in SQLite; relationships are `KGEdge` rows. Node vectors are stored in a FAISS `IndexIVFFlat` (wrapped in `IndexIDMap`) for semantic nearest-neighbour search. Visualized via React Sigma.
    - **Soft deletes**: Nodes are never removed from FAISS directly. Set `KGNode.is_deleted = True` instead. The index is automatically rebuilt when >10% of nodes are soft-deleted.
    - **Write concurrency**: All FAISS writes go through a single asyncio writer task (started in `main.py` lifespan) with `filelock` for crash safety. Readers use MMAP read-only access with no lock.
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
4.  **Modify the KG Schema**: Add columns to `KGNode` or `KGEdge` in `backend/models.py`. Run Alembic or drop-and-recreate the tables. If node text representations change, rebuild the FAISS index: `python scripts/migrate_kg_to_sqlite.py`.
5.  **First-time setup**: Install deps (`pip install -r requirements.txt`) then run the migration script if an `open_fin_kg.json` exists from a previous version: `cd backend && python scripts/migrate_kg_to_sqlite.py`.
