"""Path helpers that work under both normal Python and PyInstaller frozen builds.

When PyInstaller bundles the application, ``__file__`` references point inside
a temporary extraction folder.  ``sys._MEIPASS`` provides the root of that
extracted bundle.  The helpers here abstract this difference so the rest of
the codebase can resolve data-file paths without caring whether we are running
from source or from a frozen executable.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return ``True`` when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """Return the root directory of the backend package tree.

    * **Frozen (PyInstaller)**: ``sys._MEIPASS`` — the temp directory where
      PyInstaller extracted data files.
    * **Normal**: the directory containing *this* file, i.e. ``backend/``.
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent
