"""Phase 8 — Tests for .env.example completeness and electron main.ts consistency.

Validates:
- Both ``.env.example`` files contain all environment variables used in code.
- The ``OPEN_FIN_*`` internal vars are documented.
- ``electron/main.ts`` sets the expected env vars for child processes.
- ``package.json`` build config references the correct extraResources paths.
- ``package.json`` has the ``build:backend`` script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent.parent  # tests/phase8 -> backend -> open-fin
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"


def _read(filepath: Path) -> str:
    return filepath.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# .env.example completeness
# ---------------------------------------------------------------------------

class TestBackendEnvExample:
    """backend/.env.example should document all env vars used in the code."""

    @pytest.fixture()
    def env_text(self) -> str:
        return _read(BACKEND_DIR / ".env.example")

    @pytest.mark.parametrize(
        "var",
        [
            "ALPACA_API_KEY",
            "ALPACA_API_SECRET",
            "ALPACA_BASE_URL",
            "ALPACA_WORKER_API_KEY",
            "ALPACA_WORKER_API_SECRET",
            "OLLAMA_MODEL",
            "OLLAMA_BASE_URL",
            "OPENROUTER_API_KEY",
            "OPENROUTER_MODEL",
            "OPENROUTER_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_BASE_URL",
            "GROQ_API_KEY",
            "GROQ_MODEL",
            "GROQ_BASE_URL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "HF_API_TOKEN",
            "HF_MODEL",
            "HF_BASE_URL",
            "FMP_API_KEY",
            "TAVILY_API_KEY",
            "EXA_API_KEY",
            "ANOMALY_INTERVAL_MINUTES",
            "ANOMALY_PRICE_DROP",
            "ANOMALY_VOLUME_SPIKE",
            "ANOMALY_GAP_DOWN",
            "OPEN_FIN_DB_PATH",
            "OPEN_FIN_KG_PATH",
            "OPEN_FIN_ENV_PATH",
            "OPEN_FIN_FAISS_DIR",
        ],
    )
    def test_env_var_documented(self, env_text, var):
        assert var in env_text, (
            f"backend/.env.example is missing documentation for {var}"
        )


class TestRootEnvExample:
    """Root .env.example should also document key vars."""

    @pytest.fixture()
    def env_text(self) -> str:
        return _read(ROOT / ".env.example")

    @pytest.mark.parametrize(
        "var",
        [
            "ALPACA_API_KEY",
            "ALPACA_API_SECRET",
            "OPEN_FIN_DB_PATH",
            "OPEN_FIN_FAISS_DIR",
            "TAVILY_API_KEY",
            "FMP_API_KEY",
            "ANOMALY_INTERVAL_MINUTES",
        ],
    )
    def test_env_var_documented(self, env_text, var):
        assert var in env_text, (
            f"Root .env.example is missing documentation for {var}"
        )


# ---------------------------------------------------------------------------
# electron/main.ts env vars
# ---------------------------------------------------------------------------

class TestElectronMainTs:
    """main.ts must pass required env vars to child processes."""

    @pytest.fixture()
    def main_ts(self) -> str:
        return _read(FRONTEND_DIR / "electron" / "main.ts")

    @pytest.mark.parametrize(
        "var",
        [
            "OPEN_FIN_DB_PATH",
            "OPEN_FIN_KG_PATH",
            "OPEN_FIN_ENV_PATH",
            "OPEN_FIN_FAISS_DIR",
        ],
    )
    def test_sets_env_var(self, main_ts, var):
        assert var in main_ts, (
            f"electron/main.ts must set {var} for backend processes"
        )

    def test_has_packaged_mode_branch(self, main_ts):
        assert "app.isPackaged" in main_ts

    def test_frozen_api_path_references(self, main_ts):
        """Packaged mode should reference the frozen API binary."""
        assert "open-fin-api" in main_ts

    def test_frozen_worker_path_references(self, main_ts):
        assert "open-fin-worker" in main_ts

    def test_dev_mode_uses_uvicorn(self, main_ts):
        assert "uvicorn" in main_ts

    def test_dev_mode_uses_worker_py(self, main_ts):
        assert "worker.py" in main_ts

    def test_has_faiss_dir_in_user_data(self, main_ts):
        """faissDir should be under userData."""
        assert "faiss_data" in main_ts


# ---------------------------------------------------------------------------
# package.json build config
# ---------------------------------------------------------------------------

class TestPackageJsonBuild:
    """package.json must have the correct build pipeline and extraResources."""

    @pytest.fixture()
    def pkg(self) -> dict:
        return json.loads(_read(FRONTEND_DIR / "package.json"))

    def test_build_backend_script_exists(self, pkg):
        assert "build:backend" in pkg["scripts"], (
            "package.json must define a build:backend script"
        )

    def test_build_script_chains_backend(self, pkg):
        build_cmd = pkg["scripts"]["build"]
        assert "build:backend" in build_cmd, (
            "The main 'build' script must chain build:backend"
        )

    def test_build_script_order(self, pkg):
        """Build order: renderer → electron → backend → electron-builder."""
        build_cmd = pkg["scripts"]["build"]
        parts = [p.strip() for p in build_cmd.split("&&")]
        # Find indices
        renderer_idx = next(i for i, p in enumerate(parts) if "build:renderer" in p)
        electron_idx = next(i for i, p in enumerate(parts) if "build:electron" in p)
        backend_idx = next(i for i, p in enumerate(parts) if "build:backend" in p)
        builder_idx = next(i for i, p in enumerate(parts) if "electron-builder" in p)
        assert renderer_idx < electron_idx < backend_idx < builder_idx

    def test_extra_resources_api(self, pkg):
        resources = pkg["build"]["extraResources"]
        api_found = any(
            "dist/api" in r.get("from", "") or "dist/api" in str(r)
            for r in resources
        )
        assert api_found, "extraResources must include backend dist/api"

    def test_extra_resources_worker(self, pkg):
        resources = pkg["build"]["extraResources"]
        worker_found = any(
            "dist/worker" in r.get("from", "") or "dist/worker" in str(r)
            for r in resources
        )
        assert worker_found, "extraResources must include backend dist/worker"

    def test_no_full_backend_copy(self, pkg):
        """Should NOT copy the entire ../backend source tree anymore."""
        resources = pkg["build"]["extraResources"]
        for r in resources:
            from_path = r.get("from", "") if isinstance(r, dict) else ""
            # Should not be just "../backend" with filter "**/*"
            assert from_path != "../backend", (
                "extraResources should not copy the entire backend source tree"
            )

    def test_platform_targets(self, pkg):
        build_cfg = pkg["build"]
        assert "win" in build_cfg, "Should have Windows target config"
        assert "mac" in build_cfg, "Should have macOS target config"
        assert "linux" in build_cfg, "Should have Linux target config"

    def test_build_dev_script_skips_backend(self, pkg):
        """build:dev should allow building without PyInstaller (dev/CI shortcut)."""
        if "build:dev" in pkg["scripts"]:
            assert "build:backend" not in pkg["scripts"]["build:dev"]


# ---------------------------------------------------------------------------
# CI workflow
# ---------------------------------------------------------------------------

class TestCIWorkflow:
    """Validate .github/workflows/build.yml structure."""

    @pytest.fixture()
    def workflow(self) -> str:
        wf_path = ROOT / ".github" / "workflows" / "build.yml"
        if not wf_path.is_file():
            pytest.skip("CI workflow file not found")
        return _read(wf_path)

    def test_workflow_exists(self):
        assert (ROOT / ".github" / "workflows" / "build.yml").is_file()

    @pytest.mark.parametrize("os_name", ["windows-latest", "macos-latest", "ubuntu-latest"])
    def test_matrix_includes_os(self, workflow, os_name):
        assert os_name in workflow, f"CI must build on {os_name}"

    def test_runs_backend_tests(self, workflow):
        assert "pytest" in workflow

    def test_runs_pyinstaller_build(self, workflow):
        assert "build.py" in workflow

    def test_runs_smoke_test(self, workflow):
        assert "test_frozen" in workflow

    def test_runs_electron_builder(self, workflow):
        assert "electron-builder" in workflow


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------

class TestGitignore:
    """Verify build artifacts are gitignored."""

    @pytest.fixture()
    def gitignore(self) -> str:
        return _read(ROOT / ".gitignore")

    @pytest.mark.parametrize(
        "pattern",
        ["backend/dist/", "backend/build/"],
    )
    def test_ignores_build_artifacts(self, gitignore, pattern):
        assert pattern in gitignore, f".gitignore must contain {pattern}"
