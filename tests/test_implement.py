import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.codegraph import CodeGraphSyncResult
from pawchestrator.config import Settings
from pawchestrator.db import init_db
from pawchestrator.implement import (
    WorktreeInfo,
    build_implement_prompt,
    ensure_issue_worktree,
    files_changed_from_diff,
    run_implement,
    slugify,
)
from pawchestrator.runners import Runner, RunnerResult, RunnerTask
from pawchestrator.stage_lifecycle import StageResult


async def _async_value(value: Any) -> Any:
    return value


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(
        self,
        *,
        healthy: bool = True,
        result: RunnerResult | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.healthy = healthy
        self.events = events
        self.result = result or RunnerResult(
            exit_code=0,
            stdout="codex stdout\n",
            stderr="codex stderr\n",
            artifact=None,
            diff=(
                "diff --git a/pawchestrator/implement.py "
                "b/pawchestrator/implement.py\n"
            ),
        )
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return self.healthy, "codex missing"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        if self.events is not None:
            self.events.append("runner")
        self.task = task
        return self.result


def _successful_main_refresh(args: list[str]) -> tuple[str, str, int]:
    if args == ["branch", "--show-current"]:
        return "main\n", "", 0
    return "", "", 0


def test_slugify_matches_issue_branch_contract() -> None:
    assert slugify("Add Implement Stage!") == "add-implement-stage"
    assert len(slugify("x" * 80)) == 40
    assert slugify("!!!") == "issue"


def test_build_implement_prompt_includes_snapshot_plan_and_worktree(tmp_path: Path) -> None:
    snapshot = {
        "owner": "owner",
        "repo": "repo",
        "number": 42,
        "title": "Implement",
        "body": "Issue body",
    }
    plan = {
        "schema": "pawchestrator.implementation_plan.v1",
        "approach_summary": "Use existing orchestration.",
        "steps": [
            {
                "order": 1,
                "description": "Edit code.",
                "files_to_modify": ["pawchestrator/implement.py"],
                "notes": "Keep full artifact notes out of the prompt.",
            }
        ],
        "files_to_modify": ["pawchestrator/implement.py"],
        "estimated_risk": "low",
    }

    prompt = build_implement_prompt(snapshot, plan, tmp_path)

    assert "Issue: #42 - Implement" in prompt
    assert "Repository: owner/repo" in prompt
    assert f"Working directory: {tmp_path}" in prompt
    assert "Issue body" in prompt
    assert "Use existing orchestration." in prompt
    assert "pawchestrator/implement.py" in prompt
    assert "Keep full artifact notes out of the prompt." not in prompt
    assert "estimated_risk" not in prompt
    assert "pawchestrator.implementation_plan.v1" not in prompt
    assert "Do not run build | test commands" in prompt


def test_build_implement_prompt_compresses_plan_for_prompt_only(tmp_path: Path) -> None:
    approach_summary = "x" * 151
    plan = {
        "schema": "pawchestrator.implementation_plan.v1",
        "approach_summary": approach_summary,
        "steps": [
            {
                "order": 1,
                "description": "Edit code.",
                "files_to_modify": ["pawchestrator/implement.py"],
                "notes": "drop me",
            }
        ],
        "files_to_modify": ["pawchestrator/implement.py"],
        "estimated_risk": "low",
    }

    prompt = build_implement_prompt(_snapshot(), plan, tmp_path)

    assert ("x" * 150) in prompt
    assert ("x" * 151) not in prompt
    assert '"description": "Edit code."' in prompt
    assert '"files_to_modify": ["pawchestrator/implement.py"]' in prompt
    assert '"notes"' not in prompt
    assert plan["approach_summary"] == approach_summary
    assert "notes" in plan["steps"][0]


def test_build_implement_prompt_includes_checkbox_criteria(tmp_path: Path) -> None:
    snapshot = {
        **_snapshot(),
        "owner": "octo",
        "repo": "widgets",
        "number": 123,
        "checkboxes": [
            {"index": 0, "text": "Implement prompt includes checkbox list"},
            {"index": 3, "text": "Prompt includes correct issue reference"},
        ],
    }

    prompt = build_implement_prompt(snapshot, {"steps": []}, tmp_path)

    assert (
        "Acceptance criteria checkboxes — call "
        "`pawchestrator checkbox check octo/widgets/123 <index>` via Bash "
        "immediately after addressing each criterion:"
    ) in prompt
    assert "  0: Implement prompt includes checkbox list" in prompt
    assert "  3: Prompt includes correct issue reference" in prompt


def test_build_implement_prompt_omits_checkbox_criteria_when_empty(
    tmp_path: Path,
) -> None:
    prompt = build_implement_prompt(
        {**_snapshot(), "checkboxes": []},
        {"steps": []},
        tmp_path,
    )

    assert "Acceptance criteria checkboxes" not in prompt
    assert "pawchestrator checkbox check" not in prompt


def test_build_implement_prompt_includes_repair_context(tmp_path: Path) -> None:
    prompt = build_implement_prompt(
        _snapshot(),
        {"steps": [{"description": "Fix tests.", "files_to_modify": ["tests/test_x.py"]}]},
        tmp_path,
        repair_context={
            "status": "failed",
            "commands": [
                {
                    "command": "pytest",
                    "exit_code": 1,
                    "stderr_summary": "assertion failed",
                }
            ],
            "verify_log_tail": "FAILED tests/test_x.py",
        },
        repair_attempt=2,
    )

    assert "Verification failed after implementation" in prompt
    assert "Repair attempt: 2" in prompt
    assert "FAILED tests/test_x.py" in prompt
    assert '"command": "pytest"' in prompt


def test_files_changed_from_diff_dedupes_paths() -> None:
    diff = "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "diff --git a/b.py b/b.py",
            "diff --git a/a.py b/a.py",
        ]
    )

    assert files_changed_from_diff(diff) == ["a.py", "b.py"]


