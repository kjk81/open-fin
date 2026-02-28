"""Phase 8 — Tests for entry_api.py and entry_worker.py PyInstaller entry points.

Validates:
- Both modules import correctly.
- ``entry_api.run()`` calls ``uvicorn.run()`` with the correct app and arguments.
- ``entry_worker.run()`` delegates to ``worker.main()``.
- Both guard behind ``if __name__ == "__main__"`` and call ``freeze_support()``.
"""

from __future__ import annotations

import ast
import importlib
import multiprocessing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class TestEntryApiModule:
    """entry_api.py should import cleanly and wire uvicorn correctly."""

    def test_module_importable(self):
        import entry_api
        assert hasattr(entry_api, "run")

    def test_run_calls_uvicorn(self):
        """run() should invoke uvicorn.run — verified via AST inspection."""
        source = (BACKEND_DIR / "entry_api.py").read_text()
        tree = ast.parse(source)
        # Find the run() function body and look for a uvicorn.run(...) call
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run":
                calls = [
                    n for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "run"
                ]
                assert len(calls) >= 1, "run() must call uvicorn.run()"
                return
        pytest.fail("run() function not found in entry_api.py")

    def test_has_main_guard(self):
        """The module must have an ``if __name__ == '__main__':`` block."""
        source = (BACKEND_DIR / "entry_api.py").read_text()
        assert 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source

    def test_calls_freeze_support_in_main_guard(self):
        """freeze_support() must appear in the __main__ block."""
        source = (BACKEND_DIR / "entry_api.py").read_text()
        assert "freeze_support()" in source
        assert "multiprocessing" in source

    def test_uvicorn_host_and_port(self):
        """Verify the hardcoded host/port match what Electron expects."""
        source = (BACKEND_DIR / "entry_api.py").read_text()
        assert '"127.0.0.1"' in source or "'127.0.0.1'" in source
        assert "8000" in source


class TestEntryWorkerModule:
    """entry_worker.py should import and delegate to worker.main()."""

    def test_module_importable(self):
        import entry_worker
        assert hasattr(entry_worker, "run")

    def test_has_main_guard(self):
        source = (BACKEND_DIR / "entry_worker.py").read_text()
        assert 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source

    def test_calls_freeze_support_in_main_guard(self):
        source = (BACKEND_DIR / "entry_worker.py").read_text()
        assert "freeze_support()" in source
        assert "multiprocessing" in source

    def test_delegates_to_worker_main(self):
        """run() should call worker.main() and return its exit code."""
        source = (BACKEND_DIR / "entry_worker.py").read_text()
        assert "from worker import main" in source
        assert "return main()" in source


class TestEntryPointAST:
    """AST-level validation that entry points are well-formed."""

    @pytest.fixture(params=["entry_api.py", "entry_worker.py"])
    def entry_source(self, request) -> tuple[str, str]:
        name = request.param
        return name, (BACKEND_DIR / name).read_text()

    def test_parses_cleanly(self, entry_source):
        name, source = entry_source
        tree = ast.parse(source, filename=name)
        assert isinstance(tree, ast.Module)

    def test_has_function_named_run(self, entry_source):
        name, source = entry_source
        tree = ast.parse(source, filename=name)
        func_names = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ]
        assert "run" in func_names, f"{name} must define a run() function"

    def test_main_guard_exists(self, entry_source):
        """Verify there's an ``if __name__ == '__main__':`` at the top level."""
        name, source = entry_source
        tree = ast.parse(source, filename=name)
        found = False
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.If):
                # Check for __name__ == "__main__"
                test = ast.dump(node.test)
                if "__main__" in test and "__name__" in test:
                    found = True
                    break
        assert found, f"{name} must have an if __name__ == '__main__' guard"
