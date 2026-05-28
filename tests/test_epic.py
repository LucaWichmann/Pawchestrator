import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawchestrator.config import PipelineSettings, Settings
from pawchestrator.db import create_epic_run, get_latest_epic_run_by_issue
from pawchestrator.epic import run_epic
from pawchestrator.github import GENERATED_BY_FOOTER
from pawchestrator.implement import WorktreeInfo


def test_run_epic_mode_runs_sub_issues_on_shared_branch_then_final_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_parent(settings))
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {
                    "number": 43,
                    "title": "First child",
                    "url": "https://github.com/owner/repo/issues/43",
                },
                {
                    "number": 44,
                    "title": "Second child",
                    "url": "https://github.com/owner/repo/issues/44",
                },
            ]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=False)
    _patch_pipeline(monkeypatch, calls)
    _patch_epic_pr(monkeypatch, pr_calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=lambda _message: None,
            group_id="group-123",
            parent_run_id="epic-parent",
        )
    )

    assert [call["issue_url"] for call in calls] == [
        "https://github.com/owner/repo/issues/43",
        "https://github.com/owner/repo/issues/44",
    ]
    assert {call["create_pr"] for call in calls} == {False}
    assert {call["worktree_branch"] for call in calls} == {"paw/epic-42-big-epic"}
    assert {call["base_branch"] for call in calls} == {"main"}
    assert {call["allow_dirty_existing_worktree"] for call in calls} == {True}
    assert {call["defer_verification"] for call in calls} == {True}
    assert result.group_id == "group-123"
    assert [sub_run.pr_url for sub_run in result.sub_runs] == ["", ""]
    assert len(pr_calls) == 1
    assert _without_body(pr_calls[0]) == {
        "branch": "paw/epic-42-big-epic",
        "base_branch": "main",
        "draft": False,
        "allow_empty_commit": False,
    }
    assert "Implements epic #42" in str(pr_calls[0]["body"])
    assert "Sub-issues completed: #43, #44" in str(pr_calls[0]["body"])
    assert "Closes #43, closes #44" in str(pr_calls[0]["body"])
    assert "- #43 - First child (run `run-43`)" in str(pr_calls[0]["body"])
    assert "- #44 - Second child (run `run-44`)" in str(pr_calls[0]["body"])
    assert "- #43: 2 files changed: pawchestrator/43.py, tests/test_43.py" in str(
        pr_calls[0]["body"]
    )
    assert "- #44: passed" in str(pr_calls[0]["body"])
    status = asyncio.run(get_latest_epic_run_by_issue(settings, "owner", "repo", 42))
    assert status is not None
    assert status["status"] == "epic_complete"
    assert status["mode"] == "epic"
    assert status["branch"] == "paw/epic-42-big-epic"
    assert status["pr_url"] == "https://github.com/owner/repo/pull/42"


def test_run_epic_with_sub_issues_creates_draft_epic_pr_then_child_prs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(epic_branch_mode="epic-with-sub-issues"),
    )
    asyncio.run(_insert_parent(settings))
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [{"number": 43, "url": "https://github.com/owner/repo/issues/43"}]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=False)
    _patch_pipeline(monkeypatch, calls)
    _patch_epic_pr(monkeypatch, pr_calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=lambda _message: None,
            group_id="group-123",
            parent_run_id="epic-parent",
        )
    )

    assert len(pr_calls) == 1
    assert _without_body(pr_calls[0]) == {
        "branch": "paw/epic-42-big-epic",
        "base_branch": "main",
        "draft": True,
        "allow_empty_commit": True,
    }
    assert "Implements epic #42" in str(pr_calls[0]["body"])
    assert "Closes #" not in str(pr_calls[0]["body"])
    assert calls == [
        {
            "issue_url": "https://github.com/owner/repo/issues/43",
            "group_id": "group-123",
            "create_pr": True,
            "worktree_branch": None,
            "base_branch": "paw/epic-42-big-epic",
            "pr_base_branch": "paw/epic-42-big-epic",
            "allow_dirty_existing_worktree": False,
            "defer_verification": False,
        }
    ]
    assert result.sub_runs[0].pr_url == "https://github.com/owner/repo/pull/43"


def test_run_epic_stops_on_first_pipeline_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_parent(settings))
    calls: list[dict[str, object]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "url": "https://github.com/owner/repo/issues/44"},
                {"number": 45, "url": "https://github.com/owner/repo/issues/45"},
            ]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=False)
    _patch_pipeline(monkeypatch, calls, failures={44})

    with pytest.raises(RuntimeError, match="pipeline failed for 44"):
        asyncio.run(
            run_epic(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=lambda _message: None,
                group_id="group-123",
                parent_run_id="epic-parent",
            )
        )

    assert [call["issue_url"] for call in calls] == [
        "https://github.com/owner/repo/issues/43",
        "https://github.com/owner/repo/issues/44",
    ]
    status = asyncio.run(get_latest_epic_run_by_issue(settings, "owner", "repo", 42))
    assert status is not None
    assert status["status"] == "epic_failed"


