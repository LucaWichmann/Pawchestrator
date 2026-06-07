import asyncio
from pathlib import Path

import aiosqlite

from pawchestrator.config import Settings
from pawchestrator.db import get_run_state, init_db
from pawchestrator.run_clean import auto_clean_runs, clean_runs, parse_duration


def test_parse_duration_supports_days_hours_and_weeks() -> None:
    assert parse_duration("14d").days == 14
    assert parse_duration("12h").seconds == 12 * 60 * 60
    assert parse_duration("2w").days == 14


def test_clean_runs_deletes_artifacts_and_worktree_without_deleting_db_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    worktree = tmp_path / "worktree"
    artifacts = tmp_path / "runs" / "old-failed"
    worktree.mkdir()
    artifacts.mkdir(parents=True)
    (artifacts / "report.json").write_text("{}", encoding="utf-8")
    asyncio.run(_insert_run(settings, "old-failed", "failed", "2026-05-01T00:00:00Z"))
    asyncio.run(_insert_worktree(settings, "old-failed", worktree))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        worktree.rmdir()

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr("pawchestrator.run_clean.subprocess.run", fake_run)

    results = asyncio.run(clean_runs(settings, older_than="14d", statuses=["failed"]))

    assert [result.target.run_id for result in results] == ["old-failed"]
    assert not artifacts.exists()
    assert not worktree.exists()
    assert calls[0][0] == ["git", "worktree", "remove", "--force", str(worktree)]
    assert asyncio.run(get_run_state(settings, "old-failed")) is not None


def test_clean_runs_dry_run_lists_without_deleting(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    artifacts = tmp_path / "runs" / "old-complete"
    artifacts.mkdir(parents=True)
    asyncio.run(_insert_run(settings, "old-complete", "completed", "2026-05-01T00:00:00Z"))

    def fail_remove(*_args, **_kwargs):
        raise AssertionError("dry-run must not remove worktrees")

    monkeypatch.setattr("pawchestrator.run_clean.subprocess.run", fail_remove)

    results = asyncio.run(
        clean_runs(settings, older_than="14d", statuses=["complete"], dry_run=True)
    )

    assert [result.target.run_id for result in results] == ["old-complete"]
    assert results[0].dry_run is True
    assert artifacts.exists()


def test_auto_clean_uses_configured_age_and_skips_active_runs(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    settings.pipeline.auto_clean = "14d"
    old_failed = tmp_path / "runs" / "old-failed"
    active = tmp_path / "runs" / "active"
    fresh_failed = tmp_path / "runs" / "fresh-failed"
    for path in [old_failed, active, fresh_failed]:
        path.mkdir(parents=True)
    asyncio.run(_insert_run(settings, "old-failed", "failed", "2026-05-01T00:00:00Z"))
    asyncio.run(_insert_run(settings, "active", "running", "2026-05-01T00:00:00Z"))
    asyncio.run(_insert_run(settings, "fresh-failed", "failed", "2999-01-01T00:00:00Z"))

    results = asyncio.run(auto_clean_runs(settings))

    assert [result.target.run_id for result in results] == ["old-failed"]
    assert not old_failed.exists()
    assert active.exists()
    assert fresh_failed.exists()


async def _insert_run(
    settings: Settings,
    run_id: str,
    status: str,
    updated_at: str,
) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, workflow_type, status,
              current_stage, created_at, updated_at
            )
            VALUES (?, 'owner', 'repo', 42, 'pipeline', ?, 'pr', ?, ?)
            """,
            (run_id, status, updated_at, updated_at),
        )
        await db.commit()


async def _insert_worktree(settings: Settings, run_id: str, path: Path) -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO worktrees (
              id, run_id, owner, repo, issue_number, branch, path, created_at, updated_at
            )
            VALUES (?, ?, 'owner', 'repo', 42, 'branch', ?, '2026-05-01T00:00:00Z',
                    '2026-05-01T00:00:00Z')
            """,
            (f"worktree-{run_id}", run_id, str(path)),
        )
        await db.commit()
