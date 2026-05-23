import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings
from pawchestrator.db import init_db
from pawchestrator.implement import (
    ImplementationResult,
    WorktreeInfo,
    build_implement_prompt,
    ensure_issue_worktree,
    files_changed_from_diff,
    run_implement,
    slugify,
)
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(
        self,
        *,
        healthy: bool = True,
        result: RunnerResult | None = None,
    ) -> None:
        self.healthy = healthy
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
        self.task = task
        return self.result


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
        "steps": [{"order": 1, "description": "Edit code."}],
    }

    prompt = build_implement_prompt(snapshot, plan, tmp_path)

    assert "Issue: #42 - Implement" in prompt
    assert "Repository: owner/repo" in prompt
    assert f"Working directory: {tmp_path}" in prompt
    assert "Issue body" in prompt
    assert "pawchestrator.implementation_plan.v1" in prompt
    assert "Do not run build or test commands" in prompt


def test_files_changed_from_diff_dedupes_paths() -> None:
    diff = "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "diff --git a/b.py b/b.py",
            "diff --git a/a.py b/a.py",
        ]
    )

    assert files_changed_from_diff(diff) == ["a.py", "b.py"]


def test_ensure_issue_worktree_reuses_existing_worktree(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    worktree_path = tmp_path / "worktrees" / "owner" / "repo" / "issue-42"
    worktree_path.mkdir(parents=True)
    (worktree_path / ".git").write_text("gitdir: source", encoding="utf-8")

    info = asyncio.run(
        ensure_issue_worktree(
            settings,
            snapshot=_snapshot(),
            source_repo_path=tmp_path / "repo",
        )
    )

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


def test_run_implement_writes_report_log_and_records_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_plan_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_plan(settings, run_id)
    runner = FakeRunner()
    worktree_path = tmp_path / "worktree"

    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot: dict[str, Any],
        source_repo_path: Path,
    ) -> WorktreeInfo:
        assert source_repo_path == (tmp_path / "source").resolve()
        return WorktreeInfo(
            path=worktree_path,
            branch="paw/issue-42-add-implement",
            reused=False,
        )

    monkeypatch.setattr(
        "pawchestrator.implement.ensure_issue_worktree",
        fake_ensure_issue_worktree,
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
    assert "implementation plan not found" in stage[1]


def test_run_implement_command_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    async def fake_run_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
    ) -> ImplementationResult:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path
        assert repo_path is None
        return ImplementationResult(
            run_id=run_id,
            artifact_path=tmp_path / "runs" / run_id / "implementation_report.json",
            log_path=tmp_path / "runs" / run_id / "stdout" / "implement.log",
            worktree_path=tmp_path / "worktree",
            branch="paw/issue-42-add-implement",
            report={
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
