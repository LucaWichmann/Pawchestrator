"""Shared lifecycle wrapper for stage implementations."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from pawchestrator.db import init_db, utc_now_iso
from pawchestrator.lifecycle import complete_stage, fail_stage
from pawchestrator.run_lifecycle import start_stage
from pawchestrator.runners import RunnerFailedError


GENERIC_STAGE_ERROR = "Stage failed. See local run logs."


@dataclass(frozen=True)
class StageLifecycleConfig:
    run_status_running: str
    run_status_complete: str
    run_status_failed: str
    artifact_type: str


STAGE_CONFIGS: dict[str, StageLifecycleConfig] = {
    "scout": StageLifecycleConfig(
        "scout_running",
        "scout_complete",
        "scout_failed",
        "scout_report",
    ),
    "plan": StageLifecycleConfig(
        "plan_running",
        "plan_complete",
        "plan_failed",
        "implementation_plan",
    ),
    "implement": StageLifecycleConfig(
        "implement_running",
        "implement_complete",
        "implement_failed",
        "implementation_report",
    ),
    "verify": StageLifecycleConfig(
        "verify_running",
        "verify_complete",
        "verify_failed",
        "verification_report",
    ),
    "snapshot": StageLifecycleConfig(
        "snapshot_running",
        "snapshot_complete",
        "snapshot_failed",
        "issue_snapshot",
    ),
    "pr": StageLifecycleConfig(
        "pr_running",
        "pr_complete",
        "pr_failed",
        "pr_draft",
    ),
    "grill": StageLifecycleConfig(
        "grill_running",
        "grill_complete",
        "grill_failed",
        "grill_report",
    ),
    "review": StageLifecycleConfig(
        "review_running",
        "review_complete",
        "review_failed",
        "review_report",
    ),
    "post": StageLifecycleConfig(
        "post_running",
        "post_complete",
        "post_failed",
        "review_post_report",
    ),
    "issues": StageLifecycleConfig(
        "issues_running",
        "issues_complete",
        "issues_failed",
        "created_issues_report",
    ),
    "repair": StageLifecycleConfig(
        "repair_running",
        "repair_complete",
        "repair_failed",
        "repair_report",
    ),
    "push": StageLifecycleConfig(
        "push_running",
        "push_complete",
        "push_failed",
        "repair_push_report",
    ),
}


@dataclass(frozen=True)
class StageResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    report: dict[str, Any]


StageFn = Callable[[Path], Awaitable[tuple[dict[str, Any], Path]]]


async def run_stage_lifecycle(
    settings: Any,
    run_id: str,
    stage_name: str,
    body: StageFn,
) -> StageResult:
    config = STAGE_CONFIGS[stage_name]
    log_path = settings.app_dir / "runs" / run_id / "stdout" / f"{stage_name}.log"

    await init_db(settings)
    stage_id = await start_stage(
        settings.database_path,
        run_id=run_id,
        stage_name=stage_name,
        status=config.run_status_running,
        now=utc_now_iso(),
    )

    try:
        report, artifact_path = await body(log_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        async with aiosqlite.connect(settings.database_path) as db:
            await complete_stage(
                db,
                run_id=run_id,
                stage_id=stage_id,
                stage_name=stage_name,
                run_status=config.run_status_complete,
                artifact_type=config.artifact_type,
                artifact_path=artifact_path,
                now=utc_now_iso(),
            )
            await db.commit()
        return StageResult(
            run_id=run_id,
            artifact_path=artifact_path,
            log_path=log_path,
            report=report,
        )
    except Exception as exc:
        error = (
            exc.public_message
            if isinstance(exc, RunnerFailedError)
            else GENERIC_STAGE_ERROR
        )
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"{error}\n", encoding="utf-8")
        async with aiosqlite.connect(settings.database_path) as db:
            await fail_stage(
                db,
                run_id=run_id,
                stage_id=stage_id,
                stage_name=stage_name,
                run_status=config.run_status_failed,
                error=error,
                now=utc_now_iso(),
            )
            await db.commit()
        raise
