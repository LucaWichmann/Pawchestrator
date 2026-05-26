import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawchestrator.config import PipelineSettings, Settings
from pawchestrator.db import (
    complete_implement_run,
    complete_plan_run,
    complete_pr_run,
    complete_scout_run,
    complete_snapshot_run,
    complete_verify_run,
    create_snapshot_run,
    fail_plan_run,
    get_run_warnings,
    start_implement_run,
    start_plan_run,
    start_pr_run,
    start_scout_run,
    start_verify_run,
    upsert_worktree_record,
)
from pawchestrator.github import PAWCHESTRATOR_LABELS
from pawchestrator.pipeline import VerificationFailedError, run_pipeline


@pytest.fixture(autouse=True)
def _no_network_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.pipeline.get_gh_token", lambda: "test-token")
    monkeypatch.setattr("pawchestrator.pipeline.GitHubIssueClient", _NoNetworkGitHubClient)


def test_run_pipeline_runs_all_stages_and_marks_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert "[scout] done - readiness: ready" in progress
    assert "[verify] done - status: passed" in progress
    assert "[pr] done - https://github.com/owner/repo/pull/99" in progress
    assert progress[-1] == "https://github.com/owner/repo/pull/99"

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY started_at
            """,
            (result.run_id,),
        ).fetchall()

    assert run == ("completed", "pr", "https://github.com/owner/repo/pull/99")
    assert stages == [
        ("snapshot", "complete"),
        ("scout", "complete"),
        ("plan", "complete"),
        ("implement", "complete"),
        ("verify", "complete"),
        ("pr", "complete"),
    ]


def test_run_pipeline_skips_verify_for_non_code_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_implement_with_worktree(monkeypatch, calls, tmp_path / "worktree")
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: True,
    )
    monkeypatch.setattr(
        "pawchestrator.pipeline._changed_files",
        lambda worktree_path, base_branch: ["docs/usage.md"],
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert "[verify] skipped - no code files changed" in progress
    artifact_path = settings.app_dir / "runs" / result.run_id / "verification_report.json"
    report = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert report == {
        "schema": "pawchestrator.verification_report.v1",
        "status": "skipped",
        "skip_reason": (
            "Verification skipped - only non-code files changed: docs/usage.md"
        ),
        "commands": [],
    }
    with sqlite3.connect(settings.database_path) as db:
        verify_stage = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (result.run_id,),
        ).fetchone()

    assert verify_stage == (
        "skipped",
        "Verification skipped - only non-code files changed: docs/usage.md",
    )


def test_run_pipeline_verify_non_code_changes_forces_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_non_code_changes=True),
    )
    calls: list[str] = []
    diff_checks: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: diff_checks.append("checked") or True,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert diff_checks == []


def test_run_pipeline_git_diff_error_falls_back_to_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_implement_with_worktree(monkeypatch, calls, tmp_path / "worktree")
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: False,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]


def test_run_pipeline_stops_on_failure_and_marks_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    async def fake_plan(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("plan")
        stage_id = await start_plan_run(settings, run_id=run_id)
        await fail_plan_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error="plan exploded",
        )
        raise RuntimeError("plan exploded")

    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)

    with pytest.raises(RuntimeError, match="plan exploded"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=progress.append,
            )
        )

    assert calls == ["snapshot", "scout", "plan"]
    assert "[plan] FAILED: plan exploded" in progress
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT id, status, current_stage FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY stage_name
            """,
            (run[0],),
        ).fetchall()

    assert run[1:] == ("failed", "plan")
    assert ("plan", "failed", "plan exploded") in stages
    assert ("implement", "pending", None) in stages


def test_run_pipeline_passes_allow_empty_commit_to_pr_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    allow_empty_commit_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        allow_empty_commit_values=allow_empty_commit_values,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            allow_empty_commit=True,
        )
    )

    assert allow_empty_commit_values == [True]


def test_run_pipeline_passes_allow_dirty_to_initial_implement_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    allow_dirty_existing_worktree_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        allow_dirty_existing_worktree_values=allow_dirty_existing_worktree_values,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            allow_dirty_existing_worktree=True,
        )
    )

    assert allow_dirty_existing_worktree_values == [True]