def test_ensure_issue_worktree_reuses_existing_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    worktree_path = tmp_path / "worktrees" / "owner" / "repo" / "issue-42"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: source", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append((args, cwd))
        return _successful_main_refresh(args)

    source_repo_path = tmp_path / "repo"

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    info = asyncio.run(
        ensure_issue_worktree(
            settings,
            snapshot=_snapshot(),
            source_repo_path=source_repo_path,
        )
    )

    assert calls[-2:] == [
        (["status", "--porcelain"], worktree_path),
        (["merge", "--ff-only", "main"], worktree_path),
    ]
    assert info == WorktreeInfo(
        path=worktree_path,
        branch="paw/issue-42-add-implement",
        reused=True,
    )


def test_ensure_issue_worktree_creates_branch_and_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Path]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append((args, cwd))
        if args[:1] in [["fetch"], ["branch"], ["status"], ["merge"]]:
            return _successful_main_refresh(args)
        if args[:2] == ["rev-parse", "--verify"]:
            return "", "missing", 1
        return "created", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    info = asyncio.run(
        ensure_issue_worktree(
            Settings(app_dir=tmp_path),
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "source",
        )
    )

    assert info.path == tmp_path / "worktrees" / "owner" / "repo" / "issue-42"
    assert info.branch == "paw/issue-42-add-implement"
    assert calls == [
        (
            ["fetch", "origin", "main:refs/remotes/origin/main"],
            tmp_path / "source",
        ),
        (["branch", "--show-current"], tmp_path / "source"),
        (["status", "--porcelain"], tmp_path / "source"),
        (["merge", "--ff-only", "origin/main"], tmp_path / "source"),
        (
            ["rev-parse", "--verify", "refs/heads/paw/issue-42-add-implement"],
            tmp_path / "source",
        ),
        (
            [
                "worktree",
                "add",
                "-b",
                "paw/issue-42-add-implement",
                str(info.path),
                "main",
            ],
            tmp_path / "source",
        ),
    ]


def test_ensure_issue_worktree_uses_existing_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(args)
        if args[:1] in [["fetch"], ["branch"], ["status"], ["merge"]]:
            return _successful_main_refresh(args)
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    info = asyncio.run(
        ensure_issue_worktree(
            Settings(app_dir=tmp_path),
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "source",
        )
    )

    assert calls[-1] == [
        "worktree",
        "add",
        str(info.path),
        "paw/issue-42-add-implement",
    ]


def test_ensure_issue_worktree_accepts_custom_branch_path_and_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    custom_path = tmp_path / "worktrees" / "owner" / "repo" / "epic-42"

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(args)
        if args == ["rev-parse", "--verify", "refs/heads/paw/epic-42-parent"]:
            return "ok", "", 0
        if args == ["rev-parse", "--verify", "refs/heads/paw/child-43"]:
            return "", "missing", 1
        if args[:3] == ["worktree", "add", "-b"]:
            return "", "", 0
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    info = asyncio.run(
        ensure_issue_worktree(
            Settings(app_dir=tmp_path),
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "source",
            branch_override="paw/child-43",
            path_override=custom_path,
            base_branch="paw/epic-42-parent",
        )
    )

    assert info.path == custom_path
    assert info.branch == "paw/child-43"
    assert calls == [
        ["rev-parse", "--verify", "refs/heads/paw/epic-42-parent"],
        ["rev-parse", "--verify", "refs/heads/paw/child-43"],
        [
            "worktree",
            "add",
            "-b",
            "paw/child-43",
            str(custom_path),
            "paw/epic-42-parent",
        ],
    ]


