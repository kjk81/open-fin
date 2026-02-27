
# 1. Frontend UI/UX Style Guide

## Design Philosophy
The application merges the rapid, keyboard-centric navigation of **VS Code**, the dense information mapping of **Obsidian**, the structured chat interface of **Discord**, and the terminal-like transparency of **Claude Code**. The interface must feel like a professional quantitative terminal, not a consumer social app.

## Color Palette (Dark Mode Default - Tailwind Zinc/Slate)
* **Background (App):** `#09090b` (Zinc 950) - Deepest black for the main application shell.
* **Background (Panels/Sidebar):** `#18181b` (Zinc 900) - Slightly elevated for sidebars and chat background.
* **Borders/Dividers:** `#27272a` (Zinc 800) - Subtle separation between panes.
* **Text (Primary):** `#f4f4f5` (Zinc 50) - High contrast for main readables.
* **Text (Muted/Secondary):** `#a1a1aa` (Zinc 400) - For timestamps, minor stats, and UI labels.
* **Accent (Action/Links):** `#3b82f6` (Blue 500) - Used sparingly for interactive elements.
* **Trade Status Indicators:** * **Buy/Profit:** `#22c55e` (Green 500)
    * **Sell/Loss:** `#ef4444` (Red 500)

## Typography
* **UI Elements & Prose:** `Inter` or `Geist`. Clean, highly readable sans-serif for standard text.
* **Data, Tickers, and Code:** `JetBrains Mono` or `Fira Code`. Monospaced fonts are mandatory for aligning financial numbers, JSON outputs, and ticker symbols (`$AAPL`).

## Layout Architecture (VS Code / Discord style)
The layout is a rigid, non-scrolling 100vh shell divided into resizable panes:
1.  **Left Sidebar (The Explorer):** * Matches Discord's channel list or VS Code's file explorer.
    * Contains sections for `Portfolio`, `Watchlist (Starred)`, and `Recent Chats`.
2.  **Center Pane (The Editor/Chat):**
    * The primary interaction zone. Claude Code style chat.
    * Agent 'thoughts' or tool calls are collapsed by default in muted gray blocks (e.g., `> Agent queried yfinance...`).
    * Input box is pinned to the bottom, supporting `@` mentions for context attachment.
3.  **Right Pane (The Inspector):**
    * Contextual. If a ticker is clicked, this becomes the **Ticker Dashboard** (live stats). 
    * If a trade is proposed, this becomes the **Trade Ticket** modal for HITL execution.

## Interaction Paradigms
* **Markdown First:** All chat outputs must support rich markdown, including tables for financial data and code blocks for raw data.
* **Deep Linking:** Entities mentioned by the AI (e.g., $TSLA) should be styled as clickable pills that instantly update the Right Pane Inspector.
* **Obsidian-style Graph:** The Knowledge Graph view should be accessible via a hotkey (e.g., `Cmd/Ctrl + G`), overlaying the center pane with an interactive node map.

# 2. Knowledge Graph Performance Test Matrix

| Payload Size | Scenario | Expected Result | Pass Criteria |
|---|---|---|---|
| 1k nodes | Load summary, expand 2-3 ego networks, switch Table/Network views | Smooth interactions in both views | No dropped interactions; no UI freeze; successful view switches and search focus |
| 10k nodes | Progressive ego loading + active search/filter + table scroll | UI remains responsive with LOD hiding labels/edges as zoom decreases | Main thread stays responsive; virtualization avoids DOM blowup; ForceAtlas2 worker does not block UI |
| 50k nodes | Low Resource Mode ON, one-shot layout, deep zoom/pan stress | App remains usable with reduced visual fidelity | No renderer crash; camera interactions stay functional; memory growth remains bounded during session |