def test_run_epic_continues_on_failure_when_fail_fast_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(epic_fail_fast=False),
    )
    asyncio.run(_insert_parent(settings))
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    progress: list[str] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "url": "https://github.com/owner/repo/issues/44"},
                {"number": 45, "url": "https://github.com/owner/repo/issues/45"},
            ]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=False)
    _patch_pipeline(monkeypatch, calls, failures={44})
    _patch_epic_pr(monkeypatch, pr_calls)

    with pytest.raises(RuntimeError, match="final PR blocked"):
        asyncio.run(
            run_epic(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=progress.append,
                group_id="group-123",
                parent_run_id="epic-parent",
            )
        )

    assert [call["issue_url"] for call in calls] == [
        "https://github.com/owner/repo/issues/43",
        "https://github.com/owner/repo/issues/44",
        "https://github.com/owner/repo/issues/45",
    ]
    assert "[epic] sub-issue #44 failed; continuing" in progress
    assert pr_calls == []
    status = asyncio.run(get_latest_epic_run_by_issue(settings, "owner", "repo", 42))
    assert status is not None
    assert status["status"] == "epic_failed"


def test_run_epic_resume_skips_completed_sub_issue_and_reruns_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_parent(settings, status="epic_failed"))
    _insert_child_run(settings, run_id="completed-43", issue_number=43, status="completed")
    _insert_child_run(settings, run_id="failed-44", issue_number=44, status="failed")
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    progress: list[str] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "title": "Done", "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "title": "Retry", "url": "https://github.com/owner/repo/issues/44"},
                {"number": 45, "title": "Next", "url": "https://github.com/owner/repo/issues/45"},
            ]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=True)
    _patch_pipeline(monkeypatch, calls)
    _patch_epic_pr(monkeypatch, pr_calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
            group_id="group-123",
            parent_run_id="epic-parent",
        )
    )

    assert "[epic] skipping completed sub-issue #43" in progress
    assert [call["issue_url"] for call in calls] == [
        "https://github.com/owner/repo/issues/44",
        "https://github.com/owner/repo/issues/45",
    ]
    assert [sub_run.run_id for sub_run in result.sub_runs] == [
        "completed-43",
        "run-44",
        "run-45",
    ]
    assert len(pr_calls) == 1
    assert "Sub-issues completed: #43, #44, #45" in str(pr_calls[0]["body"])
    assert str(pr_calls[0]["body"]).endswith(GENERATED_BY_FOOTER)


def test_run_epic_resume_all_children_complete_creates_final_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_parent(settings, status="epic_failed"))
    _insert_child_run(settings, run_id="completed-43", issue_number=43, status="completed")
    _insert_child_run(settings, run_id="completed-44", issue_number=44, status="completed")
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [
                {"number": 43, "title": "Done", "url": "https://github.com/owner/repo/issues/43"},
                {"number": 44, "title": "Also done", "url": "https://github.com/owner/repo/issues/44"},
            ]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=True)
    _patch_pipeline(monkeypatch, calls)
    _patch_epic_pr(monkeypatch, pr_calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=lambda _message: None,
            group_id="group-123",
            parent_run_id="epic-parent",
        )
    )

    assert calls == []
    assert [sub_run.run_id for sub_run in result.sub_runs] == [
        "completed-43",
        "completed-44",
    ]
    assert len(pr_calls) == 1


def test_run_epic_with_sub_issues_resume_reuses_existing_draft_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(epic_branch_mode="epic-with-sub-issues"),
    )
    asyncio.run(
        _insert_parent(
            settings,
            status="epic_failed",
            pr_url="https://github.com/owner/repo/pull/42",
        )
    )
    calls: list[dict[str, object]] = []
    pr_calls: list[dict[str, object]] = []
    _patch_client(
        monkeypatch,
        _FakeSubIssueClient(
            [{"number": 43, "url": "https://github.com/owner/repo/issues/43"}]
        ),
    )
    _patch_epic_worktree(monkeypatch, tmp_path, expected_allow_dirty=False)
    _patch_pipeline(monkeypatch, calls)
    _patch_epic_pr(monkeypatch, pr_calls)

    result = asyncio.run(
        run_epic(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=lambda _message: None,
            group_id="group-123",
            parent_run_id="epic-parent",
        )
    )

    assert pr_calls == []
    assert calls[0]["create_pr"] is True
    assert result.sub_runs[0].pr_url == "https://github.com/owner/repo/pull/43"


class _FakeSubIssueClient:
    def __init__(self, sub_issues: list[dict[str, object]]) -> None:
        self.sub_issues = sub_issues
        self.fetched = False

    async def fetch_sub_issues(self, _reference):
        self.fetched = True
        return self.sub_issues

    async def fetch_issue_title(self, _reference):
        return "Big epic"


