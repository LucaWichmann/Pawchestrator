import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pawchestrator.config import Settings
from pawchestrator.db import create_grill_run, create_pipeline_run
from pawchestrator.runners import RunnerFailedError
from pawchestrator.stage_lifecycle import (
    GENERIC_STAGE_ERROR,
    STAGE_CONFIGS,
    StageLifecycleConfig,
    StageResult,
    run_stage_lifecycle,
)


def test_stage_configs_cover_known_stages() -> None:
    assert set(STAGE_CONFIGS) == {
        "scout",
        "plan",
        "implement",
        "verify",
        "snapshot",
        "pr",
        "grill",
        "epic_scout",
        "epic_architect",
        "review",
        "post",
        "issues",
        "repair",
        "push",
    }
    assert all(
        isinstance(config, StageLifecycleConfig) for config in STAGE_CONFIGS.values()
    )


def test_run_stage_lifecycle_success_path_writes_artifact(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "stage-success"
    artifact_path = tmp_path / "artifacts" / "scout.json"
    report = {"status": "ok", "items": [2, 1]}

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        assert log_path == tmp_path / "runs" / run_id / "stdout" / "scout.log"
        assert not artifact_path.exists()
        return report, artifact_path

    asyncio.run(_create_pipeline(settings, run_id))
    result = asyncio.run(run_stage_lifecycle(settings, run_id, "scout", body))

    assert result == StageResult(
        run_id=run_id,
        artifact_path=artifact_path,
        log_path=tmp_path / "runs" / run_id / "stdout" / "scout.log",
        report=report,
    )
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == report
    assert artifact_path.read_text(encoding="utf-8") == (
        '{\n  "items": [\n    2,\n    1\n  ],\n  "status": "ok"\n}\n'
    )
    assert _run_status(tmp_path, run_id) == ("scout_complete", "scout")
    assert _stage_by_name(tmp_path, run_id, "scout") == ("complete", None)
    assert _artifacts(tmp_path, run_id) == [("scout_report", str(artifact_path))]


def test_run_stage_lifecycle_grill_uses_grill_statuses(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "grill-success"
    artifact_path = tmp_path / "artifacts" / "grill.json"
    report = {"schema": "pawchestrator.grill_report.v1", "status": "success"}

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        assert log_path == tmp_path / "runs" / run_id / "stdout" / "grill.log"
        return report, artifact_path

    asyncio.run(_create_grill(settings, run_id))
    result = asyncio.run(run_stage_lifecycle(settings, run_id, "grill", body))

    assert result.report == report
    assert _run_status_with_type(tmp_path, run_id) == (
        "grill",
        "grill_complete",
        "grill",
    )
    assert _stage_by_name(tmp_path, run_id, "grill") == ("complete", None)
    assert _artifacts(tmp_path, run_id) == [("grill_report", str(artifact_path))]


def test_run_stage_lifecycle_runner_failed_error_path(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "stage-runner-failed"
    public_message = "Runner exited before producing a report."

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("runner stderr\n", encoding="utf-8")
        raise RunnerFailedError(
            public_message=public_message,
            exit_code=1,
            stderr="stderr",
            stdout="stdout",
        )

    asyncio.run(_create_pipeline(settings, run_id))
    with pytest.raises(RunnerFailedError):
        asyncio.run(run_stage_lifecycle(settings, run_id, "plan", body))

    log_path = tmp_path / "runs" / run_id / "stdout" / "plan.log"
    assert log_path.read_text(encoding="utf-8") == "runner stderr\n"
    assert _run_status(tmp_path, run_id) == ("plan_failed", "plan")
    assert _stage_by_name(tmp_path, run_id, "plan") == ("failed", public_message)
    assert _artifacts(tmp_path, run_id) == []


def test_run_stage_lifecycle_generic_exception_path_writes_minimal_log(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "stage-generic-failed"

    async def body(_log_path: Path) -> tuple[dict[str, Any], Path]:
        raise RuntimeError("internal details")

    asyncio.run(_create_pipeline(settings, run_id))
    with pytest.raises(RuntimeError, match="internal details"):
        asyncio.run(run_stage_lifecycle(settings, run_id, "implement", body))

    log_path = tmp_path / "runs" / run_id / "stdout" / "implement.log"
    assert log_path.read_text(encoding="utf-8") == f"{GENERIC_STAGE_ERROR}\n"
    assert _run_status(tmp_path, run_id) == ("implement_failed", "implement")
    assert _stage_by_name(tmp_path, run_id, "implement") == (
        "failed",
        GENERIC_STAGE_ERROR,
    )
    assert _artifacts(tmp_path, run_id) == []


async def _create_pipeline(settings: Settings, run_id: str) -> None:
    await create_pipeline_run(
        settings,
        run_id=run_id,
        owner="owner",
        repo="repo",
        issue_number=42,
    )


async def _create_grill(settings: Settings, run_id: str) -> None:
    await create_grill_run(
        settings,
        run_id=run_id,
        owner="owner",
        repo="repo",
        issue_number=42,
    )


def _run_status(tmp_path: Path, run_id: str) -> tuple[str, str | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT status, current_stage
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return row


def _run_status_with_type(tmp_path: Path, run_id: str) -> tuple[str, str, str | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT workflow_type, status, current_stage
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return row


def _stage_by_name(
    tmp_path: Path,
    run_id: str,
    stage_name: str,
) -> tuple[str, str | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = ?
            """,
            (run_id, stage_name),
        ).fetchone()
    return row


def _artifacts(tmp_path: Path, run_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        rows = db.execute(
            """
            SELECT artifact_type, file_path
            FROM artifacts
            WHERE run_id = ?
            ORDER BY artifact_type, file_path
            """,
            (run_id,),
        ).fetchall()
    return rows
