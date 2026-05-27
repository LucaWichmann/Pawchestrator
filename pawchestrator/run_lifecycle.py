"""Workflow run lifecycle metadata and transitions."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from uuid import uuid4

import aiosqlite


class WorkflowKind(StrEnum):
    PIPELINE = "pipeline"
    GRILL = "grill"
    EPIC = "epic"
    REVIEW = "review"
    REPAIR = "repair"


PIPELINE_STAGES = (
    "snapshot",
    "scout",
    "plan",
    "implement",
    "verify",
    "pr",
)
REVIEW_STAGES = (
    "review",
    "post",
    "issues",
)
REPAIR_STAGES = ("repair", "push")
GRILL_STAGES = ()
EPIC_STAGES = ()

STAGES_BY_WORKFLOW_KIND: dict[WorkflowKind, tuple[str, ...]] = {
    WorkflowKind.PIPELINE: PIPELINE_STAGES,
    WorkflowKind.GRILL: GRILL_STAGES,
    WorkflowKind.EPIC: EPIC_STAGES,
    WorkflowKind.REVIEW: REVIEW_STAGES,
    WorkflowKind.REPAIR: REPAIR_STAGES,
}

ARTIFACT_TYPES_BY_WORKFLOW_KIND: dict[WorkflowKind, tuple[str, ...]] = {
    WorkflowKind.PIPELINE: (
        "issue_snapshot",
        "scout_report",
        "implementation_plan",
        "implementation_report",
        "verification_report",
        "pr_draft",
    ),
    WorkflowKind.GRILL: ("grill_report",),
    WorkflowKind.EPIC: (),
    WorkflowKind.REVIEW: ("review_report", "created_issues_report"),
    WorkflowKind.REPAIR: ("repair_report", "repair_push_report"),
}


async def create_run(
    database_path: Path,
    *,
    run_id: str,
    owner: str,
    repo: str,
    workflow_kind: WorkflowKind | str,
    issue_number: int | None = None,
    pr_number: int | None = None,
    group_id: str | None = None,
    epic_branch_mode: str | None = None,
    now: str,
) -> None:
    kind = WorkflowKind(workflow_kind)
    async with aiosqlite.connect(database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, pr_number, group_id, workflow_type,
              status, current_stage, epic_branch_mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)
            """,
            (
                run_id,
                owner,
                repo,
                issue_number,
                pr_number,
                group_id,
                kind.value,
                epic_branch_mode,
                now,
                now,
            ),
        )
        for stage_name in STAGES_BY_WORKFLOW_KIND[kind]:
            await db.execute(
                """
                INSERT INTO workflow_stages (id, run_id, stage_name, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (str(uuid4()), run_id, stage_name),
            )
        await db.commit()


async def start_stage(
    database_path: Path,
    *,
    run_id: str,
    stage_name: str,
    status: str,
    now: str,
    workflow_kind: WorkflowKind | str | None = None,
) -> str:
    async with aiosqlite.connect(database_path) as db:
        workflow_assignment = ""
        params: list[object] = []
        if workflow_kind is not None:
            workflow_assignment = "workflow_type = ?,"
            params.append(WorkflowKind(workflow_kind).value)
        params.extend([status, stage_name, now, run_id])
        await db.execute(
            f"""
            UPDATE workflow_runs
            SET {workflow_assignment}
                status = ?,
                current_stage = ?,
                updated_at = ?
            WHERE id = ?
            """,
            params,
        )
        stage_id = await _start_stage_row(
            db,
            run_id=run_id,
            stage_name=stage_name,
            now=now,
        )
        await db.commit()
    return stage_id


async def _start_stage_row(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    stage_name: str,
    now: str,
) -> str:
    cursor = await db.execute(
        """
        SELECT id
        FROM workflow_stages
        WHERE run_id = ? AND stage_name = ? AND status = 'pending'
        ORDER BY id
        LIMIT 1
        """,
        (run_id, stage_name),
    )
    row = await cursor.fetchone()
    if row is not None:
        stage_id = str(row[0])
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'running',
                error = NULL,
                started_at = ?,
                completed_at = NULL
            WHERE id = ?
            """,
            (now, stage_id),
        )
        return stage_id

    stage_id = str(uuid4())
    await db.execute(
        """
        INSERT INTO workflow_stages (
          id, run_id, stage_name, status, started_at
        )
        VALUES (?, ?, ?, 'running', ?)
        """,
        (stage_id, run_id, stage_name, now),
    )
    return stage_id
