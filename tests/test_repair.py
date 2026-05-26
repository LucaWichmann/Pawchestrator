import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_repair_run,
    get_run_warnings,
    init_db,
    insert_repo_registration,
)
from pawchestrator.implement import (
    WorktreeInfo,
    build_repair_prompt,
    ensure_pr_worktree,
    run_repair,
)
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


class FakeRepairClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reviewers = ["alice", "bob"]
        self.requested_reviewers: list[tuple[str, str, int, list[str]]] = []

    async def fetch_pr_head_branch(self, owner: str, repo: str, number: int) -> str:
        self.calls.append(f"head:{owner}/{repo}#{number}")
        return "owner:feature"

    async def fetch_review_comments(self, owner: str, repo: str, number: int):
        self.calls.append(f"comments:{owner}/{repo}#{number}")
        return {
            "inline_comments": [{"path": "app.py", "line": 4, "body": "Fix inline"}],
            "top_level_comments": [{"body": "Fix summary"}],
        }

    async def fetch_pr_diff(self, owner: str, repo: str, number: int) -> str:
        self.calls.append(f"diff:{owner}/{repo}#{number}")
        return "diff --git a/app.py b/app.py\n"

    async def fetch_changes_requested_reviewers(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> list[str]:
        self.calls.append(f"reviews:{owner}/{repo}#{number}")
        return self.reviewers

    async def request_review(
        self,
        owner: str,
        repo: str,
        number: int,
        reviewers: list[str],
    ) -> None:
        self.calls.append(f"request-review:{owner}/{repo}#{number}")
        self.requested_reviewers.append((owner, repo, number, reviewers))


class FakeRepairRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(self) -> None:
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return True, "ok"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.task = task
        return RunnerResult(
            exit_code=0,
            stdout="fixed\n",
            stderr="",
            artifact=None,
            diff="diff --git a/app.py b/app.py\n",
        )


def test_build_repair_prompt_includes_comments_and_diff(tmp_path: Path) -> None:
    prompt = build_repair_prompt(
        owner="owner",
        repo="repo",
        pr_number=42,
        worktree_path=tmp_path,
        review_comments={
            "inline_comments": [{"body": "Fix inline"}],
            "top_level_comments": [{"body": "Fix summary"}],
        },
        diff="diff --git a/app.py b/app.py\n",
    )

    assert "Repair pull request owner/repo#42" in prompt
    assert "Fix inline" in prompt
    assert "Fix summary" in prompt
    assert "diff --git a/app.py b/app.py" in prompt
    assert "Commit the" in prompt


def test_ensure_pr_worktree_fetches_remote_branch_and_creates_fresh_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], Path]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append((args, cwd))
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)
    monkeypatch.setattr("pawchestrator.implement.uuid_slug", lambda: "abc12345")

    info = asyncio.run(
        ensure_pr_worktree(
            Settings(app_dir=tmp_path),
            source_repo_path=tmp_path / "repo",
            owner="owner",
            repo="repo",
            pr_number=42,
            head_branch="owner:feature",
        )
    )

    assert info == WorktreeInfo(
        path=tmp_path / "worktrees" / "owner" / "repo" / "repair-42-abc12345",
        branch="paw/repair-pr-42-abc12345",
        reused=False,
    )
    assert calls == [
        (
            ["fetch", "origin", "+refs/heads/feature:refs/remotes/origin/feature"],
            tmp_path / "repo",
        ),
        (
            [
                "worktree",
                "add",
                "-b",
                "paw/repair-pr-42-abc12345",
                str(info.path),
                "refs/remotes/origin/feature",
            ],
            tmp_path / "repo",
        ),
    ]


def test_run_repair_invokes_agent_with_review_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "repair-run"
    asyncio.run(_seed_repair_run(settings, run_id))
    client = FakeRepairClient()
    runner = FakeRepairRunner()
    worktree = tmp_path / "worktree"

    async def fake_ensure_pr_worktree(
        settings: Settings,
        *,
        source_repo_path: Path,
        owner: str,
        repo: str,
        pr_number: int,
        head_branch: str,
    ) -> WorktreeInfo:
        assert source_repo_path == tmp_path / "repo"
        assert head_branch == "owner:feature"
        return WorktreeInfo(path=worktree, branch="paw/repair-pr-42", reused=False)

    monkeypatch.setattr("pawchestrator.implement.ensure_pr_worktree", fake_ensure_pr_worktree)
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value("diff --git a/app.py b/app.py\n"),
    )
    git_calls: list[tuple[list[str], Path]] = []

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        git_calls.append((args, cwd))
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)

    result = asyncio.run(run_repair(run_id, settings, client=client, runner=runner))

    assert runner.task is not None
    assert runner.task.cwd == worktree
    assert runner.task.stage_name == "repair"
    assert "Fix inline" in runner.task.prompt
    assert "Fix summary" in runner.task.prompt
    assert result.report["status"] == "success"
    assert result.report["files_changed"] == ["app.py"]
    assert git_calls == [(["push", "origin", "paw/repair-pr-42"], worktree)]
    assert client.requested_reviewers == [
        ("owner", "repo", 42, ["alice", "bob"]),
    ]

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT workflow_type, status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stages = db.execute(
            "SELECT stage_name, status FROM workflow_stages WHERE run_id = ?",
            (run_id,),
        ).fetchall()

    assert run == ("repair", "push_complete", "push")
    assert stages == [("repair", "complete"), ("push", "complete")]


def test_run_repair_push_warns_when_no_changes_requested_reviewers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "repair-run"
    asyncio.run(_seed_repair_run(settings, run_id))
    client = FakeRepairClient()
    client.reviewers = []
    runner = FakeRepairRunner()
    worktree = tmp_path / "worktree"

    async def fake_ensure_pr_worktree(
        settings: Settings,
        *,
        source_repo_path: Path,
        owner: str,
        repo: str,
        pr_number: int,
        head_branch: str,
    ) -> WorktreeInfo:
        return WorktreeInfo(path=worktree, branch="paw/repair-pr-42", reused=False)

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        return "", "", 0

    monkeypatch.setattr("pawchestrator.implement.ensure_pr_worktree", fake_ensure_pr_worktree)
    monkeypatch.setattr("pawchestrator.implement._run_git", fake_run_git)
    monkeypatch.setattr(
        "pawchestrator.implement._git_rev_parse_head",
        lambda cwd: _async_value("base-sha"),
    )
    monkeypatch.setattr(
        "pawchestrator.implement._diff_since",
        lambda cwd, base_commit: _async_value("diff --git a/app.py b/app.py\n"),
    )

    asyncio.run(run_repair(run_id, settings, client=client, runner=runner))

    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert client.requested_reviewers == []
    assert warnings == [
        {
            "id": warnings[0]["id"],
            "run_id": run_id,
            "stage_name": "push",
            "code": "no_changes_requested_reviewers",
            "message": "No CHANGES_REQUESTED reviewers found; skipped re-review request.",
            "created_at": warnings[0]["created_at"],
        }
    ]


async def _seed_repair_run(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    await insert_repo_registration(
        settings,
        owner="owner",
        repo="repo",
        local_path=settings.app_dir / "repo",
    )
    await create_repair_run(
        settings,
        run_id=run_id,
        owner="owner",
        repo="repo",
        pr_number=42,
    )


async def _async_value(value: Any) -> Any:
    return value
