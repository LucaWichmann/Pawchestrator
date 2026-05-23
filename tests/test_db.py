import asyncio
import sqlite3
from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.db import init_db, list_tables, upsert_worktree_record


def test_init_db_creates_mvp0_tables(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    database_path = asyncio.run(init_db(settings))

    assert database_path == tmp_path / "database.sqlite"
    assert asyncio.run(list_tables(database_path)) >= {
        "workflow_runs",
        "workflow_stages",
        "artifacts",
        "worktrees",
    }


def test_upsert_worktree_record_inserts_and_updates(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(init_db(settings))

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, created_at, updated_at
            )
            VALUES (
              'run-123', 'owner', 'repo', 42, 'plan_complete',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """
        )
        db.commit()

    asyncio.run(
        upsert_worktree_record(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
            branch="paw/issue-42-old",
            path=tmp_path / "old",
        )
    )
    asyncio.run(
        upsert_worktree_record(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
            branch="paw/issue-42-new",
            path=tmp_path / "new",
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        rows = db.execute(
            "SELECT branch, path FROM worktrees WHERE run_id = 'run-123'"
        ).fetchall()

    assert rows == [("paw/issue-42-new", str(tmp_path / "new"))]
