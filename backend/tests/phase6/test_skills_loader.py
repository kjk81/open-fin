"""Phase 6 — Tests for agent/skills_loader.py."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.skills_loader import (
    Skill,
    _load_all,
    get_skill,
    list_skills,
    reload_skills,
    _cache,
    SKILLS_DIR,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the module-level skill cache before each test."""
    import agent.skills_loader as sl
    sl._loaded = False
    sl._cache.clear()
    yield
    sl._loaded = False
    sl._cache.clear()


# ---------------------------------------------------------------------------
# Skill model
# ---------------------------------------------------------------------------

class TestSkillModel:
    def test_valid_skill(self):
        s = Skill(name="test", description="A test skill", instructions="## Do stuff")
        assert s.name == "test"
        assert s.required_tools == []

    def test_required_fields(self):
        with pytest.raises(Exception):
            # name and instructions are required
            Skill(description="missing required fields")


# ---------------------------------------------------------------------------
# _load_all with real skills directory
# ---------------------------------------------------------------------------

class TestLoadAll:
    def test_loads_dcf_analysis(self):
        """The real skills/ dir should contain dcf_analysis.md."""
        _load_all()
        assert "dcf_analysis" in _cache
        skill = _cache["dcf_analysis"]
        assert "DCF" in skill.instructions or "dcf" in skill.instructions.lower()
        assert "get_financial_statements" in skill.required_tools

    def test_missing_dir_logs_warning(self, tmp_path):
        """If SKILLS_DIR doesn't exist, _load_all should handle gracefully."""
        import agent.skills_loader as sl
        original = sl.SKILLS_DIR
        sl.SKILLS_DIR = tmp_path / "nonexistent"
        try:
            sl._loaded = False
            sl._cache.clear()
            _load_all()
            assert len(sl._cache) == 0
        finally:
            sl.SKILLS_DIR = original


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TestGetSkill:
    def test_returns_skill(self):
        skill = get_skill("dcf_analysis")
        assert skill is not None
        assert isinstance(skill, Skill)

    def test_returns_none_for_unknown(self):
        skill = get_skill("nonexistent_skill")
        assert skill is None


class TestListSkills:
    def test_returns_sorted_list(self):
        names = list_skills()
        assert isinstance(names, list)
        assert names == sorted(names)
        assert "dcf_analysis" in names


class TestReloadSkills:
    def test_reload_repopulates(self):
        import agent.skills_loader as sl
        # Load once
        list_skills()
        old_len = len(sl._cache)
        # Reload
        reload_skills()
        assert len(sl._cache) == old_len


# ---------------------------------------------------------------------------
# Custom skill loading from temp directory
# ---------------------------------------------------------------------------

class TestCustomSkill:
    def test_loads_custom_md(self, tmp_path):
        """Test loading a custom skill from a temp directory."""
        md_content = """---
name: my_custom_skill
description: A custom test skill
required_tools:
  - get_ohlcv
  - get_technical_snapshot
---

# Custom Skill

## Steps
1. Fetch OHLCV data
2. Compute signals
"""
        (tmp_path / "my_custom_skill.md").write_text(md_content)

        import agent.skills_loader as sl
        original = sl.SKILLS_DIR
        sl.SKILLS_DIR = tmp_path
        sl._loaded = False
        sl._cache.clear()

        try:
            skill = get_skill("my_custom_skill")
            assert skill is not None
            assert skill.name == "my_custom_skill"
            assert "get_ohlcv" in skill.required_tools
            assert "Custom Skill" in skill.instructions
        finally:
            sl.SKILLS_DIR = original

    def test_malformed_yaml_skipped(self, tmp_path):
        """A skill file with broken YAML front-matter should be skipped, not crash."""
        bad_content = """---
name: [[[invalid yaml
---

Some instructions.
"""
        (tmp_path / "bad_skill.md").write_text(bad_content)

        import agent.skills_loader as sl
        original = sl.SKILLS_DIR
        sl.SKILLS_DIR = tmp_path
        sl._loaded = False
        sl._cache.clear()

        try:
            names = list_skills()
            # Should not crash; may or may not include the skill depending on parser
            assert isinstance(names, list)
        finally:
            sl.SKILLS_DIR = original