def test_run_pipeline_blocks_pr_when_verify_fails_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_repair_attempts=0),
    )
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed"])

    with pytest.raises(VerificationFailedError, match="verification failed"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=progress.append,
            )
        )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify"]
    assert "[verify] done - status: failed" in progress
    assert not any(call == "pr" for call in calls)
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            ORDER BY stage_name, started_at
            """
        ).fetchall()

    assert run == ("failed", "verify", None)
    assert ("verify", "failed", "test exited 1: assertion failed") in stages
    assert ("pr", "pending", None) in stages


def test_run_pipeline_repairs_failed_verify_then_creates_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    repair_contexts: list[tuple[dict[str, object] | None, int | None]] = []
    allow_dirty_existing_worktree_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        repair_contexts=repair_contexts,
        allow_dirty_existing_worktree_values=allow_dirty_existing_worktree_values,
    )
    _patch_verify_results(monkeypatch, calls, ["failed", "passed"])

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "verify",
        "implement",
        "verify",
        "pr",
    ]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert allow_dirty_existing_worktree_values == [False, True]
    assert repair_contexts == [
        (None, None),
        (
            {
                "status": "failed",
                "commands": [
                    {
                        "command": "pytest",
                        "exit_code": 1,
                        "stdout_summary": "",
                        "stderr_summary": "assertion failed",
                    }
                ],
                "skip_reason": None,
                "verify_log_tail": (
                    "[command] test: pytest\n"
                    "[exit_code] 1\n"
                    "[stdout]\n\n"
                    "[stderr]\n"
                    "assertion failed\n"
                ),
            },
            1,
        ),
    ]
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE stage_name IN ('implement', 'verify', 'pr')
            ORDER BY started_at
            """
        ).fetchall()

    assert run == ("completed", "pr", "https://github.com/owner/repo/pull/99")
    assert stages == [
        ("implement", "complete"),
        ("verify", "failed"),
        ("implement", "complete"),
        ("verify", "complete"),
        ("pr", "complete"),
    ]


def test_run_pipeline_reconciles_checkbox_marks_after_implement_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed", "passed"])

    async def fake_reconcile(
        settings: Settings,
        run_id: str,
        client: object,
    ) -> tuple[bool, list[dict[str, object]]]:
        calls.append("reconcile")
        return False, []

    monkeypatch.setattr(
        "pawchestrator.pipeline.reconcile_checkbox_marks",
        fake_reconcile,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "reconcile",
        "verify",
        "implement",
        "reconcile",
        "verify",
        "reconcile",
        "pr",
    ]


def test_run_pipeline_records_checkbox_reconciliation_warning_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    async def fake_reconcile(
        settings: Settings,
        run_id: str,
        client: object,
    ) -> tuple[bool, list[dict[str, object]]]:
        raise RuntimeError("lost patch state")

    monkeypatch.setattr(
        "pawchestrator.pipeline.reconcile_checkbox_marks",
        fake_reconcile,
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )
    warnings = asyncio.run(get_run_warnings(settings, result.run_id))

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert [warning["code"] for warning in warnings] == [
        "checkbox_reconciliation_failed",
        "checkbox_reconciliation_failed",
    ]
    assert warnings[0]["stage_name"] == "implement"
    assert warnings[1]["stage_name"] == "verify"
    assert "lost patch state" in warnings[0]["message"]


def test_run_pipeline_exhausts_verify_repairs_and_leaves_pr_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_repair_attempts=2),
    )
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed", "failed", "failed"])

    with pytest.raises(VerificationFailedError):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "verify",
        "implement",
        "verify",
        "implement",
        "verify",
    ]
    with sqlite3.connect(settings.database_path) as db:
        pr_stage = db.execute(
            """
            SELECT status
            FROM workflow_stages
            WHERE stage_name = 'pr'
            """
        ).fetchone()

    assert pr_stage == ("pending",)


def test_run_pipeline_updates_stage_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    client = _RecordingLabelClient()
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert client.ensure_calls == [
        ("owner", "repo", name, color)
        for name, color in PAWCHESTRATOR_LABELS.values()
    ]
    assert client.added_labels == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:implementing",
        "pawchestrator:verifying",
        "pawchestrator:pr-ready",
    ]
    assert "pawchestrator:failed" not in client.added_labels


def test_run_pipeline_sets_failed_label_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    client = _RecordingLabelClient()
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    async def fake_plan(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("plan")
        stage_id = await start_plan_run(settings, run_id=run_id)
        await fail_plan_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error="plan exploded",
        )
        raise RuntimeError("plan exploded")

    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)

    with pytest.raises(RuntimeError, match="plan exploded"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )

    assert client.added_labels == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:failed",
    ]


def test_run_pipeline_label_errors_do_not_abort_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    calls: list[str] = []
    client = _RecordingLabelClient(fail_add=True)
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert client.add_attempts == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:implementing",
        "pawchestrator:verifying",
        "pawchestrator:pr-ready",
    ]


