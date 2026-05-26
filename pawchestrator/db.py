"""SQLite initialization for Pawchestrator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from pawchestrator.config import Settings, ensure_app_dir

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
)
TERMINAL_RUN_STATUSES = (
    "completed",
    "failed",
    "grill_complete",
    "grill_failed",
    "epic_complete",
    "epic_failed",
    "post_complete",
    "post_failed",
    "review_failed",
)
STALE_RUN_ERROR = "Run aborted: Pawchestrator stopped before this run finished."

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


async def create_snapshot_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (run_id, owner, repo, issue_number, now, now),
        )
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'snapshot_running',
                current_stage = 'snapshot',
                owner = ?,
                repo = ?,
                issue_number = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (owner, repo, issue_number, now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="snapshot", now=now)
        await db.commit()
    return stage_id


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
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, group_id, workflow_type, status, current_stage,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'pipeline', 'pending', NULL, ?, ?)
            """,
            (run_id, owner, repo, issue_number, group_id, now, now),
        )
        for stage_name in PIPELINE_STAGES:
            await db.execute(
                """
                INSERT INTO workflow_stages (id, run_id, stage_name, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (str(uuid4()), run_id, stage_name),
            )
        await db.commit()


async def create_review_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> None:
    await _create_pr_workflow_run(
        settings,
        run_id=run_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workflow_type="review",
    )


async def start_review_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'review_running',
                current_stage = 'review',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(
            db,
            run_id=run_id,
            stage_name="review",
            now=now,
        )
        await db.commit()
    return stage_id


async def complete_review_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'review_complete',
                current_stage = 'review',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'review_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def start_review_post_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'post_running',
                current_stage = 'post',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(
            db,
            run_id=run_id,
            stage_name="post",
            now=now,
        )
        await db.commit()
    return stage_id


async def complete_review_post_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'post_complete',
                current_stage = 'post',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.commit()


async def fail_review_post_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'post_failed',
                current_stage = 'post',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


async def fail_review_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'review',
                status = 'review_failed',
                current_stage = 'review',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


async def create_repair_run(
    settings: Settings,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> None:
    await _create_pr_workflow_run(
        settings,
        run_id=run_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workflow_type="repair",
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
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, pr_number, workflow_type, status, current_stage,
              created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, ?, ?, 'pending', NULL, ?, ?)
            """,
            (run_id, owner, repo, pr_number, workflow_type, now, now),
        )
        if workflow_type == "review":
            for stage_name in REVIEW_STAGES:
                await db.execute(
                    """
                    INSERT INTO workflow_stages (id, run_id, stage_name, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (str(uuid4()), run_id, stage_name),
                )
        await db.commit()


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
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, group_id, workflow_type, status, current_stage,
              epic_branch_mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'epic', 'pending', NULL, ?, ?, ?)
            """,
            (run_id, owner, repo, issue_number, group_id, mode, now, now),
        )
        await db.commit()


async def start_epic_run(settings: Settings, *, run_id: str) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'epic_running', current_stage = 'epic', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.commit()


async def complete_epic_run(
    settings: Settings,
    *,
    run_id: str,
    pr_url: str | None,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'epic_complete', current_stage = 'epic',
                pr_url = COALESCE(?, pr_url), updated_at = ?
            WHERE id = ?
            """,
            (pr_url, now, run_id),
        )
        await db.commit()


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


async def fail_epic_run(settings: Settings, *, run_id: str) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'epic_failed', current_stage = 'epic', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
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
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, workflow_type, status, current_stage,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'grill', 'pending', NULL, ?, ?)
            """,
            (run_id, owner, repo, issue_number, now, now),
        )
        await db.commit()


async def complete_snapshot_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'snapshot_complete', current_stage = 'snapshot', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'issue_snapshot', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_snapshot_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'snapshot_failed', current_stage = 'snapshot', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


async def start_scout_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'scout_running', current_stage = 'scout', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="scout", now=now)
        await db.commit()
    return stage_id