async def _insert_parent(
    settings: Settings,
    *,
    status: str = "pending",
    pr_url: str | None = None,
) -> None:
    await create_epic_run(
        settings,
        run_id="epic-parent",
        owner="owner",
        repo="repo",
        issue_number=42,
        group_id="group-123",
    )
    if status != "pending" or pr_url is not None:
        with sqlite3.connect(settings.database_path) as db:
            db.execute(
                """
                UPDATE workflow_runs
                SET status = ?, current_stage = 'epic', pr_url = ?
                WHERE id = 'epic-parent'
                """,
                (status, pr_url),
            )
            db.commit()


def _insert_child_run(
    settings: Settings,
    *,
    run_id: str,
    issue_number: int,
    status: str,
    pr_url: str | None = None,
) -> None:
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, group_id, workflow_type, status,
              current_stage, pr_url, created_at, updated_at
            )
            VALUES (?, 'owner', 'repo', ?, 'group-123', 'pipeline', ?, 'pr', ?,
                    ?, ?)
            """,
            (
                run_id,
                issue_number,
                status,
                pr_url,
                f"2026-05-24T10:00:{issue_number}Z",
                f"2026-05-24T10:01:{issue_number}Z",
            ),
        )
        db.commit()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeSubIssueClient) -> None:
    monkeypatch.setattr("pawchestrator.epic.get_gh_token", lambda: "token")
    monkeypatch.setattr("pawchestrator.epic.GitHubIssueClient", lambda _token: client)


def _patch_epic_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    expected_allow_dirty: bool | None = None,
) -> None:
    async def fake_ensure_issue_worktree(
        settings: Settings,
        *,
        snapshot,
        source_repo_path,
        allow_dirty_existing_worktree,
        branch_override,
        path_override,
        base_branch,
    ):
        assert branch_override == "paw/epic-42-big-epic"
        assert path_override == tmp_path / "worktrees" / "owner" / "repo" / "epic-42"
        assert base_branch == "main"
        if expected_allow_dirty is not None:
            assert allow_dirty_existing_worktree is expected_allow_dirty
        return WorktreeInfo(path=path_override, branch=branch_override, reused=False)

    monkeypatch.setattr("pawchestrator.epic.ensure_issue_worktree", fake_ensure_issue_worktree)


def _patch_epic_pr(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, object]],
) -> None:
    async def fake_create_worktree_pr(**kwargs):
        calls.append(
            {
                "branch": kwargs["branch"],
                "base_branch": kwargs["base_branch"],
                "draft": kwargs["draft"],
                "allow_empty_commit": kwargs["allow_empty_commit"],
                "body": kwargs["body"],
            }
        )
        return SimpleNamespace(pr_url="https://github.com/owner/repo/pull/42")

    monkeypatch.setattr("pawchestrator.epic.create_worktree_pr", fake_create_worktree_pr)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, object]],
    *,
    failures: set[int] | None = None,
) -> None:
    failing_numbers = failures or set()

    async def fake_run_pipeline(
        issue_url: str,
        settings: Settings,
        *,
        group_id: str | None = None,
        repo_path: Path | None = None,
        progress=print,
        create_pr: bool = True,
        worktree_branch: str | None = None,
        worktree_path: Path | None = None,
        base_branch: str = "main",
        pr_base_branch: str = "main",
        allow_dirty_existing_worktree: bool = False,
        defer_verification: bool = False,
    ):
        calls.append(
            {
                "issue_url": issue_url,
                "group_id": group_id,
                "create_pr": create_pr,
                "worktree_branch": worktree_branch,
                "base_branch": base_branch,
                "pr_base_branch": pr_base_branch,
                "allow_dirty_existing_worktree": allow_dirty_existing_worktree,
                "defer_verification": defer_verification,
            }
        )
        issue_number = int(issue_url.rsplit("/", 1)[1])
        if issue_number in failing_numbers:
            raise RuntimeError(f"pipeline failed for {issue_number}")
        _write_child_artifacts(settings, issue_number)
        return SimpleNamespace(
            run_id=f"run-{issue_number}",
            pr_url=(
                f"https://github.com/owner/repo/pull/{issue_number}"
                if create_pr
                else ""
            ),
        )

    monkeypatch.setattr("pawchestrator.epic.run_pipeline", fake_run_pipeline)


def _without_body(call: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in call.items() if key != "body"}


def _write_child_artifacts(settings: Settings, issue_number: int) -> None:
    run_dir = settings.app_dir / "runs" / f"run-{issue_number}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "implementation_report.json").write_text(
        json.dumps(
            {
                "diff_summary": (
                    f"2 files changed: pawchestrator/{issue_number}.py, "
                    f"tests/test_{issue_number}.py"
                ),
                "files_changed": [
                    f"pawchestrator/{issue_number}.py",
                    f"tests/test_{issue_number}.py",
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "verification_report.json").write_text(
        json.dumps({"status": "passed", "commands": [], "skip_reason": None}),
        encoding="utf-8",
    )
