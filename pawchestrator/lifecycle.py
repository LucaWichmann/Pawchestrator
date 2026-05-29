"""Lifecycle transitions for workflow stages."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import aiosqlite

from pawchestrator.run_lifecycle import PIPELINE_STAGES, REPAIR_STAGES, REVIEW_STAGES

if TYPE_CHECKING:
    from pawchestrator.config import Settings


TERMINAL_RUN_STATUSES = (
    "completed",
    "failed",
    "grill_complete",
    "grill_failed",
    "epic_complete",
    "epic_failed",
    "post_complete",
    "post_failed",
    "issues_complete",
    "issues_failed",
    "issues_skipped",
    "review_failed",
    "repair_complete",
    "repair_failed",
    "push_complete",
    "push_failed",
)
STALE_RUN_ERROR = "Run aborted: Pawchestrator stopped before this run finished."
PLAN_APPROVAL_RESTART_ERROR = "daemon restarted during plan approval"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def fail_stale_runs_on_startup(settings: Settings) -> int:
    from pawchestrator.approval_gate import has_approval_event
    from pawchestrator.db import init_db

    await init_db(settings)
    now = _utc_now_iso()
    cleaned = 0
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        terminal_placeholders = ", ".join("?" for _ in TERMINAL_RUN_STATUSES)
        cursor = await db.execute(
            f"""
            SELECT id, workflow_type, status, current_stage, pr_url
            FROM workflow_runs
            WHERE status NOT IN ({terminal_placeholders})
              AND (group_id IS NULL OR workflow_type = 'epic')
            ORDER BY created_at, id
            """,
            TERMINAL_RUN_STATUSES,
        )
        runs = await cursor.fetchall()

        for run in runs:
            run_id = str(run["id"])
            if run["status"] == "grill_waiting":
                continue
            if run["status"] == "awaiting_plan_approval" and has_approval_event(run_id):
                continue

            if run["status"] == "pr_complete" and run["pr_url"]:
                await db.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'completed',
                        current_stage = 'pr',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, run_id),
                )
                cleaned += 1
                continue

            if run["workflow_type"] == "epic":
                await db.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'epic_failed',
                        current_stage = 'epic',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, run_id),
                )
                cleaned += 1
                continue

            stage_name = await _stale_failure_stage(db, run)
            error = (
                PLAN_APPROVAL_RESTART_ERROR
                if run["status"] == "awaiting_plan_approval"
                else STALE_RUN_ERROR
            )
            await db.execute(
                """
                UPDATE workflow_runs
                SET status = 'failed',
                    current_stage = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (stage_name, now, run_id),
            )
            await _fail_stale_stage(
                db,
                run_id=run_id,
                stage_name=stage_name,
                error=error,
                now=now,
            )
            cleaned += 1

        await db.commit()
    return cleaned


async def complete_stage(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_id: str,
    stage_name: str,
    run_status: str,
    now: str,
    artifact_type: str | None = None,
    artifact_path: Path | None = None,
    workflow_type: str | None = None,
    pr_url: str | None = None,
) -> None:
    """Mark a stage complete and optionally insert its artifact row."""

    await _update_run(
        db,
        run_id=run_id,
        stage_name=stage_name,
        run_status=run_status,
        now=now,
        workflow_type=workflow_type,
        pr_url=pr_url,
    )
    await db.execute(
        """
        UPDATE workflow_stages
        SET status = 'complete', completed_at = ?
        WHERE id = ?
        """,
        (now, stage_id),
    )
    await _insert_artifact(
        db,
        run_id=run_id,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
        now=now,
    )


async def fail_stage(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_id: str,
    stage_name: str,
    run_status: str,
    error: str | None,
    now: str,
    artifact_type: str | None = None,
    artifact_path: Path | None = None,
    workflow_type: str | None = None,
) -> None:
    """Mark a stage failed and optionally insert its artifact row."""

    await _update_run(
        db,
        run_id=run_id,
        stage_name=stage_name,
        run_status=run_status,
        now=now,
        workflow_type=workflow_type,
    )
    await db.execute(
        """
        UPDATE workflow_stages
        SET status = 'failed', error = ?, completed_at = ?
        WHERE id = ?
        """,
        (error, now, stage_id),
    )
    await _insert_artifact(
        db,
        run_id=run_id,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
        now=now,
    )


async def skip_stage(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_name: str,
    reason: str,
    now: str,
    stage_id: str | None = None,
    run_status: str | None = None,
    artifact_type: str | None = None,
    artifact_path: Path | None = None,
    workflow_type: str | None = None,
    pending_only: bool = False,
) -> None:
    """Mark a stage skipped and optionally insert its artifact row."""

    if run_status is not None:
        await _update_run(
            db,
            run_id=run_id,
            stage_name=stage_name,
            run_status=run_status,
            now=now,
            workflow_type=workflow_type,
        )
    if stage_id is None:
        status_filter = " AND status = 'pending'" if pending_only else ""
        await db.execute(
            f"""
            UPDATE workflow_stages
            SET status = 'skipped', error = ?, completed_at = ?
            WHERE run_id = ? AND stage_name = ?{status_filter}
            """,
            (reason, now, run_id, stage_name),
        )
    else:
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'skipped', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (reason, now, stage_id),
        )
    await _insert_artifact(
        db,
        run_id=run_id,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
        now=now,
    )