async def start_grill_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'grill',
                status = 'grill_running',
                current_stage = 'grill',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="grill", now=now)
        await db.commit()
    return stage_id


async def complete_grill_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'grill',
                status = 'grill_complete',
                current_stage = 'grill',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'grill_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_grill_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET workflow_type = 'grill',
                status = 'grill_failed',
                current_stage = 'grill',
                updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


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


async def complete_scout_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'scout_complete', current_stage = 'scout', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'scout_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_scout_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'scout_failed', current_stage = 'scout', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


async def start_plan_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'plan_running', current_stage = 'plan', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="plan", now=now)
        await db.commit()
    return stage_id


async def complete_plan_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'plan_complete', current_stage = 'plan', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'implementation_plan', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_plan_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'plan_failed', current_stage = 'plan', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
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


async def start_implement_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'implement_running', current_stage = 'implement', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="implement", now=now)
        await db.commit()
    return stage_id


async def complete_implement_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'implement_complete', current_stage = 'implement', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'implementation_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_implement_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'implement_failed', current_stage = 'implement', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


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


async def start_verify_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'verify_running', current_stage = 'verify', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="verify", now=now)
        await db.commit()
    return stage_id


async def complete_verify_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
    passed: bool,
    error: str | None = None,
) -> None:
    now = utc_now_iso()
    run_status = "verify_complete" if passed else "verify_failed"
    stage_status = "complete" if passed else "failed"
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = ?, current_stage = 'verify', updated_at = ?
            WHERE id = ?
            """,
            (run_status, now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = ?, error = ?, completed_at = ?
            WHERE id = ?
            """,
            (stage_status, error, now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'verification_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def skip_verify_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
    reason: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'verify_skipped', current_stage = 'verify', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'skipped', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (reason, now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'verification_report', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_verify_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'verify_failed', current_stage = 'verify', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


async def start_pr_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'pr_running', current_stage = 'pr', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        stage_id = await _start_stage_row(db, run_id=run_id, stage_name="pr", now=now)
        await db.commit()
    return stage_id


async def complete_pr_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    artifact_path: Path,
    pr_url: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'pr_complete', current_stage = 'pr', pr_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (pr_url, now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'complete', completed_at = ?
            WHERE id = ?
            """,
            (now, stage_id),
        )
        await db.execute(
            """
            INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
            VALUES (?, ?, 'pr_draft', ?, ?)
            """,
            (str(uuid4()), run_id, str(artifact_path), now),
        )
        await db.commit()


async def fail_pr_run(
    settings: Settings,
    *,
    run_id: str,
    stage_id: str,
    error: str,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = 'pr_failed', current_stage = 'pr', updated_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'failed', error = ?, completed_at = ?
            WHERE id = ?
            """,
            (error, now, stage_id),
        )
        await db.commit()


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
        await db.execute(
            """
            UPDATE workflow_stages
            SET status = 'skipped', error = ?, completed_at = ?
            WHERE run_id = ? AND stage_name = 'pr' AND status = 'pending'
            """,
            (reason, now, run_id),
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


async def fail_stale_runs_on_startup(settings: Settings) -> int:
    await init_db(settings)
    now = utc_now_iso()
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
                now=now,
            )
            cleaned += 1

        await db.commit()
    return cleaned


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
    group_id = str(row["group_id"])
    parent_worktree = await get_worktree_record(settings, run_id=parent_run_id)
    sub_runs = []
    for grouped_run in await get_runs_by_group_id(settings, group_id):
        if grouped_run.get("workflow_type") != "pipeline":
            continue
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


async def _stale_failure_stage(
    db: aiosqlite.Connection,
    run: aiosqlite.Row,
) -> str:
    workflow_type = str(run["workflow_type"] or "pipeline")
    if workflow_type == "grill":
        return "grill"

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
    return stage if stage in PIPELINE_STAGES or stage in REVIEW_STAGES else None


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
            (str(uuid4()), run_id, stage_name, STALE_RUN_ERROR, now, now),
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
        (STALE_RUN_ERROR, now, now, str(row["id"])),
    )


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
