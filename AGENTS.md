# Open-Fin Developer Guide

Open-Fin is a local-first financial co-pilot merging real-time market data with local LLM intelligence and knowledge graphs.

## Architecture

### Frontend (Electron + React)
- **Stack**: React 18, TypeScript, Vite, Electron.
- **State**: React Context (`AppContext.tsx`).
- **Styling**: Standard CSS with CSS variables (`src/index.css`). No Tailwind.
- **Visualization**: `@react-sigma/core` (Graphs), `@tanstack/react-table` (Data).
- **Entry**: `frontend/electron/main.ts` (Main), `frontend/src/main.tsx` (Renderer).

### Backend (Python FastAPI)
- **API**: FastAPI (`backend/main.py`).
- **Database**: SQLite + SQLAlchemy (`backend/database.py`). WAL mode enabled.
- **Vector Store**: FAISS (CPU) + FastEmbed (`backend/agent/vector_store.py`).
- **AI**: LangChain + LangGraph (`backend/agent/`).
- **Workers**: Background process for strategies (`backend/worker.py`) using `apscheduler`.

## Key Directories

| Path | Description |
| :--- | :--- |
| `backend/agent/` | Core logic: `graph.py` (Workflow), `nodes.py` (Actions), `vector_store.py` (FAISS). |
| `backend/routers/` | API endpoints: `chat.py`, `portfolio.py`, `graph.py`. |
| `backend/tools/` | Research tools: `web.py` (Scraping/Search), `finance.py`. |
| `backend/worker.py` | Background task runner for trading strategies. |
| `frontend/src/components/` | UI components: `KG*.tsx` (Graph), `Chat*.tsx` (LLM). |

## Core Concepts

1. **Hybrid Intelligence**: Combines deterministic data (prices) with probabilistic AI (sentiment).
2. **Knowledge Graph**: SQLite (`kg_nodes`, `kg_edges`) + FAISS index.
   - **Soft Deletes**: Nodes are marked `is_deleted=True`. Index rebuilt when >10 % deleted.
   - **Concurrency**: Single-writer asyncio task in `main.py` manages FAISS writes. Embeds batched at 500 vectors max. FAISS startup runs in `asyncio.to_thread` to avoid blocking.
3. **Agent Workflow**: User Query → LangGraph → Tools → SSE Response (120 s timeout).
4. **Security**:
   - `session_id` requires UUID4 format. `context_refs` validated against allow-list + ticker regex.
   - CORS restricted to `GET`/`POST`/`OPTIONS` with explicit allowed headers.
   - Co-mention regex requires `$`-prefix (e.g. `$AAPL`); falls back to bare uppercase with extended stopword list.
   - SSRF mitigated via `backend/clients/url_guard.py` (IP-blocklist); imported by `http_base.py` and `sec_filings.py`.

## Development Standards

- **Python**: PEP 8. Strong type hints.
- **TypeScript**: Functional components, Hooks, Strict typing.
- **Styling**: Use CSS variables (e.g., `var(--accent)`). match `index.css`.
- **Testing**: `pytest` (Backend), `npm run typecheck` (Frontend).

## Quick Start for Agents

1. **Context**: Review `backend/agent/graph.py` for decision flow.
2. **New Tool**: Add to `backend/agent/nodes.py`, register in `graph.py`.
3. **UI**: Create components in `frontend/src/components/`. Route data via `backend/routers/`.
4. **Database**: Add columns to `backend/models.py`. Migration via `backend/scripts/migrate_kg_to_sqlite.py` (if KG changes) or manual Alembic.
5. **Worker**: Register new strategies in `backend/strategies/__init__.py`.
