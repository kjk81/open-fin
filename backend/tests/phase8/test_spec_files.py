"""Phase 8 — Tests for PyInstaller spec files.

Validates that the ``.spec`` files:
- Parse as valid Python (they are Python scripts).
- Reference the correct entry-point scripts.
- Include required hidden imports.
- Bundle the ``agent/skills`` data directory (API spec only).
- Exclude heavy dev-only packages.
- Use directory mode (``exclude_binaries=True``).
- Name the output executables correctly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class TestApiSpec:
    """Validate open-fin-api.spec structural integrity."""

    @pytest.fixture()
    def spec_source(self) -> str:
        return (BACKEND_DIR / "open-fin-api.spec").read_text()

    def test_file_exists(self):
        assert (BACKEND_DIR / "open-fin-api.spec").is_file()

    def test_parses_as_python(self, spec_source):
        tree = ast.parse(spec_source, filename="open-fin-api.spec")
        assert isinstance(tree, ast.Module)

    def test_targets_entry_api(self, spec_source):
        assert "entry_api.py" in spec_source

    def test_bundles_skills_data(self, spec_source):
        assert "agent/skills" in spec_source or 'agent" / "skills' in spec_source

    @pytest.mark.parametrize(
        "hidden_import",
        [
            "uvicorn.logging",
            "uvicorn.lifespan.on",
            "sqlalchemy.dialects.sqlite",
            "aiosqlite",
            "langchain_openai",
            "langchain_ollama",
            "langchain_google_genai",
            "fastembed",
            "faiss",
            "main",
            "database",
            "pathutil",
            "routers.chat",
            "routers.portfolio",
            "tools.finance",
            "tools.web",
            "strategies.momentum",
        ],
    )
    def test_includes_hidden_import(self, spec_source, hidden_import):
        assert hidden_import in spec_source, (
            f"open-fin-api.spec is missing hiddenimport: {hidden_import}"
        )

    @pytest.mark.parametrize("excluded", ["pytest", "tkinter", "matplotlib"])
    def test_excludes_dev_packages(self, spec_source, excluded):
        assert excluded in spec_source, (
            f"open-fin-api.spec should explicitly exclude: {excluded}"
        )

    def test_directory_mode(self, spec_source):
        """Should use exclude_binaries=True (directory mode, not one-file)."""
        assert "exclude_binaries=True" in spec_source

    def test_output_name(self, spec_source):
        assert 'name="open-fin-api"' in spec_source

    def test_console_mode(self, spec_source):
        """Must be a console app so Electron can capture stdout/stderr."""
        assert "console=True" in spec_source


class TestWorkerSpec:
    """Validate open-fin-worker.spec structural integrity."""

    @pytest.fixture()
    def spec_source(self) -> str:
        return (BACKEND_DIR / "open-fin-worker.spec").read_text()

    def test_file_exists(self):
        assert (BACKEND_DIR / "open-fin-worker.spec").is_file()

    def test_parses_as_python(self, spec_source):
        tree = ast.parse(spec_source, filename="open-fin-worker.spec")
        assert isinstance(tree, ast.Module)

    def test_targets_entry_worker(self, spec_source):
        assert "entry_worker.py" in spec_source

    @pytest.mark.parametrize(
        "hidden_import",
        [
            "sqlalchemy.dialects.sqlite",
            "worker",
            "worker_db",
            "worker_models",
            "strategies",
            "strategies.momentum",
            "yfinance",
        ],
    )
    def test_includes_hidden_import(self, spec_source, hidden_import):
        assert hidden_import in spec_source, (
            f"open-fin-worker.spec is missing hiddenimport: {hidden_import}"
        )

    @pytest.mark.parametrize(
        "excluded",
        ["faiss", "fastembed", "onnxruntime", "langchain", "langgraph"],
    )
    def test_excludes_heavy_ai_packages(self, spec_source, excluded):
        """Worker must not bundle FAISS/LLM packages — only the API needs them."""
        # The excluded packages should appear in the 'excludes' list
        assert f'"{excluded}"' in spec_source or f"'{excluded}'" in spec_source

    def test_no_skills_data(self, spec_source):
        """Worker doesn't serve the agent — should not bundle skill files."""
        assert "datas=[]" in spec_source

    def test_directory_mode(self, spec_source):
        assert "exclude_binaries=True" in spec_source

    def test_output_name(self, spec_source):
        assert 'name="open-fin-worker"' in spec_source

    def test_console_mode(self, spec_source):
        assert "console=True" in spec_source
