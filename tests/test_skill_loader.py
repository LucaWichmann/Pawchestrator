from pathlib import Path

import pytest

from pawchestrator.skill_loader import load_skill


def test_load_skill_returns_bundled_content() -> None:
    result = load_skill("RepoScout")
    assert result is not None
    assert "scout_report" in result
    assert "No prose. No progress updates. Emit valid JSON artifact only." in result


def test_load_skill_returns_none_for_unknown_skill() -> None:
    assert load_skill("NonExistentSkill") is None


def test_load_skill_user_override_takes_precedence(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "RepoScout"
    skill_dir.mkdir(parents=True)
    (skill_dir / "Skill.md").write_text("custom instructions", encoding="utf-8")

    result = load_skill("RepoScout", app_dir=tmp_path)
    assert result == "custom instructions"


def test_load_skill_falls_back_to_bundled_when_no_override(tmp_path: Path) -> None:
    result = load_skill("RepoScout", app_dir=tmp_path)
    assert result is not None
    assert "scout_report" in result


def test_load_skill_all_bundled_skills_present() -> None:
    for skill_name in (
        "RepoScout",
        "ImplementationPlan",
        "WorkOnIssue",
        "RepairVerification",
        "IssueGrill",
        "CriteriaDedupe",
        "PullRequestReview",
    ):
        result = load_skill(skill_name)
        assert result is not None, f"bundled skill missing: {skill_name}"
        assert len(result) > 20, f"bundled skill suspiciously short: {skill_name}"


def test_load_skill_review_instructions_use_commentable_lines() -> None:
    result = load_skill("PullRequestReview")

    assert result is not None
    assert "Commentable added lines" in result
    assert "Do not use diff positions" in result
