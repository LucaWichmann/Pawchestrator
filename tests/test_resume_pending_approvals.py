import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pawchestrator.approval_gate import clear_approval_event, has_approval_event
from pawchestrator.config import Settings
from pawchestrator.db import create_pipeline_run, get_run_warnings
from pawchestrator.lifecycle import PLAN_STALE_AFTER_RESTART, resume_pending_approvals


class FakeGitHubClient:
    def __init__(self, updated_at: str | None = None, error: Exception | None = None):
        self.updated_at = updated_at or "2026-05-23T00:00:00Z"
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    async def fetch_issue_updated_at(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> str:
        self.calls.append((owner, repo, issue_number))
        if self.error is not None:
            raise self.error
        return self.updated_at


def test_resume_pending_approvals_registers_event_without_warning(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _create_awaiting_approval_run(settings, tmp_path, plan_mtime=1_780_000_000)
    client = FakeGitHubClient("2026-05-23T00:00:00Z")

    asyncio.run(resume_pending_approvals(settings, client))

    assert has_approval_event("run-123") is True
    assert client.calls == [("owner", "repo", 42)]
    assert asyncio.run(get_run_warnings(settings, "run-123")) == []
    clear_approval_event("run-123")


def test_resume_pending_approvals_warns_when_issue_updated_after_plan(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    plan_mtime = datetime(2026, 5, 23, tzinfo=UTC).timestamp()
    _create_awaiting_approval_run(settings, tmp_path, plan_mtime=plan_mtime)
    client = FakeGitHubClient("2026-05-24T00:00:00Z")

    asyncio.run(resume_pending_approvals(settings, client))

    warnings = asyncio.run(get_run_warnings(settings, "run-123"))
    assert has_approval_event("run-123") is True
    assert len(warnings) == 1
    assert warnings[0]["stage_name"] == "plan"
    assert warnings[0]["code"] == PLAN_STALE_AFTER_RESTART
    clear_approval_event("run-123")


def test_resume_pending_approvals_registers_event_when_github_fails(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _create_awaiting_approval_run(settings, tmp_path, plan_mtime=1_780_000_000)
    client = FakeGitHubClient(error=RuntimeError("github failed"))

    asyncio.run(resume_pending_approvals(settings, client))

    assert has_approval_event("run-123") is True
    assert asyncio.run(get_run_warnings(settings, "run-123")) == []
    clear_approval_event("run-123")


def _create_awaiting_approval_run(
    settings: Settings,
    tmp_path: Path,
    *,
    plan_mtime: float,
) -> None:
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    plan_path = tmp_path / "runs" / "run-123" / "implementation_plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("{}", encoding="utf-8")
    os.utime(plan_path, (plan_mtime, plan_mtime))
    _set_run_state(tmp_path, status="awaiting_plan_approval", current_stage="plan")


def _set_run_state(tmp_path: Path, *, status: str, current_stage: str) -> None:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.execute(
            """
            UPDATE workflow_runs
            SET status = ?, current_stage = ?
            WHERE id = 'run-123'
            """,
            (status, current_stage),
        )
        db.commit()
