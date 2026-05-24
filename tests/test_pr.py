import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import PrSettings, Settings
from pawchestrator.db import get_run_warnings, init_db
from pawchestrator.pr import PrDraftResult, build_pr_body, resolve_pr_assignees, run_pr


def test_build_pr_body_includes_required_sections_for_passed_verification() -> None:
    body = build_pr_body(
        _run_state(),
        _plan(),
        {"status": "passed", "commands": [], "skip_reason": None},
    )

    assert "## Summary\n\nAdd PR stage." in body
    assert "Fixes #42" in body
    assert "- Create draft PR." in body
    assert "All checks passed." in body
    assert "Internal artifacts are stored locally under run `run-123`" in body


def test_build_pr_body_reports_skipped_verification() -> None:
    body = build_pr_body(
        _run_state(),
        _plan(),
        {"status": "skipped", "commands": [], "skip_reason": "No config."},
    )

    assert "No config." in body


def test_build_pr_body_reports_failed_commands() -> None:
    body = build_pr_body(
        _run_state(),
        _plan(),
        {
            "status": "failed",
            "commands": [
                {"command": "pytest", "exit_code": 1},
                {"command": "ruff check .", "exit_code": 0},
            ],
            "skip_reason": None,
        },
    )

    assert "- `pytest` exit 1" in body
    assert "- `ruff check .` exit 0" in body


def test_run_pr_pushes_branch_creates_pr_writes_artifact_and_records_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    calls: list[tuple[list[str], Path]] = []

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append((cmd, cwd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "1\n", "", 0
        if cmd[:2] == ["git", "push"]:
            return "pushed", "", 0
        if cmd[:3] == ["gh", "pr", "create"]:
            return "https://github.com/owner/repo/pull/99\n", "", 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    result = asyncio.run(run_pr(run_id, settings))

    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert result.branch == "paw/issue-42-test"
    assert result.title == "fix: Add PR command (#42)"
    assert calls[0] == (
        ["git", "rev-list", "--count", "main..HEAD"],
        worktree_path,
    )
    assert calls[1] == (
        ["git", "push", "-u", "origin", "paw/issue-42-test"],
        worktree_path,
    )
    assert calls[2][0][:6] == [
        "gh",
        "pr",
        "create",
        "--title",
        "fix: Add PR command (#42)",
        "--body",
    ]
    assert "--draft" not in calls[2][0]
    assert "--base" in calls[2][0]
    assert "main" in calls[2][0]
    assert "--head" in calls[2][0]
    assert "paw/issue-42-test" in calls[2][0]
    assert "--assignee" in calls[2][0]
    assert "octo" in calls[2][0]

    draft = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert result.artifact_path == tmp_path / "runs" / run_id / "pr_draft.json"
    assert draft == {
        "schema": "pawchestrator.pr_draft.v1",
        "pr_url": "https://github.com/owner/repo/pull/99",
        "branch": "paw/issue-42-test",
        "base": "main",
        "title": "fix: Add PR command (#42)",
    }

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'pr'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'pr_draft'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("pr_complete", "pr", "https://github.com/owner/repo/pull/99")
    assert stage == ("complete", None)
    assert artifact == ("pr_draft", str(result.artifact_path))


def test_run_pr_includes_draft_flag_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path, pr=PrSettings(draft=True))
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    calls: list[list[str]] = []

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "1\n", "", 0
        if cmd[:2] == ["git", "push"]:
            return "pushed", "", 0
        if cmd[:3] == ["gh", "pr", "create"]:
            return "https://github.com/owner/repo/pull/99\n", "", 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    result = asyncio.run(run_pr(run_id, settings))

    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert calls[2][:4] == ["gh", "pr", "create", "--draft"]


def test_run_pr_reuses_existing_pr_for_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    calls: list[list[str]] = []

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "1\n", "", 0
        if cmd[:2] == ["git", "push"]:
            return "pushed", "", 0
        if cmd[:3] == ["gh", "pr", "create"]:
            return "", "a pull request already exists for branch", 1
        if cmd[:3] == ["gh", "pr", "view"]:
            return "https://github.com/owner/repo/pull/100\n", "", 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    result = asyncio.run(run_pr(run_id, settings))

    assert result.pr_url == "https://github.com/owner/repo/pull/100"
    assert calls[-1] == [
        "gh",
        "pr",
        "view",
        "paw/issue-42-test",
        "--json",
        "url",
        "--jq",
        ".url",
    ]


def test_run_pr_creates_empty_commit_when_allowed_and_branch_has_no_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    calls: list[list[str]] = []

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "0\n", "", 0
        if cmd[:3] == ["git", "commit", "--allow-empty"]:
            return "[paw/issue-42-test abc123] no-op", "", 0
        if cmd[:2] == ["git", "push"]:
            return "pushed", "", 0
        if cmd[:3] == ["gh", "pr", "create"]:
            return "https://github.com/owner/repo/pull/99\n", "", 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    result = asyncio.run(run_pr(run_id, settings, allow_empty_commit=True))

    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert calls[:3] == [
        ["git", "rev-list", "--count", "main..HEAD"],
        [
            "git",
            "commit",
            "--allow-empty",
            "-m",
            "chore(paw): record no-op for issue #42",
        ],
        ["git", "push", "-u", "origin", "paw/issue-42-test"],
    ]
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert run == ("pr_complete", "pr", "https://github.com/owner/repo/pull/99")


def test_run_pr_fails_before_create_when_empty_commit_not_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    calls: list[list[str]] = []

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "0\n", "", 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    message = "branch has no commits relative to main; cannot create PR"
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(run_pr(run_id, settings))

    assert calls == [["git", "rev-list", "--count", "main..HEAD"]]
    _assert_pr_failed(settings, run_id, message)


def test_run_pr_reports_missing_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run not found: missing"):
        asyncio.run(run_pr("missing", Settings(app_dir=tmp_path)))


def test_run_pr_records_failure_when_worktree_missing(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=None))
    _write_artifacts(settings, run_id)

    with pytest.raises(RuntimeError, match="worktree record not found"):
        asyncio.run(run_pr(run_id, settings))

    _assert_pr_failed(settings, run_id, "worktree record not found")