class _NoNetworkGitHubClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.comment_id = 123

    async def post_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> int:
        return self.comment_id

    async def edit_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        body: str,
    ) -> None:
        return None

    async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
        return None

    async def add_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        return None

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        return None

    async def fetch_issue_body(self, reference: object) -> str:
        return ""

    async def patch_issue_body(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> None:
        return None


class _RecordingLabelClient:
    def __init__(self, *, fail_add: bool = False) -> None:
        self.fail_add = fail_add
        self.ensure_calls: list[tuple[str, str, str, str]] = []
        self.added_labels: list[str] = []
        self.add_attempts: list[str] = []
        self.removed_labels: list[str] = []

    async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
        self.ensure_calls.append((owner, repo, name, color))

    async def add_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.add_attempts.append(name)
        if self.fail_add:
            raise RuntimeError("label add failed")
        self.added_labels.append(name)

    async def remove_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.removed_labels.append(name)


def _fake_post_initial_run_comment(client: _RecordingLabelClient):
    async def fake_post_initial_run_comment(settings: Settings, run_id: str):
        return client

    return fake_post_initial_run_comment


def _patch_successful_stages(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    allow_empty_commit_values: list[bool] | None = None,
    repair_contexts: list[tuple[dict[str, object] | None, int | None]] | None = None,
    allow_dirty_existing_worktree_values: list[bool] | None = None,
) -> None:
    async def fake_snapshot(issue_url: str, settings: Settings, *, run_id: str):
        calls.append("snapshot")
        stage_id = await create_snapshot_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=42,
        )
        artifact_path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_snapshot_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
        return SimpleNamespace(run_id=run_id, artifact_path=artifact_path, issue_number=42, title="Issue")

    async def fake_scout(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("scout")
        stage_id = await start_scout_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "scout_report.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_scout_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
        return SimpleNamespace(report={"readiness": "ready"})

    async def fake_plan(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("plan")
        stage_id = await start_plan_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_plan.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_plan_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
        return SimpleNamespace(plan={})

    async def fake_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        repair_context: dict[str, object] | None = None,
        repair_attempt: int | None = None,
        allow_dirty_existing_worktree: bool = False,
    ):
        calls.append("implement")
        if repair_contexts is not None:
            repair_contexts.append((repair_context, repair_attempt))
        if allow_dirty_existing_worktree_values is not None:
            allow_dirty_existing_worktree_values.append(allow_dirty_existing_worktree)
        stage_id = await start_implement_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_report.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_implement_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
        return SimpleNamespace(report={})

    async def fake_verify(run_id: str, settings: Settings):
        calls.append("verify")
        stage_id = await start_verify_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "verification_report.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_verify_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
            passed=True,
        )
        return SimpleNamespace(report={"status": "passed"})

    async def fake_pr(
        run_id: str,
        settings: Settings,
        *,
        allow_empty_commit: bool = False,
    ):
        calls.append("pr")
        if allow_empty_commit_values is not None:
            allow_empty_commit_values.append(allow_empty_commit)
        stage_id = await start_pr_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "pr_draft.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_pr_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
            pr_url="https://github.com/owner/repo/pull/99",
        )
        return SimpleNamespace(pr_url="https://github.com/owner/repo/pull/99")

    monkeypatch.setattr("pawchestrator.pipeline.snapshot_issue", fake_snapshot)
    monkeypatch.setattr("pawchestrator.pipeline.run_scout", fake_scout)
    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)
    monkeypatch.setattr("pawchestrator.pipeline.run_implement", fake_implement)
    monkeypatch.setattr("pawchestrator.pipeline.run_verify", fake_verify)
    monkeypatch.setattr("pawchestrator.pipeline.run_pr", fake_pr)


def _patch_implement_with_worktree(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    worktree_path: Path,
) -> None:
    async def fake_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        repair_context: dict[str, object] | None = None,
        repair_attempt: int | None = None,
        allow_dirty_existing_worktree: bool = False,
    ):
        calls.append("implement")
        worktree_path.mkdir(parents=True, exist_ok=True)
        await upsert_worktree_record(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=42,
            branch="issue-42",
            path=worktree_path,
        )
        stage_id = await start_implement_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_report.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        await complete_implement_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
        return SimpleNamespace(report={})

    monkeypatch.setattr("pawchestrator.pipeline.run_implement", fake_implement)


def _patch_verify_results(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    statuses: list[str],
) -> None:
    remaining = list(statuses)

    async def fake_verify(run_id: str, settings: Settings):
        calls.append("verify")
        if not remaining:
            raise AssertionError("unexpected verify call")
        status = remaining.pop(0)
        passed = status == "passed"
        stage_id = await start_verify_run(settings, run_id=run_id)
        artifact_path = settings.app_dir / "runs" / run_id / "verification_report.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema": "pawchestrator.verification_report.v1",
            "status": status,
            "commands": [
                {
                    "command": "pytest",
                    "exit_code": 0 if passed else 1,
                    "stdout_summary": "",
                    "stderr_summary": "" if passed else "assertion failed",
                }
            ],
            "skip_reason": None,
        }
        artifact_path.write_text("{}", encoding="utf-8")
        log_path = settings.app_dir / "runs" / run_id / "stdout" / "verify.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            (
                "[command] test: pytest\n"
                f"[exit_code] {0 if passed else 1}\n"
                "[stdout]\n\n"
                "[stderr]\n"
                f"{'' if passed else 'assertion failed'}\n"
            ),
            encoding="utf-8",
        )
        await complete_verify_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
            passed=passed,
            error=None if passed else "test exited 1: assertion failed",
        )
        return SimpleNamespace(
            run_id=run_id,
            artifact_path=artifact_path,
            log_path=log_path,
            report=report,
        )

    monkeypatch.setattr("pawchestrator.pipeline.run_verify", fake_verify)
