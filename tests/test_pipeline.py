import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_implement_run,
    complete_plan_run,
    complete_pr_run,
    complete_scout_run,
    complete_snapshot_run,
    complete_verify_run,
    create_snapshot_run,
    fail_plan_run,
    start_implement_run,
    start_plan_run,
    start_pr_run,
    start_scout_run,
    start_verify_run,
)
from pawchestrator.pipeline import run_pipeline


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


def _patch_successful_stages(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
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

    async def fake_implement(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("implement")
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

    async def fake_pr(run_id: str, settings: Settings):
        calls.append("pr")
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
