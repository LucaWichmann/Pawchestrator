"""Shared lifecycle wrapper for stage implementations."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from pawchestrator.db import init_db, utc_now_iso
from pawchestrator.lifecycle import complete_stage, fail_stage, skip_stage
from pawchestrator.run_lifecycle import start_stage
from pawchestrator.runners import RunnerFailedError


GENERIC_STAGE_ERROR = "Stage failed. See local run logs."


@dataclass(frozen=True)
class StageLifecycleConfig:
    run_status_running: str
    run_status_complete: str
    run_status_failed: str
    artifact_type: str | None
    workflow_kind: str | None = None
    run_status_skipped: str | None = None


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
        run_status_skipped="verify_skipped",
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
        "grill",
    ),
    "epic_scout": StageLifecycleConfig(
        "epic_scout_running",
        "epic_scout_complete",
        "epic_scout_failed",
        "epic_scout_report",
        "epic_architect",
    ),
    "epic_architect": StageLifecycleConfig(
        "epic_architect_running",
        "epic_architect_complete",
        "epic_architect_failed",
        "epic_architect_plan",
        "epic_architect",
    ),
    "review": StageLifecycleConfig(
        "review_running",
        "review_complete",
        "review_failed",
        "review_report",
        "review",
    ),
    "post": StageLifecycleConfig(
        "post_running",
        "post_complete",
        "post_failed",
        None,
        "review",
    ),
    "issues": StageLifecycleConfig(
        "issues_running",
        "issues_complete",
        "issues_failed",
        "created_issues_report",
        "review",
        "issues_skipped",
    ),
    "repair": StageLifecycleConfig(
        "repair_running",
        "repair_complete",
        "repair_failed",
        "repair_report",
        "repair",
    ),
    "push": StageLifecycleConfig(
        "push_running",
        "push_complete",
        "push_failed",
        "repair_push_report",
        "repair",
    ),
}


@dataclass(frozen=True)
class StageResult:
    run_id: str
    artifact_path: Path | None
    log_path: Path
    report: dict[str, Any]

    @property
    def pr_url(self) -> str:
        return str(self.report["pr_url"])

    @property
    def branch(self) -> str:
        return str(self.report["branch"])

    @property
    def title(self) -> str:
        return str(self.report["title"])

    @property
    def draft(self) -> dict[str, Any]:
        return self.report

    @property
    def issue_number(self) -> int:
        return int(self.report["number"])

    @property
    def worktree_path(self) -> Path:
        return Path(str(self.report["worktree_path"]))


class StageSkipped(Exception):
    def __init__(self, reason: str, report: dict[str, Any], artifact_path: Path | None):
        super().__init__(reason)
        self.reason = reason
        self.report = report
        self.artifact_path = artifact_path


class StageFailedWithArtifact(RuntimeError):
    def __init__(
        self,
        message: str,
        report: dict[str, Any],
        artifact_path: Path | None,
    ):
        super().__init__(message)
        self.report = report
        self.artifact_path = artifact_path


StageFn = Callable[[Path], Awaitable[tuple[dict[str, Any], Path | None]]]


def _write_stage_artifact(report: dict[str, Any], artifact_path: Path | None) -> None:
    if artifact_path is None:
        return
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _failed_verify_error(report: dict[str, Any], log_path: Path) -> str:
    explicit = report.get("error")
    if explicit:
        return str(explicit)

    command_names = _verify_log_command_names(log_path)
    commands = report.get("commands")
    if isinstance(commands, list):
        for index, command in enumerate(commands):
            if not isinstance(command, dict) or command.get("exit_code") == 0:
                continue
            if index < len(command_names):
                command_name = command_names[index]
            else:
                command_name = command.get("command")
            detail = str(
                command.get("stderr_summary") or command.get("stdout_summary") or ""
            )
            message = f"{command_name} exited {command.get('exit_code')}"
            if detail:
                return f"{message}: {detail}"
            return message
    return GENERIC_STAGE_ERROR


def _verify_log_command_names(log_path: Path) -> list[str]:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    names: list[str] = []
    for line in lines:
        if not line.startswith("[command] ") or ": " not in line:
            continue
        names.append(line.removeprefix("[command] ").split(": ", 1)[0])
    return names


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
        workflow_kind=config.workflow_kind,
        now=utc_now_iso(),
    )

    try:
        report, artifact_path = await body(log_path)
        _write_stage_artifact(report, artifact_path)
        if stage_name == "verify" and report.get("status") == "failed":
            error = _failed_verify_error(report, log_path)
            async with aiosqlite.connect(settings.database_path) as db:
                await fail_stage(
                    db,
                    run_id=run_id,
                    stage_id=stage_id,
                    stage_name=stage_name,
                    run_status=config.run_status_failed,
                    error=error,
                    artifact_type=config.artifact_type,
                    artifact_path=artifact_path,
                    workflow_type=config.workflow_kind,
                    now=utc_now_iso(),
                )
                await db.commit()
            return StageResult(
                run_id=run_id,
                artifact_path=artifact_path,
                log_path=log_path,
                report=report,
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
                workflow_type=config.workflow_kind,
                now=utc_now_iso(),
            )
            await db.commit()
        return StageResult(
            run_id=run_id,
            artifact_path=artifact_path,
            log_path=log_path,
            report=report,
        )
    except StageSkipped as skipped:
        _write_stage_artifact(skipped.report, skipped.artifact_path)
        async with aiosqlite.connect(settings.database_path) as db:
            await skip_stage(
                db,
                run_id=run_id,
                stage_id=stage_id,
                stage_name=stage_name,
                run_status=config.run_status_skipped,
                reason=skipped.reason,
                artifact_type=config.artifact_type,
                artifact_path=skipped.artifact_path,
                workflow_type=config.workflow_kind,
                now=utc_now_iso(),
            )
            await db.commit()
        return StageResult(
            run_id=run_id,
            artifact_path=skipped.artifact_path,
            log_path=log_path,
            report=skipped.report,
        )
    except StageFailedWithArtifact as exc:
        _write_stage_artifact(exc.report, exc.artifact_path)
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"{GENERIC_STAGE_ERROR}\n", encoding="utf-8")
        async with aiosqlite.connect(settings.database_path) as db:
            await fail_stage(
                db,
                run_id=run_id,
                stage_id=stage_id,
                stage_name=stage_name,
                run_status=config.run_status_failed,
                error=GENERIC_STAGE_ERROR,
                artifact_type=config.artifact_type,
                artifact_path=exc.artifact_path,
                workflow_type=config.workflow_kind,
                now=utc_now_iso(),
            )
            await db.commit()
        raise
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
                workflow_type=config.workflow_kind,
                now=utc_now_iso(),
            )
            await db.commit()
        raise
