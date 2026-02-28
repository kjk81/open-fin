# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Open-Fin API server (open-fin-api).

Build:
    pyinstaller open-fin-api.spec --clean

Output:
    dist/api/open-fin-api/            (directory mode — fast startup)
        open-fin-api.exe   (Windows)
        open-fin-api       (macOS/Linux)
"""

import sys
from pathlib import Path

block_cipher = None

# Absolute path to the backend directory (where this spec lives)
BACKEND = Path(SPECPATH).resolve()

a = Analysis(
    [str(BACKEND / "entry_api.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        # Bundle skill playbook markdown files
        (str(BACKEND / "agent" / "skills"), "agent/skills"),
    ],
    hiddenimports=[
        # --- Uvicorn internals (not detected by default) ---
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        # --- SQLAlchemy dialect ---
        "sqlalchemy.dialects.sqlite",
        "aiosqlite",
        # --- LangChain / LLM providers ---
        "langchain",
        "langchain.tools",
        "langchain_core",
        "langchain_openai",
        "langchain_ollama",
        "langchain_google_genai",
        "langgraph",
        # --- FAISS / Embeddings ---
        "faiss",
        "fastembed",
        "onnxruntime",
        # --- Data / scraping ---
        "yfinance",
        "alpaca_trade_api",
        "beautifulsoup4",
        "bs4",
        "markdownify",
        "tavily",
        "httpx",
        "httpx._transports",
        "httpx._transports.default",
        # --- Project modules (not automatically followed) ---
        "main",
        "database",
        "models",
        "pathutil",
        "worker_db",
        "worker_models",
        "agent",
        "agent.graph",
        "agent.knowledge_graph",
        "agent.llm",
        "agent.nodes",
        "agent.skills_loader",
        "agent.state",
        "agent.vector_store",
        "clients",
        "clients.edgar",
        "clients.fmp",
        "clients.http_base",
        "clients.url_guard",
        "routers",
        "routers.alerts",
        "routers.chat",
        "routers.graph",
        "routers.llm",
        "routers.loadouts",
        "routers.portfolio",
        "routers.ticker",
        "routers.trade",
        "routers.watchlist",
        "schemas",
        "schemas.finance",
        "schemas.kg_entities",
        "schemas.tool_contracts",
        "strategies",
        "strategies.momentum",
        "tools",
        "tools._utils",
        "tools.anomaly_worker",
        "tools.edgar",
        "tools.finance",
        "tools.sec_filings",
        "tools.web",
        # --- Misc hidden deps ---
        "frontmatter",
        "filelock",
        "dotenv",
        "engineio.async_drivers.threading",
        "email.mime.multipart",
        "email.mime.text",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages that are not needed at runtime
        "tkinter",
        "matplotlib",
        "PIL",
        "scipy",
        "notebook",
        "IPython",
        "pytest",
        "respx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Collect all fastembed data files (ONNX model metadata, etc.)
from PyInstaller.utils.hooks import collect_all

fastembed_datas, fastembed_binaries, fastembed_hiddenimports = collect_all("fastembed")
a.datas += fastembed_datas
a.binaries += fastembed_binaries
a.hiddenimports += fastembed_hiddenimports

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # directory mode (not one-file)
    name="open-fin-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # console app — Electron captures stdout/stderr
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="open-fin-api",
)
