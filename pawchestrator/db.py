"""SQLite initialization for Pawchestrator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from pawchestrator.config import Settings, ensure_app_dir
from pawchestrator.lifecycle import (
    STALE_RUN_ERROR,
    TERMINAL_RUN_STATUSES,
    fail_stale_runs_on_startup,
    skip_stage,
)
from pawchestrator.run_lifecycle import (
    WorkflowKind,
    create_run,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  issue_number INTEGER,
  pr_number INTEGER,
  group_id TEXT,
  workflow_type TEXT NOT NULL DEFAULT 'pipeline',
  status TEXT NOT NULL DEFAULT 'pending',
  current_stage TEXT,
  pr_url TEXT,
  epic_branch_mode TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_stages (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  stage_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  artifact_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_warnings (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  stage_name TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worktrees (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id),
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  branch TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_repos (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  local_path TEXT NOT NULL,
  added_at TEXT NOT NULL,
  UNIQUE(owner, repo)
);

CREATE TABLE IF NOT EXISTS checkbox_marks (
  run_id TEXT NOT NULL,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  checkbox_index INTEGER NOT NULL,
  checkbox_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, owner, repo, issue_number, checkbox_index)
);
"""


async def init_db(settings: Settings) -> Path:
    """Create app directory and initialize the MVP 0 SQLite schema."""

    ensure_app_dir(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA_SQL)
        await _add_column_if_missing(db, "github_comment_id TEXT")
        await _rebuild_workflow_runs_for_nullable_issue_number(db)
        await _add_column_if_missing(db, "pr_number INTEGER")
        await _add_column_if_missing(db, "group_id TEXT")
        await _add_column_if_missing(
            db,
            "workflow_type TEXT NOT NULL DEFAULT 'pipeline'",
        )
        await _add_column_if_missing(db, "epic_branch_mode TEXT")
        await db.commit()
    return settings.database_path


async def _add_column_if_missing(
    db: aiosqlite.Connection,
    column_definition: str,
) -> None:
    column_name = column_definition.split(maxsplit=1)[0]
    cursor = await db.execute("PRAGMA table_info(workflow_runs)")
    existing_columns = {str(row[1]) for row in await cursor.fetchall()}
    if column_name not in existing_columns:
        await db.execute(f"ALTER TABLE workflow_runs ADD COLUMN {column_definition}")


async def _rebuild_workflow_runs_for_nullable_issue_number(
    db: aiosqlite.Connection,
) -> None:
    cursor = await db.execute("PRAGMA table_info(workflow_runs)")
    columns = await cursor.fetchall()
    issue_column = next((row for row in columns if str(row[1]) == "issue_number"), None)
    if issue_column is None or not bool(issue_column[3]):
        return

    column_names = [str(row[1]) for row in columns]
    selected_columns = ", ".join(column_names)
    await db.execute("PRAGMA legacy_alter_table = ON")
    await db.execute("ALTER TABLE workflow_runs RENAME TO workflow_runs_legacy")
    await db.execute("PRAGMA legacy_alter_table = OFF")
    await db.execute(
        """
        CREATE TABLE workflow_runs (
          id TEXT PRIMARY KEY,
          owner TEXT NOT NULL,
          repo TEXT NOT NULL,
          issue_number INTEGER,
          pr_number INTEGER,
          group_id TEXT,
          workflow_type TEXT NOT NULL DEFAULT 'pipeline',
          status TEXT NOT NULL DEFAULT 'pending',
          current_stage TEXT,
          pr_url TEXT,
          github_comment_id TEXT,
          epic_branch_mode TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        f"""
        INSERT INTO workflow_runs ({selected_columns})
        SELECT {selected_columns}
        FROM workflow_runs_legacy
        """
    )
    await db.execute("DROP TABLE workflow_runs_legacy")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def create_pipeline_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
    group_id: str | None = None,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        group_id=group_id,
        workflow_kind=WorkflowKind.PIPELINE,
        now=now,
    )


async def create_review_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workflow_kind=WorkflowKind.REVIEW,
        now=now,
    )


async def create_repair_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workflow_kind=WorkflowKind.REPAIR,
        now=now,
    )


async def _create_pr_workflow_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    workflow_type: str,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workflow_kind=workflow_type,
        now=now,
    )


async def get_run_by_pr_number(
    settings: Settings,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    workflow_type: str | None = None,
) -> dict[str, object] | None:
    await init_db(settings)
    filters = [
        "owner = ?",
        "repo = ?",
        "pr_number = ?",
    ]
    params: list[object] = [owner, repo, pr_number]
    if workflow_type is not None:
        filters.append("workflow_type = ?")
        params.append(workflow_type)
    where_clause = " AND ".join(filters)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT id, owner, repo, issue_number, pr_number, workflow_type, status,
                   current_stage, pr_url, created_at, updated_at
            FROM workflow_runs
            WHERE {where_clause}
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            params,
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def create_epic_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
    group_id: str,
    epic_branch_mode: str | None = None,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    mode = epic_branch_mode or settings.pipeline.epic_branch_mode
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        group_id=group_id,
        workflow_kind=WorkflowKind.EPIC,
        epic_branch_mode=mode,
        now=now,
    )


async def set_run_pr_url(settings: Settings, *, run_id: str, pr_url: str) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET pr_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (pr_url, now, run_id),
        )
        await db.commit()


async def create_grill_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        workflow_kind=WorkflowKind.GRILL,
        now=now,
    )


async def create_epic_architect_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    await create_run(
        settings.database_path,
        run_id=run_id,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        workflow_kind=WorkflowKind.EPIC_ARCHITECT,
        now=now,
    )


async def set_grill_waiting(settings: Settings, *, run_id: str) -> None:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'grill',
                status = 'grill_waiting',
                current_stage = 'grill',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.commit()


async def upsert_worktree_record(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
    branch: str,
    path: Path,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO worktrees (
              id, run_id, owner, repo, issue_number, branch, path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              owner = excluded.owner,
              repo = excluded.repo,
              issue_number = excluded.issue_number,
              branch = excluded.branch,
              path = excluded.path,
              updated_at = excluded.updated_at
            """,
            (
                str(uuid4()),
                run_id,
                owner,
                repo,
                issue_number,
                branch,
                str(path),
                now,
                now,
            ),
        )
        await db.commit()


