"""Lifecycle transitions for workflow stages."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import aiosqlite


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