@pytest.mark.parametrize(
    ("artifact_name", "message"),
    [
        ("implementation_plan.json", "implementation_plan.json"),
        ("verification_report.json", "verification_report.json"),
    ],
)
def test_run_pr_records_failure_when_required_artifact_missing(
    tmp_path: Path,
    artifact_name: str,
    message: str,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)
    (tmp_path / "runs" / run_id / artifact_name).unlink()

    with pytest.raises(FileNotFoundError, match=message):
        asyncio.run(run_pr(run_id, settings))

    _assert_pr_failed(settings, run_id, message)


def test_run_pr_records_failure_when_gh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=worktree_path))
    _write_artifacts(settings, run_id)

    async def fake_run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return "1\n", "", 0
        if cmd[:2] == ["git", "push"]:
            return "pushed", "", 0
        return "", "gh auth failed", 1

    monkeypatch.setattr("pawchestrator.pr._run_process", fake_run_process)

    with pytest.raises(RuntimeError, match="gh auth failed"):
        asyncio.run(run_pr(run_id, settings))

    _assert_pr_failed(settings, run_id, "gh auth failed")


def test_run_pr_command_prints_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    async def fake_run_pr(
        run_id: str,
        settings: Settings,
        *,
        allow_empty_commit: bool = False,
    ) -> PrDraftResult:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path
        assert allow_empty_commit is False
        return PrDraftResult(
            run_id=run_id,
            artifact_path=tmp_path / "runs" / run_id / "pr_draft.json",
            pr_url="https://github.com/owner/repo/pull/99",
            branch="paw/issue-42-test",
            title="fix: Add PR command (#42)",
            draft={},
        )

    monkeypatch.setattr(cli, "run_pr", fake_run_pr)

    result = CliRunner().invoke(cli.app, ["run", "pr", "run-123"])

    assert result.exit_code == 0
    assert "https://github.com/owner/repo/pull/99" in result.output