async def insert_repo_registration(
    settings: Settings,
    *,
    owner: str,
    repo: str,
    local_path: Path,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO github_repos (id, owner, repo, local_path, added_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner, repo) DO UPDATE SET
              local_path = excluded.local_path,
              added_at = excluded.added_at
            """,
            (str(uuid4()), owner, repo, str(local_path), now),
        )
        await db.commit()


async def lookup_repo_path(settings: Settings, *, owner: str, repo: str) -> Path | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            """
            SELECT local_path
            FROM github_repos
            WHERE owner = ? AND repo = ?
            """,
            (owner, repo),
        )
        row = await cursor.fetchone()
    return Path(row[0]) if row is not None else None


async def store_github_comment_id(
    settings: Settings,
    run_id: str,
    comment_id: int,
) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET github_comment_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(comment_id), utc_now_iso(), run_id),
        )
        await db.commit()


async def get_github_comment_id(settings: Settings, run_id: str) -> int | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            """
            SELECT github_comment_id
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


async def list_repo_registrations(settings: Settings) -> list[dict[str, str]]:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT owner, repo, local_path, added_at
            FROM github_repos
            ORDER BY owner, repo
            """
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def count_registered_repos(settings: Settings) -> int:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM github_repos")
        row = await cursor.fetchone()
    return int(row[0])


async def insert_run_warning(
    settings: Settings,
    *,
    run_id: str,
    stage_name: str,
    code: str,
    message: str,
) -> None:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO run_warnings (id, run_id, stage_name, code, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), run_id, stage_name, code, message, now),
        )
        await db.commit()


async def get_run_warnings(settings: Settings, run_id: str) -> list[dict[str, str]]:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, run_id, stage_name, code, message, created_at
            FROM run_warnings
            WHERE run_id = ?
            ORDER BY created_at
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_runs_by_group_id(settings: Settings, group_id: str) -> list[dict]:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, owner, repo, issue_number, group_id, workflow_type, status,
                   current_stage, pr_url, created_at, updated_at
            FROM workflow_runs
            WHERE group_id = ?
            ORDER BY created_at, id
            """,
            (group_id,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_latest_pipeline_runs_by_group_issue(
    settings: Settings,
    group_id: str,
) -> dict[int, dict[str, object]]:
    grouped_runs = [
        run
        for run in await get_runs_by_group_id(settings, group_id)
        if run.get("workflow_type") == "pipeline" and run.get("issue_number") is not None
    ]
    latest_by_issue: dict[int, dict[str, object]] = {}
    for run in grouped_runs:
        issue_number = int(run["issue_number"])
        existing = latest_by_issue.get(issue_number)
        if existing is None or _run_sort_key(run) >= _run_sort_key(existing):
            latest_by_issue[issue_number] = run
    return latest_by_issue


async def get_latest_failed_epic_run_by_issue(
    settings: Settings,
    *,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, group_id, status, pr_url, epic_branch_mode
            FROM workflow_runs
            WHERE owner = ?
              AND repo = ?
              AND issue_number = ?
              AND workflow_type = 'epic'
              AND status = 'epic_failed'
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (owner, repo, issue_number),
        )
        row = await cursor.fetchone()

    if row is None or row["group_id"] is None:
        return None

    parent_worktree = await get_worktree_record(settings, run_id=str(row["id"]))
    return {
        "run_id": str(row["id"]),
        "group_id": str(row["group_id"]),
        "status": str(row["status"]),
        "mode": str(row["epic_branch_mode"] or settings.pipeline.epic_branch_mode),
        "branch": None if parent_worktree is None else str(parent_worktree["branch"]),
        "pr_url": row["pr_url"],
    }


def _run_sort_key(run: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(run.get("updated_at") or ""),
        str(run.get("created_at") or ""),
        str(run.get("id") or ""),
    )


async def get_worktree_record(settings: Settings, *, run_id: str) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, run_id, owner, repo, issue_number, branch, path, created_at, updated_at
            FROM worktrees
            WHERE run_id = ?
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def mark_run_completed(
    settings: Settings,
    *,
    run_id: str,
    current_stage: str = "pr",
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'completed', current_stage = ?, updated_at = ?
            WHERE id = ?
            """,
            (current_stage, now, run_id),
        )
        await db.commit()


async def skip_pr_stage(settings: Settings, *, run_id: str, reason: str) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await skip_stage(
            db,
            run_id=run_id,
            stage_name="pr",
            reason=reason,
            pending_only=True,
            now=now,
        )
        await db.commit()


async def mark_run_failed(settings: Settings, *, run_id: str) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'failed', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.commit()


async def get_run_state(settings: Settings, run_id: str) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        run_cursor = await db.execute(
            """
            SELECT id, owner, repo, issue_number, pr_number, workflow_type, status,
                   current_stage, pr_url, github_comment_id, epic_branch_mode,
                   created_at, updated_at
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        )
        run = await run_cursor.fetchone()
        if run is None:
            return None

        stages_cursor = await db.execute(
            """
            SELECT id, run_id, stage_name, status, error, started_at, completed_at
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY
              CASE stage_name
                WHEN 'snapshot' THEN 1
                WHEN 'scout' THEN 2
                WHEN 'plan' THEN 3
                WHEN 'implement' THEN 4
                WHEN 'verify' THEN 5
                WHEN 'pr' THEN 6
                WHEN 'review' THEN 7
                WHEN 'post' THEN 8
                WHEN 'issues' THEN 9
                WHEN 'repair' THEN 10
                WHEN 'push' THEN 11
                ELSE 99
              END,
              started_at,
              id
            """,
            (run_id,),
        )
        artifacts_cursor = await db.execute(
            """
            SELECT id, run_id, artifact_type, file_path, created_at
            FROM artifacts
            WHERE run_id = ?
            ORDER BY created_at, id
            """,
            (run_id,),
        )
        stages = await stages_cursor.fetchall()
        artifacts = await artifacts_cursor.fetchall()

    payload = dict(run)
    payload["stages"] = [dict(stage) for stage in stages]
    payload["artifacts"] = [dict(artifact) for artifact in artifacts]
    payload["warnings"] = await get_run_warnings(settings, str(run["id"]))
    payload["review_report"] = _read_latest_artifact(payload, "review_report")
    payload["created_issue_urls"] = _created_issue_urls(payload)
    return payload


async def get_latest_run_by_issue(
    settings: Settings,
    owner: str,
    repo: str,
    issue_number: int,
    workflow_type: str,
) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id
            FROM workflow_runs
            WHERE owner = ?
              AND repo = ?
              AND issue_number = ?
              AND workflow_type = ?
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (owner, repo, issue_number, workflow_type),
        )
        row = await cursor.fetchone()

    if row is None:
        return None

    run = await get_run_state(settings, str(row["id"]))
    if run is None:
        return None

    warnings = await get_run_warnings(settings, str(run["id"]))
    run["warnings"] = warnings
    run["run_id"] = run.pop("id")
    if workflow_type == "pipeline":
        run.pop("artifacts", None)
        return run

    if workflow_type == "epic_architect":
        run.pop("artifacts", None)
        run.pop("pr_url", None)
        run["epic_analysis"] = None
        run["created_sub_issues"] = []
        return run

    grill_report = _read_latest_artifact(run, "grill_report")
    run.pop("artifacts", None)
    run.pop("pr_url", None)
    run["grill_report"] = grill_report
    return run


async def get_latest_grill_run_by_issue(
    settings: Settings,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict[str, object] | None:
    return await get_latest_run_by_issue(settings, owner, repo, issue_number, "grill")


async def get_latest_epic_architect_run_by_issue(
    settings: Settings,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict[str, object] | None:
    return await get_latest_run_by_issue(
        settings,
        owner,
        repo,
        issue_number,
        "epic_architect",
    )


async def get_latest_epic_run_by_issue(
    settings: Settings,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, group_id, status, pr_url, epic_branch_mode
            FROM workflow_runs
            WHERE owner = ?
              AND repo = ?
              AND issue_number = ?
              AND workflow_type = 'epic'
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (owner, repo, issue_number),
        )
        row = await cursor.fetchone()

    if row is None or row["group_id"] is None:
        return None

    parent_run_id = str(row["id"])
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT stage_name, status, started_at, completed_at, error
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (parent_run_id,),
        )
        parent_stages = [dict(stage) for stage in await cursor.fetchall()]

    group_id = str(row["group_id"])
    parent_worktree = await get_worktree_record(settings, run_id=parent_run_id)
    latest_runs_by_issue = await get_latest_pipeline_runs_by_group_issue(
        settings,
        group_id,
    )
    sub_runs = []
    for grouped_run in latest_runs_by_issue.values():
        run = await get_run_state(settings, str(grouped_run["id"]))
        if run is None:
            continue
        run["warnings"] = await get_run_warnings(settings, str(run["id"]))
        run["run_id"] = run.pop("id")
        run.pop("artifacts", None)
        sub_runs.append(run)

    return {
        "run_id": parent_run_id,
        "group_id": group_id,
        "status": str(row["status"]),
        "mode": str(row["epic_branch_mode"] or settings.pipeline.epic_branch_mode),
        "branch": None if parent_worktree is None else str(parent_worktree["branch"]),
        "pr_url": row["pr_url"],
        "epic_confirm": settings.pipeline.epic_confirm,
        "parent_stages": parent_stages,
        "sub_runs": sub_runs,
    }


async def is_repo_registered(settings: Settings, *, owner: str, repo: str) -> bool:
    return await lookup_repo_path(settings, owner=owner, repo=repo) is not None


def _read_latest_artifact(
    run: dict[str, object],
    artifact_type: str,
) -> dict[str, object] | None:
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, list):
        return None

    matching = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type
    ]
    if not matching:
        return None

    path = Path(str(matching[-1].get("file_path") or ""))
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _created_issue_urls(run: dict[str, object]) -> list[str]:
    report = _read_latest_artifact(run, "created_issues_report")
    if report is None:
        return []
    urls = report.get("created_issue_urls")
    if not isinstance(urls, list):
        return []
    return [url for url in urls if isinstance(url, str)]


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


async def list_tables(database_path: Path) -> set[str]:
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def record_checkbox_mark(
    database_path: Path,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
    checkbox_index: int,
    checkbox_text: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(database_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.execute(
            """
            INSERT INTO checkbox_marks (
              run_id, owner, repo, issue_number, checkbox_index, checkbox_text,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, owner, repo, issue_number, checkbox_index)
            DO UPDATE SET
              checkbox_text = excluded.checkbox_text,
              updated_at = excluded.updated_at
            """,
            (
                run_id,
                owner,
                repo,
                issue_number,
                checkbox_index,
                checkbox_text,
                now,
                now,
            ),
        )
        await db.commit()


async def get_checkbox_marks_for_run_issue(
    database_path: Path,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> list[dict[str, object]]:
    async with aiosqlite.connect(database_path) as db:
        await db.executescript(SCHEMA_SQL)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT run_id, owner, repo, issue_number, checkbox_index, checkbox_text,
                   created_at, updated_at
            FROM checkbox_marks
            WHERE run_id = ?
              AND owner = ?
              AND repo = ?
              AND issue_number = ?
            ORDER BY checkbox_index
            """,
            (run_id, owner, repo, issue_number),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]
