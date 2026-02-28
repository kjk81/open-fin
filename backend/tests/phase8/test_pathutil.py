"""Phase 8 — Tests for pathutil.py (PyInstaller frozen-mode path helper).

Validates:
- Normal mode: ``base_dir()`` returns the backend directory.
- Frozen simulation: ``base_dir()`` returns ``sys._MEIPASS``.
- ``is_frozen()`` reflects ``sys.frozen`` truthfully.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pathutil import base_dir, is_frozen


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # tests/phase8 -> tests -> backend


class TestIsFrozen:
    """is_frozen() should reflect sys.frozen."""

    def test_not_frozen_by_default(self):
        assert is_frozen() is False

    def test_frozen_when_attr_set(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert is_frozen() is True

    def test_not_frozen_when_attr_false(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert is_frozen() is False


class TestBaseDir:
    """base_dir() should resolve differently in normal vs frozen mode."""

    def test_normal_mode_returns_backend_dir(self):
        result = base_dir()
        # Should point at the backend/ directory (where pathutil.py lives)
        assert result == BACKEND_DIR
        assert result.is_dir()

    def test_normal_mode_contains_expected_files(self):
        d = base_dir()
        assert (d / "main.py").is_file()
        assert (d / "pathutil.py").is_file()
        assert (d / "agent").is_dir()

    def test_frozen_mode_returns_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        result = base_dir()
        assert result == tmp_path

    def test_frozen_mode_does_not_depend_on_file(self, monkeypatch, tmp_path):
        """Even if __file__ would resolve elsewhere, frozen mode uses _MEIPASS."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "custom"), raising=False)
        result = base_dir()
        assert result == tmp_path / "custom"


class TestBaseDirType:
    """Return type must always be Path."""

    def test_returns_path_instance(self):
        assert isinstance(base_dir(), Path)

    def test_frozen_returns_path_instance(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert isinstance(base_dir(), Path)