async def _update_run(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_name: str,
    run_status: str,
    now: str,
    workflow_type: str | None = None,
    pr_url: str | None = None,
) -> None:
    assignments = ["status = ?", "current_stage = ?"]
    values: list[object] = [run_status, stage_name]
    if workflow_type is not None:
        assignments.insert(0, "workflow_type = ?")
        values.insert(0, workflow_type)
    if pr_url is not None:
        assignments.append("pr_url = ?")
        values.append(pr_url)
    assignments.append("updated_at = ?")
    values.extend([now, run_id])
    await db.execute(
        f"""
        UPDATE workflow_runs
        SET {", ".join(assignments)}
        WHERE id = ?
        """,
        tuple(values),
    )


async def _insert_artifact(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    artifact_type: str | None,
    artifact_path: Path | None,
    now: str,
) -> None:
    if artifact_type is None or artifact_path is None:
        return
    await db.execute(
        """
        INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(uuid4()), run_id, artifact_type, str(artifact_path), now),
    )


async def _stale_failure_stage(
    db: aiosqlite.Connection,
    run: aiosqlite.Row,
) -> str:
    workflow_type = str(run["workflow_type"] or "pipeline")
    if workflow_type == "grill":
        return "grill"
    if workflow_type == "epic_architect":
        return "epic_architect"
    if workflow_type == "repair":
        return "repair"

    status = str(run["status"] or "")
    current_stage = _valid_pipeline_stage(run["current_stage"])
    status_stage = _valid_pipeline_stage(status.rsplit("_", 1)[0])
    if status.endswith("_running") or status.endswith("_failed"):
        return status_stage or current_stage or "snapshot"

    if status == "pending":
        return await _first_pending_pipeline_stage(db, str(run["id"])) or "snapshot"

    if status.endswith("_complete") or status == "verify_skipped":
        pending_stage = await _first_pending_pipeline_stage(db, str(run["id"]))
        if pending_stage is not None:
            return pending_stage
        return _next_pipeline_stage(current_stage) or current_stage or "snapshot"

    return current_stage or status_stage or "snapshot"


def _valid_pipeline_stage(stage_name: object) -> str | None:
    stage = str(stage_name or "")
    return (
        stage
        if stage in PIPELINE_STAGES or stage in REVIEW_STAGES or stage in REPAIR_STAGES
        else None
    )


def _next_pipeline_stage(stage_name: str | None) -> str | None:
    if stage_name not in PIPELINE_STAGES:
        return None
    index = PIPELINE_STAGES.index(stage_name)
    if index + 1 >= len(PIPELINE_STAGES):
        return stage_name
    return PIPELINE_STAGES[index + 1]


async def _first_pending_pipeline_stage(
    db: aiosqlite.Connection,
    run_id: str,
) -> str | None:
    cursor = await db.execute(
        """
        SELECT stage_name
        FROM workflow_stages
        WHERE run_id = ?
          AND status = 'pending'
          AND stage_name IN (?, ?, ?, ?, ?, ?)
        ORDER BY
          CASE stage_name
            WHEN 'snapshot' THEN 1
            WHEN 'scout' THEN 2
            WHEN 'plan' THEN 3
            WHEN 'implement' THEN 4
            WHEN 'verify' THEN 5
            WHEN 'pr' THEN 6
            ELSE 99
          END,
          id
        LIMIT 1
        """,
        (run_id, *PIPELINE_STAGES),
    )
    row = await cursor.fetchone()
    return str(row["stage_name"]) if row is not None else None


async def _fail_stale_stage(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_name: str,
    error: str = STALE_RUN_ERROR,
    now: str,
) -> None:
    cursor = await db.execute(
        """
        SELECT id
        FROM workflow_stages
        WHERE run_id = ? AND stage_name = ?
        ORDER BY
          CASE status
            WHEN 'running' THEN 1
            WHEN 'pending' THEN 2
            ELSE 3
          END,
          started_at DESC,
          id DESC
        LIMIT 1
        """,
        (run_id, stage_name),
    )
    row = await cursor.fetchone()
    if row is None:
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, error, started_at, completed_at
            )
            VALUES (?, ?, ?, 'failed', ?, ?, ?)
            """,
            (str(uuid4()), run_id, stage_name, error, now, now),
        )
        return

    await db.execute(
        """
        UPDATE workflow_stages
        SET status = 'failed',
            error = ?,
            started_at = COALESCE(started_at, ?),
            completed_at = ?
        WHERE id = ?
        """,
        (error, now, now, str(row["id"])),
    )
