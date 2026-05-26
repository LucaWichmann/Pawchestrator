"""Skill file loader — bundled + optional user override."""

from __future__ import annotations

from pathlib import Path

_BUNDLED_SKILLS_DIR = Path(__file__).parent / "skills"


def load_skill(skill_name: str, app_dir: Path | None = None) -> str | None:
    """Return skill instructions from user override or bundled file, or None."""
    if app_dir is not None:
        override = app_dir / "skills" / skill_name / "Skill.md"
        if override.exists():
            return override.read_text(encoding="utf-8").strip()
    bundled = _BUNDLED_SKILLS_DIR / skill_name / "Skill.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8").strip()
    return None
