import asyncio
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings
from pawchestrator.db import init_db


def test_run_show_command_prints_full_detail(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(init_db(settings))
    _insert_run_detail(settings, "run-123")
    run_dir = tmp_path / "runs" / "run-123"
    (run_dir / "stdout").mkdir(parents=True)
    (run_dir / "issue.snapshot.json").write_text("{}", encoding="utf-8")
    (run_dir / "stdout" / "plan.log").write_text("planned", encoding="utf-8")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["run", "show", "run-123"])

    assert result.exit_code == 0
    assert "Metadata" in result.output
    assert "ID: run-123" in result.output
    assert "Type: pipeline" in result.output
    assert "Repository: owner/repo" in result.output
    assert "Issue/PR: #42" in result.output
    assert "Status: running" in result.output
    assert "Stages" in result.output
    assert "plan" in result.output
    assert "failed" in result.output
    assert "plan failed" in result.output
    assert "Warnings" in result.output
    assert "PLAN_WARN" in result.output
    assert "needs review" in result.output
    assert "Artifacts" in result.output
    assert str(run_dir / "issue.snapshot.json") in result.output
    assert str(run_dir / "stdout" / "plan.log") in result.output


def test_run_show_command_errors_when_run_missing(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(init_db(settings))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["run", "show", "missing-run"])

    assert result.exit_code == 1
    assert "Run not found: missing-run" in result.output


def test_run_list_command_prints_table(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(init_db(settings))
    _insert_run_detail(settings, "run-123")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["run", "list"])

    assert result.exit_code == 0
    assert "run_id" in result.output
    assert "workflow_type" in result.output
    assert "issue/pr" in result.output
    assert "current_stage" in result.output
    assert "created_at" in result.output
    assert "pr_url" in result.output
    assert "run-123" in result.output
    assert "pipeline" in result.output
    assert "#42" in result.output
    assert "running" in result.output
    assert "plan" in result.output


def test_run_list_command_passes_filters_and_default_limit(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    captured: dict[str, object] = {}

    async def fake_list_runs(
        runtime_settings: Settings,
        owner_repo: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        captured["settings"] = runtime_settings
        captured["owner_repo"] = owner_repo
        captured["status"] = status
        captured["limit"] = limit
        return [
            {
                "id": "review-run",
                "workflow_type": "review",
                "issue_number": None,
                "pr_number": 7,
                "status": "review_running",
                "current_stage": "review",
                "created_at": "2026-06-04T20:00:00Z",
                "pr_url": None,
            }
        ]

    monkeypatch.setattr(cli, "list_runs", fake_list_runs)

    result = CliRunner().invoke(
        cli.app,
        ["run", "list", "--repo", "owner/repo", "--status", "running"],
    )

    assert result.exit_code == 0
    assert captured == {
        "settings": settings,
        "owner_repo": "owner/repo",
        "status": "running",
        "limit": 20,
    }
    assert "review-run" in result.output
    assert "PR #7" in result.output


def _insert_run_detail(settings: Settings, run_id: str) -> None:
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, pr_number, workflow_type, status,
              current_stage, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "owner",
                "repo",
                42,
                None,
                "pipeline",
                "running",
                "plan",
                "2026-06-04T20:00:00Z",
                "2026-06-04T20:05:00Z",
            ),
        )
        db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, error, started_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "stage-1",
                run_id,
                "plan",
                "failed",
                "plan failed",
                "2026-06-04T20:01:00Z",
                "2026-06-04T20:02:00Z",
            ),
        )
        db.execute(
            """
            INSERT INTO run_warnings (
              id, run_id, stage_name, code, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "warning-1",
                run_id,
                "plan",
                "PLAN_WARN",
                "needs review",
                "2026-06-04T20:03:00Z",
            ),
        )
