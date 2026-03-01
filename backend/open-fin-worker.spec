# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Open-Fin background worker (open-fin-worker).

Build:
    pyinstaller open-fin-worker.spec --clean

Output:
    dist/worker/open-fin-worker/            (directory mode — fast startup)
        open-fin-worker.exe   (Windows)
        open-fin-worker       (macOS/Linux)

The worker is much lighter than the API server — it only needs SQLAlchemy,
APScheduler, yfinance, and Alpaca.  No FAISS, fastembed, or LangChain.
"""

import sys
from pathlib import Path

block_cipher = None

BACKEND = Path(SPECPATH).resolve()

a = Analysis(
    [str(BACKEND / "entry_worker.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # --- SQLAlchemy dialect ---
        "sqlalchemy.dialects.sqlite",
        "aiosqlite",
        # --- Greenlet (required by SQLAlchemy async engine) ---
        "greenlet",
        # --- Pydantic v2 core (C-extension, not auto-detected) ---
        "pydantic_core",
        "pydantic_core._pydantic_core",
        # --- Scheduling ---
        "apscheduler",
        "apscheduler.schedulers.blocking",
        "apscheduler.triggers.cron",
        "apscheduler.triggers.interval",
        # --- Data ---
        "yfinance",
        "alpaca_trade_api",
        # --- Project modules ---
        "worker",
        "worker_db",
        "worker_models",
        "strategies",
        "strategies.momentum",
        "pathutil",
        # --- Misc ---
        "dotenv",
        "filelock",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "scipy",
        "notebook",
        "IPython",
        "pytest",
        "respx",
        # Worker doesn't need these heavy packages
        "faiss",
        "fastembed",
        "onnxruntime",
        "langchain",
        "langchain_core",
        "langchain_openai",
        "langchain_ollama",
        "langchain_google_genai",
        "langgraph",
        "tavily",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # directory mode
    name="open-fin-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="open-fin-worker",
)
