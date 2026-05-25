import asyncio
import sqlite3
from pathlib import Path
from uuid import UUID

from pawchestrator.config import Settings
from pawchestrator.db import (
    TERMINAL_RUN_STATUSES,
    complete_verify_run,
    create_epic_run,
    create_pipeline_run,
    create_grill_run,
    fail_verify_run,
    fail_stale_runs_on_startup,
    get_github_comment_id,
    get_latest_grill_run_by_issue,
    get_runs_by_group_id,
    get_run_warnings,
    init_db,
    insert_run_warning,
    list_tables,
    set_grill_waiting,
    skip_verify_run,
    STALE_RUN_ERROR,
    start_verify_run,
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


def test_create_epic_run_inserts_parent_without_stages(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    asyncio.run(
        create_epic_run(
            settings,
            run_id="epic-parent",
            owner="owner",
            repo="repo",
            issue_number=42,
            group_id="epic-group",
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            """
            SELECT status, current_stage, group_id, workflow_type
            FROM workflow_runs
            WHERE id = 'epic-parent'
            """
        ).fetchone()
        stages = db.execute(
            "SELECT COUNT(*) FROM workflow_stages WHERE run_id = 'epic-parent'"
        ).fetchone()

    assert run == ("pending", None, "epic-group", "epic")
    assert stages == (0,)


def test_skip_verify_run_writes_skipped_status_and_reason(tmp_path: Path) -> None:
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
    stage_id = asyncio.run(start_verify_run(settings, run_id="run-123"))

    asyncio.run(
        skip_verify_run(
            settings,
            run_id="run-123",
            stage_id=stage_id,
            artifact_path=tmp_path / "runs" / "run-123" / "verification.json",
            reason="verification intentionally bypassed",
        )
    )

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            """
            SELECT status, current_stage
            FROM workflow_runs
            WHERE id = 'run-123'
            """
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error, completed_at
            FROM workflow_stages
            WHERE id = ?
            """,
            (stage_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path
            FROM artifacts
            WHERE run_id = 'run-123'
            """
        ).fetchone()

    assert run == ("verify_skipped", "verify")
    assert stage[:2] == ("skipped", "verification intentionally bypassed")
    assert stage[2].endswith("Z")
    assert artifact == (
        "verification_report",
        str(tmp_path / "runs" / "run-123" / "verification.json"),
    )


def test_verify_complete_and_fail_paths_are_unchanged(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="complete-run",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="failed-run",
            owner="owner",
            repo="repo",
            issue_number=43,
        )
    )
    complete_stage_id = asyncio.run(
        start_verify_run(settings, run_id="complete-run")
    )
    failed_stage_id = asyncio.run(start_verify_run(settings, run_id="failed-run"))

    asyncio.run(
        complete_verify_run(
            settings,
            run_id="complete-run",
            stage_id=complete_stage_id,
            artifact_path=tmp_path / "runs" / "complete-run" / "verification.json",
            passed=True,
        )
    )
    asyncio.run(
        fail_verify_run(
            settings,
            run_id="failed-run",
            stage_id=failed_stage_id,
            error="verification failed",
        )
    )

    complete_run, complete_stages = _fetch_run_and_stages(tmp_path, "complete-run")
    failed_run, failed_stages = _fetch_run_and_stages(tmp_path, "failed-run")

    assert complete_run == ("verify_complete", "verify", None)
    assert complete_stages["verify"] == ("complete", None)
    assert failed_run == ("verify_failed", "verify", None)
    assert failed_stages["verify"] == ("failed", "verification failed")


def test_fail_stale_runs_marks_pending_pipeline_failed(tmp_path: Path) -> None:
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

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("failed", "snapshot", None)
    assert stages["snapshot"] == ("failed", STALE_RUN_ERROR)
    assert stages["scout"] == ("pending", None)


def test_fail_stale_runs_marks_running_stage_failed(tmp_path: Path) -> None:
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
    _set_run_state(tmp_path, "run-123", status="plan_running", current_stage="plan")
    _set_stage_state(tmp_path, "run-123", "plan", "running")

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("failed", "plan", None)
    assert stages["plan"] == ("failed", STALE_RUN_ERROR)


def test_fail_stale_runs_marks_next_stage_after_completed_stage_failed(
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
    _set_run_state(tmp_path, "run-123", status="plan_complete", current_stage="plan")
    for stage_name in ("snapshot", "scout", "plan"):
        _set_stage_state(tmp_path, "run-123", stage_name, "complete")

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("failed", "implement", None)
    assert stages["plan"] == ("complete", None)
    assert stages["implement"] == ("failed", STALE_RUN_ERROR)


def test_fail_stale_runs_marks_pr_complete_with_url_completed(tmp_path: Path) -> None:
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
    _set_run_state(
        tmp_path,
        "run-123",
        status="pr_complete",
        current_stage="pr",
        pr_url="https://github.com/owner/repo/pull/99",
    )
    _set_stage_state(tmp_path, "run-123", "pr", "complete")

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("completed", "pr", "https://github.com/owner/repo/pull/99")
    assert stages["pr"] == ("complete", None)


def test_fail_stale_runs_leaves_terminal_runs_unchanged(tmp_path: Path) -> None:
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
    _set_run_state(tmp_path, "run-123", status="completed", current_stage="pr")

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 0
    run, _ = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("completed", "pr", None)


def test_grill_waiting_is_not_terminal() -> None:
    assert "grill_waiting" not in TERMINAL_RUN_STATUSES


def test_set_grill_waiting_transitions_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_grill_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )

    asyncio.run(set_grill_waiting(settings, run_id="run-123"))

    run, _ = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("grill_waiting", "grill", None)


def test_fail_stale_runs_skips_grill_waiting(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_grill_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    asyncio.run(set_grill_waiting(settings, run_id="run-123"))

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 0
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("grill_waiting", "grill", None)
    assert stages == {}


def test_grill_waiting_survives_repeated_startup_cleanup(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_grill_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    asyncio.run(set_grill_waiting(settings, run_id="run-123"))

    first_cleaned = asyncio.run(fail_stale_runs_on_startup(settings))
    second_cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert first_cleaned == 0
    assert second_cleaned == 0
    run, _ = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("grill_waiting", "grill", None)


def test_get_latest_grill_run_by_issue_returns_grill_waiting(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_grill_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    asyncio.run(set_grill_waiting(settings, run_id="run-123"))

    run = asyncio.run(
        get_latest_grill_run_by_issue(settings, "owner", "repo", 42)
    )

    assert run is not None
    assert run["run_id"] == "run-123"
    assert run["status"] == "grill_waiting"
    assert run["workflow_type"] == "grill"


def test_fail_stale_runs_marks_grill_failed(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_grill_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    _set_run_state(tmp_path, "run-123", status="grill_running", current_stage="grill")

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "run-123")
    assert run == ("failed", "grill", None)
    assert stages["grill"] == ("failed", STALE_RUN_ERROR)


def test_fail_stale_runs_marks_epic_parent_failed(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_epic_run(
            settings,
            run_id="epic-parent",
            owner="owner",
            repo="repo",
            issue_number=42,
            group_id="epic-group",
        )
    )
    _set_run_state(
        tmp_path,
        "epic-parent",
        status="epic_running",
        current_stage="epic",
    )

    cleaned = asyncio.run(fail_stale_runs_on_startup(settings))

    assert cleaned == 1
    run, stages = _fetch_run_and_stages(tmp_path, "epic-parent")
    assert run == ("epic_failed", "epic", None)
    assert stages == {}


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


def _set_run_state(
    tmp_path: Path,
    run_id: str,
    *,
    status: str,
    current_stage: str | None,
    pr_url: str | None = None,
) -> None:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.execute(
            """
            UPDATE workflow_runs
            SET status = ?, current_stage = ?, pr_url = ?
            WHERE id = ?
            """,
            (status, current_stage, pr_url, run_id),
        )
        db.commit()


def _set_stage_state(
    tmp_path: Path,
    run_id: str,
    stage_name: str,
    status: str,
) -> None:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        db.execute(
            """
            UPDATE workflow_stages
            SET status = ?, error = NULL
            WHERE run_id = ? AND stage_name = ?
            """,
            (status, run_id, stage_name),
        )
        db.commit()


def _fetch_run_and_stages(
    tmp_path: Path,
    run_id: str,
) -> tuple[tuple[str, str | None, str | None], dict[str, tuple[str, str | None]]]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            """
            SELECT status, current_stage, pr_url
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        stage_rows = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (run_id,),
        ).fetchall()
    return run, {name: (status, error) for name, status, error in stage_rows}
