"""Phase 8 — Tests for test_frozen.py (smoke test script).

Validates the smoke test script itself:
- Imports and parses correctly.
- Has the expected public interface.
- Uses temporary directory for isolation.
- Checks health endpoint at the correct URL.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class TestFrozenSmokeScript:
    """Validate test_frozen.py structure and correctness."""

    @pytest.fixture()
    def source(self) -> str:
        return (BACKEND_DIR / "test_frozen.py").read_text()

    def test_file_exists(self):
        assert (BACKEND_DIR / "test_frozen.py").is_file()

    def test_parses_as_python(self, source):
        tree = ast.parse(source, filename="test_frozen.py")
        assert isinstance(tree, ast.Module)

    def test_has_main_function(self, source):
        tree = ast.parse(source, filename="test_frozen.py")
        func_names = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ]
        assert "main" in func_names

    def test_health_check_url(self, source):
        assert "http://127.0.0.1:" in source
        assert "/api/health" in source

    def test_uses_temp_directory(self, source):
        assert "TemporaryDirectory" in source or "tempfile" in source

    def test_sets_required_env_vars(self, source):
        for var in ["OPEN_FIN_DB_PATH", "OPEN_FIN_KG_PATH", "OPEN_FIN_FAISS_DIR", "OPEN_FIN_ENV_PATH"]:
            assert var in source, f"Smoke test must set {var} for isolation"

    def test_has_timeout(self, source):
        """Should have a startup timeout to prevent hanging in CI."""
        assert "STARTUP_TIMEOUT" in source or "timeout" in source.lower()

    def test_kills_process_in_finally(self, source):
        """Must terminate the spawned process in a finally block."""
        assert "finally:" in source
        assert "terminate" in source or "kill" in source

    def test_has_main_guard(self, source):
        assert '__name__' in source and '__main__' in source
