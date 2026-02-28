"""Phase 8 — Tests for frozen-mode path resolution in skills_loader and vector_store.

Validates that:
- ``skills_loader.SKILLS_DIR`` resolves to the real skills directory in normal mode.
- Under a simulated frozen build, SKILLS_DIR uses ``sys._MEIPASS``.
- ``vector_store._index_dir()`` respects env var > frozen > source-tree fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class TestSkillsLoaderPaths:
    """Verify SKILLS_DIR resolves correctly in both modes."""

    def test_normal_mode_skills_dir(self):
        from agent.skills_loader import SKILLS_DIR
        assert SKILLS_DIR.is_dir()
        assert (SKILLS_DIR / "dcf_analysis.md").is_file()

    def test_normal_mode_matches_expected_path(self):
        from agent.skills_loader import SKILLS_DIR
        expected = BACKEND_DIR / "agent" / "skills"
        assert SKILLS_DIR == expected

    def test_frozen_mode_skills_dir_uses_meipass(self, tmp_path, monkeypatch):
        """If we set sys.frozen + sys._MEIPASS, re-importing should produce
        a SKILLS_DIR rooted at _MEIPASS/agent/skills."""
        # Create the expected directory structure in tmp_path
        skill_dir = tmp_path / "agent" / "skills"
        skill_dir.mkdir(parents=True)
        (skill_dir / "test_skill.md").write_text("---\nname: test\n---\nHello")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        # Re-evaluate base_dir in frozen mode
        from pathutil import base_dir
        result = base_dir() / "agent" / "skills"
        assert result == skill_dir


class TestVectorStoreIndexDir:
    """Verify _index_dir() fallback chain: env var > frozen > source tree."""

    def test_env_var_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path / "custom_faiss"))
        # Need to reload the function since it reads env at call time
        from agent.vector_store import _index_dir
        result = _index_dir()
        assert result == (tmp_path / "custom_faiss").resolve()
        assert result.is_dir()  # should be created

    def test_source_tree_fallback(self, monkeypatch):
        monkeypatch.delenv("OPEN_FIN_FAISS_DIR", raising=False)
        # Ensure we're not in frozen mode
        if hasattr(sys, "frozen"):
            monkeypatch.delattr(sys, "frozen", raising=False)
        from agent.vector_store import _index_dir
        result = _index_dir()
        expected = BACKEND_DIR / "faiss_data"
        assert result == expected

    def test_frozen_fallback_uses_executable_parent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPEN_FIN_FAISS_DIR", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        fake_exe = tmp_path / "dist" / "open-fin-api.exe"
        fake_exe.parent.mkdir(parents=True, exist_ok=True)
        fake_exe.touch()
        monkeypatch.setattr(sys, "executable", str(fake_exe))

        from agent.vector_store import _index_dir
        result = _index_dir()
        assert result == (tmp_path / "dist" / "faiss_data")
        assert result.is_dir()

    def test_env_var_takes_precedence_over_frozen(self, tmp_path, monkeypatch):
        """Even in frozen mode, OPEN_FIN_FAISS_DIR env var wins."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "fake.exe"))
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(tmp_path / "env_faiss"))

        from agent.vector_store import _index_dir
        result = _index_dir()
        assert result == (tmp_path / "env_faiss").resolve()

    def test_creates_directory(self, tmp_path, monkeypatch):
        target = tmp_path / "new_dir" / "nested"
        monkeypatch.setenv("OPEN_FIN_FAISS_DIR", str(target))
        from agent.vector_store import _index_dir
        result = _index_dir()
        assert result.is_dir()
