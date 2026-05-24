import asyncio
import sqlite3
from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_pipeline_run,
    get_github_comment_id,
    init_db,
    list_tables,
    store_github_comment_id,
    upsert_worktree_record,
)


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

    with sqlite3.connect(database_path) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }

    assert "github_comment_id" in columns
    assert "workflow_type" in columns


def test_init_db_migrates_legacy_workflow_runs_table(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    settings.app_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            """
            CREATE TABLE workflow_runs (
              id TEXT PRIMARY KEY,
              owner TEXT NOT NULL,
              repo TEXT NOT NULL,
              issue_number INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              current_stage TEXT,
              pr_url TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        db.commit()

    asyncio.run(init_db(settings))
    asyncio.run(init_db(settings))

    with sqlite3.connect(settings.database_path) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }

    assert "github_comment_id" in columns
    assert "workflow_type" in columns


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


def test_create_pipeline_run_inserts_all_pending_stages(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = 'run-123'"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE run_id = 'run-123'
            ORDER BY rowid
            """
        ).fetchall()

    assert run == ("pending", None)
    assert stages == [
        ("snapshot", "pending"),
        ("scout", "pending"),
        ("plan", "pending"),
        ("implement", "pending"),
        ("verify", "pending"),
        ("pr", "pending"),
    ]


def test_github_comment_id_helpers_store_and_fetch_id(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )

    assert asyncio.run(get_github_comment_id(settings, "run-123")) is None

    asyncio.run(store_github_comment_id(settings, "run-123", 99))

    assert asyncio.run(get_github_comment_id(settings, "run-123")) == 99
