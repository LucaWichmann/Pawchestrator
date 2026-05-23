"""SQLite initialization for Pawchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from pawchestrator.config import Settings, ensure_app_dir

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  current_stage TEXT,
  pr_url TEXT,
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
"""


async def init_db(settings: Settings) -> Path:
    """Create app directory and initialize the MVP 0 SQLite schema."""

    ensure_app_dir(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    return settings.database_path


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
    stage_id = str(uuid4())
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'snapshot_running', 'snapshot', ?, ?)
            """,
            (run_id, owner, repo, issue_number, now, now),
        )
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at
            )
            VALUES (?, ?, 'snapshot', 'running', ?)
            """,
            (stage_id, run_id, now),
        )
        await db.commit()
    return stage_id


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
    stage_id = str(uuid4())
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
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at
            )
            VALUES (?, ?, 'scout', 'running', ?)
            """,
            (stage_id, run_id, now),
        )
        await db.commit()
    return stage_id


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
    stage_id = str(uuid4())
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
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at
            )
            VALUES (?, ?, 'plan', 'running', ?)
            """,
            (stage_id, run_id, now),
        )
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


async def start_implement_run(settings: Settings, *, run_id: str) -> str:
    await init_db(settings)
    stage_id = str(uuid4())
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
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at
            )
            VALUES (?, ?, 'implement', 'running', ?)
            """,
            (stage_id, run_id, now),
        )
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


async def get_run_state(settings: Settings, run_id: str) -> dict[str, object] | None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        run_cursor = await db.execute(
            """
            SELECT id, owner, repo, issue_number, status, current_stage, pr_url,
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
            ORDER BY started_at, id
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


async def list_tables(database_path: Path) -> set[str]:
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        rows = await cursor.fetchall()
    return {row[0] for row in rows}
