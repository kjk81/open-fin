"""Phase 8 — Tests for build.py (backend build script).

Validates:
- Module imports and structure.
- ``build_api()`` and ``build_worker()`` invoke PyInstaller with correct args.
- ``main()`` dispatches ``all``, ``api``, ``worker`` correctly.
- Exit-on-failure behavior.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import build as build_module


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class TestBuildModuleStructure:
    """Verify build.py is well-formed."""

    def test_importable(self):
        assert hasattr(build_module, "build_api")
        assert hasattr(build_module, "build_worker")
        assert hasattr(build_module, "main")
        assert hasattr(build_module, "run")

    def test_backend_dir_resolves(self):
        assert build_module.BACKEND_DIR == BACKEND_DIR


class TestRunHelper:
    """build.run() should shell out and abort on failure."""

    def test_run_calls_subprocess(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            build_module.run(["echo", "hello"])
            mock_run.assert_called_once()

    def test_run_exits_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with pytest.raises(SystemExit) as exc_info:
                build_module.run(["false"])
            assert exc_info.value.code == 1

    def test_run_uses_backend_cwd(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            build_module.run(["echo", "test"])
            _, kwargs = mock_run.call_args
            assert kwargs["cwd"] == str(BACKEND_DIR)


class TestBuildApi:
    """build_api() should invoke PyInstaller with the correct spec and paths."""

    def test_invokes_pyinstaller_with_api_spec(self):
        with patch.object(build_module, "run") as mock_run:
            build_module.build_api()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "open-fin-api.spec" in args
            assert "--distpath" in args
            idx = args.index("--distpath")
            assert "dist/api" in args[idx + 1]

    def test_uses_clean_flag(self):
        with patch.object(build_module, "run") as mock_run:
            build_module.build_api()
            args = mock_run.call_args[0][0]
            assert "--clean" in args

    def test_uses_noconfirm_flag(self):
        with patch.object(build_module, "run") as mock_run:
            build_module.build_api()
            args = mock_run.call_args[0][0]
            assert "--noconfirm" in args


class TestBuildWorker:
    """build_worker() should invoke PyInstaller with the worker spec."""

    def test_invokes_pyinstaller_with_worker_spec(self):
        with patch.object(build_module, "run") as mock_run:
            build_module.build_worker()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "open-fin-worker.spec" in args
            assert "--distpath" in args
            idx = args.index("--distpath")
            assert "dist/worker" in args[idx + 1]


class TestMainDispatch:
    """main() should dispatch based on CLI arg."""

    def test_default_builds_both(self):
        with patch.object(build_module, "build_api") as api, \
             patch.object(build_module, "build_worker") as worker, \
             patch("sys.argv", ["build.py"]):
            build_module.main()
            api.assert_called_once()
            worker.assert_called_once()

    def test_api_only(self):
        with patch.object(build_module, "build_api") as api, \
             patch.object(build_module, "build_worker") as worker, \
             patch("sys.argv", ["build.py", "api"]):
            build_module.main()
            api.assert_called_once()
            worker.assert_not_called()

    def test_worker_only(self):
        with patch.object(build_module, "build_api") as api, \
             patch.object(build_module, "build_worker") as worker, \
             patch("sys.argv", ["build.py", "worker"]):
            build_module.main()
            api.assert_not_called()
            worker.assert_called_once()