def test_ensure_issue_worktree_updates_main_when_source_not_on_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(args)
        if args == ["branch", "--show-current"]:
            return "feature\n", "", 0
        if args[:2] == ["rev-parse", "--verify"]:
            return "", "", 0
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return "", "", 0
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    asyncio.run(
        ensure_issue_worktree(
            Settings(app_dir=tmp_path),
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "source",
        )
    )

    assert ["update-ref", "refs/heads/main", "refs/remotes/origin/main"] in calls


def test_ensure_issue_worktree_fails_when_source_main_is_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        if args == ["status", "--porcelain"]:
            return " M file.py\n", "", 0
        return _successful_main_refresh(args)

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    with pytest.raises(RuntimeError, match="source repo main has uncommitted changes"):
        asyncio.run(
            ensure_issue_worktree(
                Settings(app_dir=tmp_path),
                snapshot=_snapshot(),
                source_repo_path=tmp_path / "source",
            )
        )


def test_ensure_issue_worktree_fails_when_main_diverged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        if args == ["branch", "--show-current"]:
            return "feature\n", "", 0
        if args[:2] == ["rev-parse", "--verify"]:
            return "", "", 0
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return "", "", 1
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    with pytest.raises(RuntimeError, match="local main cannot fast-forward"):
        asyncio.run(
            ensure_issue_worktree(
                Settings(app_dir=tmp_path),
                snapshot=_snapshot(),
                source_repo_path=tmp_path / "source",
            )
        )


def test_ensure_issue_worktree_fails_when_existing_worktree_is_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_path = tmp_path / "worktrees" / "owner" / "repo" / "issue-42"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: source", encoding="utf-8")

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        if args == ["status", "--porcelain"] and cwd == worktree_path:
            return " M file.py\n", "", 0
        return _successful_main_refresh(args)

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    with pytest.raises(RuntimeError, match="issue worktree has uncommitted changes"):
        asyncio.run(
            ensure_issue_worktree(
                Settings(app_dir=tmp_path),
                snapshot=_snapshot(),
                source_repo_path=tmp_path / "source",
            )
        )


def test_ensure_issue_worktree_allows_dirty_existing_worktree_for_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_path = tmp_path / "worktrees" / "owner" / "repo" / "issue-42"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: source", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append((args, cwd))
        if args == ["status", "--porcelain"] and cwd == worktree_path:
            return " M file.py\n", "", 0
        return _successful_main_refresh(args)

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    info = asyncio.run(
        ensure_issue_worktree(
            Settings(app_dir=tmp_path),
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "source",
            allow_dirty_existing_worktree=True,
        )
    )

    assert info == WorktreeInfo(
        path=worktree_path,
        branch="paw/issue-42-add-implement",
        reused=True,
    )
    assert calls == []


