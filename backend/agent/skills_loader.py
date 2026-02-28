"""Skill loader – parses Markdown playbooks with YAML front-matter.

Each ``.md`` file in ``backend/agent/skills/`` defines a reusable analytical
playbook (a *Skill*).  The YAML front-matter carries structured metadata
while the Markdown body holds the step-by-step instructions injected into the
LLM system prompt when the skill is activated.

Usage::

    from agent.skills_loader import get_skill, list_skills

    skill = get_skill("dcf_analysis")          # Skill model or None
    all_names = list_skills()                   # ["dcf_analysis", ...]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import frontmatter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Directory that houses the ``.md`` skill files
SKILLS_DIR = Path(__file__).resolve().parent / "skills"


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    """Parsed representation of a single Markdown skill playbook."""

    name: str = Field(..., description="Unique slug identifying the skill.")
    description: str = Field(
        default="", description="Human-readable summary of what the skill does."
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="Tool names the skill expects to be available.",
    )
    instructions: str = Field(
        ..., description="Full Markdown body with step-by-step instructions.",
    )


# ---------------------------------------------------------------------------
# Internal cache (populated lazily on first access)
# ---------------------------------------------------------------------------

_cache: dict[str, Skill] = {}
_loaded: bool = False


def _load_all() -> None:
    """Scan ``SKILLS_DIR`` and populate the module-level cache."""
    global _loaded
    _cache.clear()

    if not SKILLS_DIR.is_dir():
        logger.warning("Skills directory not found: %s", SKILLS_DIR)
        _loaded = True
        return

    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            post = frontmatter.load(str(path))
            meta: dict = post.metadata  # type: ignore[assignment]
            name = meta.get("name", path.stem)
            skill = Skill(
                name=name,
                description=meta.get("description", ""),
                required_tools=meta.get("required_tools", []),
                instructions=post.content,
            )
            _cache[skill.name] = skill
            logger.info("Loaded skill: %s (%s)", skill.name, path.name)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load skill %s: %s", path.name, exc)

    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load_all()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_skill(name: str) -> Optional[Skill]:
    """Return a :class:`Skill` by name, or ``None`` if not found."""
    _ensure_loaded()
    return _cache.get(name)


def list_skills() -> list[str]:
    """Return a sorted list of all available skill names."""
    _ensure_loaded()
    return sorted(_cache.keys())


def reload_skills() -> None:
    """Force a re-scan of the skills directory (e.g. after hot-adding files)."""
    global _loaded
    _loaded = False
    _ensure_loaded()
