import asyncio
import sqlite3
from pathlib import Path
from uuid import UUID

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_pipeline_run,
    get_github_comment_id,
    get_runs_by_group_id,
    get_run_warnings,
    init_db,
    insert_run_warning,
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
        "run_warnings",
        "worktrees",
    }

    with sqlite3.connect(database_path) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }

    assert "github_comment_id" in columns
    assert "group_id" in columns
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
        db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, created_at, updated_at
            )
            VALUES (
              'legacy-run', 'owner', 'repo', 42, 'pending',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """
        )
        row = db.execute(
            "SELECT group_id FROM workflow_runs WHERE id = 'legacy-run'"
        ).fetchone()

    assert "github_comment_id" in columns
    assert "group_id" in columns
    assert "workflow_type" in columns
    assert row == (None,)


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
            "SELECT status, current_stage, group_id FROM workflow_runs WHERE id = 'run-123'"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE run_id = 'run-123'
            ORDER BY rowid
            """
        ).fetchall()

    assert run == ("pending", None, None)
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


def test_insert_run_warning_inserts_uuid_and_timestamp(tmp_path: Path) -> None:
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

    asyncio.run(
        insert_run_warning(
            settings,
            run_id="run-123",
            stage_name="scout",
            code="assignment_lookup_failed",
            message="Could not find an assignee",
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT id, run_id, stage_name, code, message, created_at
            FROM run_warnings
            """
        ).fetchone()

    assert row[1:5] == (
        "run-123",
        "scout",
        "assignment_lookup_failed",
        "Could not find an assignee",
    )
    assert str(UUID(row[0])) == row[0]
    assert row[5].endswith("Z")


def test_get_run_warnings_returns_warnings_ordered_by_created_at(
    tmp_path: Path,
) -> None:
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
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="other-run",
            owner="owner",
            repo="repo",
            issue_number=43,
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.executemany(
            """
            INSERT INTO run_warnings (
              id, run_id, stage_name, code, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "warning-2",
                    "run-123",
                    "plan",
                    "second",
                    "Second warning",
                    "2026-05-24T10:00:02Z",
                ),
                (
                    "warning-other",
                    "other-run",
                    "plan",
                    "other",
                    "Other warning",
                    "2026-05-24T10:00:00Z",
                ),
                (
                    "warning-1",
                    "run-123",
                    "scout",
                    "first",
                    "First warning",
                    "2026-05-24T10:00:01Z",
                ),
            ],
        )
        db.commit()

    warnings = asyncio.run(get_run_warnings(settings, "run-123"))

    assert warnings == [
        {
            "id": "warning-1",
            "run_id": "run-123",
            "stage_name": "scout",
            "code": "first",
            "message": "First warning",
            "created_at": "2026-05-24T10:00:01Z",
        },
        {
            "id": "warning-2",
            "run_id": "run-123",
            "stage_name": "plan",
            "code": "second",
            "message": "Second warning",
            "created_at": "2026-05-24T10:00:02Z",
        },
    ]


def test_get_runs_by_group_id_returns_matching_runs_ordered_by_created_at(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(init_db(settings))

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.executemany(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, group_id, workflow_type, status,
              current_stage, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "child-2",
                    "owner",
                    "repo",
                    43,
                    "epic-run",
                    "pipeline",
                    "pending",
                    None,
                    "2026-05-24T10:00:02Z",
                    "2026-05-24T10:00:02Z",
                ),
                (
                    "other-child",
                    "owner",
                    "repo",
                    44,
                    "other-epic-run",
                    "pipeline",
                    "pending",
                    None,
                    "2026-05-24T10:00:00Z",
                    "2026-05-24T10:00:00Z",
                ),
                (
                    "child-1",
                    "owner",
                    "repo",
                    42,
                    "epic-run",
                    "pipeline",
                    "scout_running",
                    "scout",
                    "2026-05-24T10:00:01Z",
                    "2026-05-24T10:00:03Z",
                ),
            ],
        )
        db.commit()

    runs = asyncio.run(get_runs_by_group_id(settings, "epic-run"))

    assert [run["id"] for run in runs] == ["child-1", "child-2"]
    assert runs[0] == {
        "id": "child-1",
        "owner": "owner",
        "repo": "repo",
        "issue_number": 42,
        "group_id": "epic-run",
        "workflow_type": "pipeline",
        "status": "scout_running",
        "current_stage": "scout",
        "pr_url": None,
        "created_at": "2026-05-24T10:00:01Z",
        "updated_at": "2026-05-24T10:00:03Z",
    }