def test_run_implement_writes_report_log_and_records_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    events: list[str] = []
    runner = FakeRunner(events=events)
    worktree_path = tmp_path / "worktree"

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert source_repo_path == (tmp_path / "source").resolve()
        assert allow_dirty_existing_worktree is False
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/issue-42-add-implement",
            reused=False,
        )

    async def fake_sync_back_if_merged(
        settings: Settings,
        *,
        source_repo_path: Path,
        worktree_path: Path,
        branch: str,
    ) -> CodeGraphSyncResult:
        events.append("sync-back")
        return CodeGraphSyncResult(
            action="skipped",
            source=worktree_path,
            destination=source_repo_path,
            message="branch not merged into main",
        )

    async def fake_seed_worktree_index(
        settings: Settings,
        *,
        source_repo_path: Path,
        worktree_path: Path,
    ) -> CodeGraphSyncResult:
        events.append("seed")
        return CodeGraphSyncResult(
            action="copied",
            source=source_repo_path,
            destination=worktree_path,
            message="seeded worktree CodeGraph index",
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement.sync_back_if_merged",
        fake_sync_back_if_merged,
    )
    monkeypatch.setattr(
        "pawchestrator.implement.seed_worktree_index",
        fake_seed_worktree_index,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(runner.result.diff),
    )

    result = asyncio.run(
        run_implement(
            run_id,
            settings,
            repo_path=tmp_path / "source",
            runner=runner,
        )
    )

    assert runner.task is not None
    assert events == ["sync-back", "seed", "runner"]
    assert runner.task.cwd == worktree_path
    assert runner.task.stage_name == "implement"
    assert "IssueSnapshot JSON" in runner.task.prompt
    assert result.artifact_path == tmp_path / "runs" / run_id / "implementation_report.json"
    assert result.log_path == tmp_path / "runs" / run_id / "stdout" / "implement.log"

    report = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    log = result.log_path.read_text(encoding="utf-8")
    assert report["schema"] == "pawchestrator.implementation_report.v1"
    assert report["status"] == "success"
    assert report["files_changed"] == ["pawchestrator/implement.py"]
    assert report["diff_summary"] == "1 file changed: pawchestrator/implement.py"
    assert report["codex_output"] == "codex stdout\ncodex stderr\n"
    assert "[stdout]" in log
    assert "codex stdout" in log
    assert "[codegraph] seed copied: seeded worktree CodeGraph index" in log

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'implement'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'implementation_report'
            """,
            (run_id,),
        ).fetchone()
        worktree = db.execute(
            "SELECT branch, path FROM worktrees WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    assert run == ("implement_complete", "implement")
    assert stage == ("complete", None)
    assert artifact == ("implementation_report", str(result.artifact_path))
    assert worktree == ("paw/issue-42-add-implement", str(worktree_path))


def test_run_implement_records_failure_and_error_report(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)

    with pytest.raises(FileNotFoundError, match="implementation plan not found"):
        asyncio.run(run_implement(run_id, settings, repo_path=tmp_path, runner=FakeRunner()))

    report_path = tmp_path / "runs" / run_id / "implementation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert "implementation plan not found" in report["error"]

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'implement'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("implement_failed", "implement")
    assert stage[0] == "failed"
    assert stage[1] == "Stage failed. See local run logs."


def test_run_implement_continues_when_codegraph_seed_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    worktree_path = tmp_path / "worktree"
    runner = FakeRunner()

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert allow_dirty_existing_worktree is False
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/issue-42-add-implement",
            reused=False,
        )

    async def fake_seed_worktree_index(
        settings: Settings,
        *,
        source_repo_path: Path,
        worktree_path: Path,
    ) -> CodeGraphSyncResult:
        raise RuntimeError("copy exploded")

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement.seed_worktree_index",
        fake_seed_worktree_index,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(runner.result.diff),
    )

    result = asyncio.run(
        run_implement(
            run_id,
            settings,
            repo_path=tmp_path / "source",
            runner=runner,
        )
    )

    assert result.report["status"] == "success"
    assert runner.task is not None
    log = result.log_path.read_text(encoding="utf-8")
    assert "[codegraph] seed warning: copy exploded" in log


def test_run_implement_fails_when_codex_changes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    worktree_path = tmp_path / "worktree"
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=0,
            stdout="no changes\n",
            stderr="",
            artifact=None,
            diff="",
        )
    )

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert allow_dirty_existing_worktree is False
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/issue-42-add-implement",
            reused=False,
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(""),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._committed_diff_against_base",
        lambda cwd, base_branch: _async_value(""),
    )

    with pytest.raises(RuntimeError, match="without changing files"):
        asyncio.run(
            run_implement(run_id, settings, repo_path=tmp_path / "source", runner=runner)
        )

    report_path = tmp_path / "runs" / run_id / "implementation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert report["files_changed"] == []
    assert report["error"] == "Codex completed without changing files"

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'implement'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("implement_failed", "implement")
    assert stage[0] == "failed"
    assert stage[1] == "Stage failed. See local run logs."


def test_run_implement_dirty_start_fails_when_runner_adds_no_new_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    worktree_path = tmp_path / "worktree"
    dirty_diff = "diff --git a/old.py b/old.py\n"
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=0,
            stdout="no new changes\n",
            stderr="",
            artifact=None,
            diff=dirty_diff,
        )
    )

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert allow_dirty_existing_worktree is True
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/epic-42-parent",
            reused=True,
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(dirty_diff),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._committed_diff_against_base",
        lambda cwd, base_branch: _async_value(""),
    )

    with pytest.raises(RuntimeError, match="without changing files"):
        asyncio.run(
            run_implement(
                run_id,
                settings,
                repo_path=tmp_path / "source",
                runner=runner,
                allow_dirty_existing_worktree=True,
            )
        )

    report_path = tmp_path / "runs" / run_id / "implementation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert report["files_changed"] == []


def test_run_implement_detects_committed_changes_since_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    worktree_path = tmp_path / "worktree"
    committed_diff = "diff --git a/pawchestrator/db.py b/pawchestrator/db.py\n"
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=0,
            stdout="committed changes\n",
            stderr="",
            artifact=None,
            diff="",
        )
    )

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert allow_dirty_existing_worktree is False
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/issue-42-add-implement",
            reused=True,
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(committed_diff),
    )

    result = asyncio.run(
        run_implement(
            run_id,
            settings,
            repo_path=tmp_path / "source",
            runner=runner,
        )
    )

    assert result.report["status"] == "success"
    assert result.report["files_changed"] == ["pawchestrator/db.py"]
    assert result.report["diff_summary"] == "1 file changed: pawchestrator/db.py"


def test_run_implement_accepts_existing_branch_changes_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    worktree_path = tmp_path / "worktree"
    dirty_diff = "diff --git a/.codegraph/config.json b/.codegraph/config.json\n"
    branch_diff = "diff --git a/pawchestrator/db.py b/pawchestrator/db.py\n"
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=0,
            stdout="Implemented and committed already.\n",
            stderr="",
            artifact=None,
            diff=dirty_diff,
        )
    )

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
        allow_dirty_existing_worktree: bool = False,
    ) -> WorktreeInfo:
        assert allow_dirty_existing_worktree is True
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/epic-42-parent",
            reused=True,
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
    )
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value(dirty_diff),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._committed_diff_against_base",
        lambda cwd, base_branch: _async_value(branch_diff),
    )

    result = asyncio.run(
        run_implement(
            run_id,
            settings,
            repo_path=tmp_path / "source",
            runner=runner,
            allow_dirty_existing_worktree=True,
        )
    )

    assert result.report["status"] == "success"
    assert result.report["files_changed"] == ["pawchestrator/db.py"]


def test_run_implement_command_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    async def fake_run_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
    ) -> StageResult:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path
        assert repo_path is None
        return StageResult(
            run_id=run_id,
            artifact_path=tmp_path / "runs" / run_id / "implementation_report.json",
            log_path=tmp_path / "runs" / run_id / "stdout" / "implement.log",
            report={
                "worktree_path": str(tmp_path / "worktree"),
                "branch": "paw/issue-42-add-implement",
                "files_changed": ["pawchestrator/implement.py"],
                "diff_summary": "1 file changed: pawchestrator/implement.py",
            },
        )

    monkeypatch.setattr(cli, "run_implement", fake_run_implement)

    result = CliRunner().invoke(cli.app, ["run", "implement", "run-123"])

    assert result.exit_code == 0
    assert f"Worktree: {tmp_path / 'worktree'}" in result.output
    assert "Branch: paw/issue-42-add-implement" in result.output
    assert "Changed files: 1" in result.output
    assert "- pawchestrator/implement.py" in result.output


def test_run_implement_reports_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with pytest.raises(ValueError, match="run not found: missing"):
        asyncio.run(run_implement("missing", settings, repo_path=tmp_path, runner=FakeRunner()))


async def _insert_plan_run(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'plan_complete', 'plan',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at, completed_at
            )
            VALUES (
              'stage-123', ?, 'plan', 'complete',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.commit()


def _snapshot() -> dict[str, Any]:
    return {
        "schema": "pawchestrator.issue_snapshot.v1",
        "owner": "owner",
        "repo": "repo",
        "number": 42,
        "title": "Add implement",
        "body": "Issue body",
        "comments": [],
    }


def _write_snapshot(settings: Settings, run_id: str) -> None:
    path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_snapshot()), encoding="utf-8")


def _write_plan(settings: Settings, run_id: str) -> None:
    path = settings.app_dir / "runs" / run_id / "implementation_plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.implementation_plan.v1",
                "approach_summary": "Add implement stage.",
                "steps": [
                    {
                        "order": 1,
                        "description": "Add orchestration.",
                        "files_to_modify": ["pawchestrator/implement.py"],
                        "notes": "",
                    }
                ],
                "files_to_modify": ["pawchestrator/implement.py"],
                "estimated_risk": "medium",
            }
        ),
        encoding="utf-8",
    )