def test_resolve_pr_assignees_returns_empty_when_assignment_disabled(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path, pr=PrSettings(assign=False))
    client = FakeAssignmentClient(["admin"])

    assignees = asyncio.run(
        resolve_pr_assignees(
            {"assignees": ["octo"]},
            settings,
            owner="owner",
            repo="repo",
            run_id="run-123",
            client=client,
        )
    )

    assert assignees == []
    assert client.calls == []


def test_resolve_pr_assignees_uses_snapshot_assignees_without_github_lookup(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    client = FakeAssignmentClient(["admin"])

    assignees = asyncio.run(
        resolve_pr_assignees(
            {"assignees": ["octo", "hubot"]},
            settings,
            owner="owner",
            repo="repo",
            run_id="run-123",
            client=client,
        )
    )

    assert assignees == ["octo", "hubot"]
    assert client.calls == []


def test_resolve_pr_assignees_falls_back_to_admin_collaborators(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    client = FakeAssignmentClient(["alice", "bob"])

    assignees = asyncio.run(
        resolve_pr_assignees(
            {"assignees": []},
            settings,
            owner="owner",
            repo="repo",
            run_id="run-123",
            client=client,
        )
    )

    assert assignees == ["alice", "bob"]
    assert client.calls == [("owner", "repo")]


def test_resolve_pr_assignees_warns_when_admin_lookup_fails(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=None))
    client = FakeAssignmentClient(RuntimeError("lookup failed"))

    assignees = asyncio.run(
        resolve_pr_assignees(
            {"assignees": []},
            settings,
            owner="owner",
            repo="repo",
            run_id=run_id,
            client=client,
        )
    )

    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert assignees == []
    assert [warning["code"] for warning in warnings] == ["assignment_lookup_failed"]
    assert warnings[0]["stage_name"] == "pr"
    assert "lookup failed" in warnings[0]["message"]


def test_resolve_pr_assignees_warns_when_admin_lookup_returns_empty(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_verify_run(settings, run_id, worktree_path=None))
    client = FakeAssignmentClient([])

    assignees = asyncio.run(
        resolve_pr_assignees(
            {"assignees": []},
            settings,
            owner="owner",
            repo="repo",
            run_id=run_id,
            client=client,
        )
    )

    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert assignees == []
    assert [warning["code"] for warning in warnings] == ["assignment_lookup_failed"]


def _run_state() -> dict[str, Any]:
    return {
        "id": "run-123",
        "owner": "owner",
        "repo": "repo",
        "issue_number": 42,
    }


def _plan() -> dict[str, Any]:
    return {
        "schema": "pawchestrator.implementation_plan.v1",
        "approach_summary": "Add PR stage.",
        "steps": [{"order": 1, "description": "Create draft PR."}],
    }


async def _insert_verify_run(
    settings: Settings,
    run_id: str,
    *,
    worktree_path: Path | None,
) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'verify_complete', 'verify',
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
              'stage-verify', ?, 'verify', 'complete',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        if worktree_path is not None:
            await db.execute(
                """
                INSERT INTO worktrees (
                  id, run_id, owner, repo, issue_number, branch, path,
                  created_at, updated_at
                )
                VALUES (
                  'worktree-123', ?, 'owner', 'repo', 42,
                  'paw/issue-42-test', ?,
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
                )
                """,
                (run_id, str(worktree_path)),
            )
        await db.commit()


def _write_artifacts(settings: Settings, run_id: str) -> None:
    run_dir = settings.app_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "issue.snapshot.json").write_text(
        json.dumps({"title": "Add PR command", "assignees": ["octo"]}),
        encoding="utf-8",
    )
    (run_dir / "implementation_plan.json").write_text(
        json.dumps(_plan()),
        encoding="utf-8",
    )
    (run_dir / "verification_report.json").write_text(
        json.dumps({"status": "passed", "commands": [], "skip_reason": None}),
        encoding="utf-8",
    )


def _assert_pr_failed(settings: Settings, run_id: str, error: str) -> None:
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'pr'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("pr_failed", "pr")
    assert stage[0] == "failed"
    assert error in stage[1]


class FakeAssignmentClient:
    def __init__(self, result: list[str] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def fetch_admin_collaborators(self, owner: str, repo: str) -> list[str]:
        self.calls.append((owner, repo))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
