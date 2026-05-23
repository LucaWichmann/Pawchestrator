import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings
from pawchestrator.db import init_db
from pawchestrator.runners import Runner, RunnerResult, RunnerTask
from pawchestrator.scout import build_scout_prompt, run_scout


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(
        self,
        *,
        healthy: bool = True,
        result: RunnerResult | None = None,
    ) -> None:
        self.healthy = healthy
        self.result = result or RunnerResult(
            exit_code=0,
            stdout='{"result": "ok"}',
            stderr="",
            artifact={
                "schema": "pawchestrator.scout_report.v1",
                "status": "success",
                "readiness": "ready",
                "risk": "low",
                "findings": [{"kind": "scope", "text": "Small change"}],
                "risks": [],
                "next_recommended_stage": "plan",
            },
        )
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return self.healthy, "fake unhealthy"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.task = task
        return self.result


def test_build_scout_prompt_includes_issue_context() -> None:
    prompt = build_scout_prompt(
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "title": "Add scout",
            "body": "Body text",
            "comments": [
                {
                    "author": "alice",
                    "created_at": "2026-05-23T00:00:00Z",
                    "body": "Needs tests",
                }
            ],
        }
    )

    assert "Issue: #42 - Add scout" in prompt
    assert "Repository: owner/repo" in prompt
    assert "Body text" in prompt
    assert "alice at 2026-05-23T00:00:00Z" in prompt
    assert "Needs tests" in prompt


def test_run_scout_writes_artifact_log_and_records_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)
    runner = FakeRunner()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    result = asyncio.run(
        run_scout(run_id, settings, repo_path=repo_path, runner=runner)
    )

    assert runner.task is not None
    assert runner.task.cwd == repo_path.resolve()
    assert "Issue: #42 - Add scout" in runner.task.prompt
    assert result.artifact_path == tmp_path / "runs" / run_id / "scout_report.json"
    assert result.log_path == tmp_path / "runs" / run_id / "stdout" / "scout.log"
    assert result.report["readiness"] == "ready"

    report = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    log = result.log_path.read_text(encoding="utf-8")
    assert report["next_recommended_stage"] == "plan"
    assert "[stdout]" in log
    assert '{"result": "ok"}' in log

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'scout'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'scout_report'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("scout_complete", "scout")
    assert stage == ("complete", None)
    assert artifact == ("scout_report", str(result.artifact_path))


def test_run_scout_records_failure_and_log(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=1,
            stdout="",
            stderr="not signed in",
            artifact=None,
        )
    )

    with pytest.raises(RuntimeError, match="not signed in"):
        asyncio.run(run_scout(run_id, settings, repo_path=tmp_path, runner=runner))

    log_path = tmp_path / "runs" / run_id / "stdout" / "scout.log"
    assert "not signed in" in log_path.read_text(encoding="utf-8")
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'scout'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("scout_failed", "scout")
    assert stage == ("failed", "not signed in")


def test_run_scout_rejects_empty_findings(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=0,
            stdout='{"result": {}}',
            stderr="",
            artifact={"schema": "pawchestrator.scout_report.v1", "findings": []},
        )
    )

    with pytest.raises(ValueError, match="findings"):
        asyncio.run(run_scout(run_id, settings, repo_path=tmp_path, runner=runner))

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'scout'
            """,
            (run_id,),
        ).fetchone()

    assert stage[0] == "failed"
    assert "findings" in stage[1]


def test_run_scout_requires_snapshot(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_snapshot_run(settings, "run-123"))

    with pytest.raises(FileNotFoundError, match="issue snapshot not found"):
        asyncio.run(run_scout("run-123", settings, repo_path=tmp_path, runner=FakeRunner()))


def test_run_scout_command_prints_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(app_dir=tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    async def fake_run_scout(run_id: str, settings: Settings) -> Any:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path

        class Result:
            report = {
                "schema": "pawchestrator.scout_report.v1",
                "status": "success",
                "readiness": "ready",
                "risk": "low",
                "findings": [],
                "risks": [],
                "next_recommended_stage": "plan",
            }

        return Result()

    monkeypatch.setattr(cli, "run_scout", fake_run_scout)

    result = CliRunner().invoke(cli.app, ["run", "scout", "run-123"])

    assert result.exit_code == 0
    assert '"readiness": "ready"' in result.output


def test_run_scout_command_reports_missing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    result = CliRunner().invoke(cli.app, ["run", "scout", "missing"])

    assert result.exit_code == 1
    assert "Scout failed: run not found: missing" in result.output


async def _insert_snapshot_run(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'snapshot_complete', 'snapshot',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at, completed_at
            )
            VALUES (
              'stage-123', ?, 'snapshot', 'complete',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.commit()


def _write_snapshot(settings: Settings, run_id: str) -> None:
    path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.issue_snapshot.v1",
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "title": "Add scout",
                "body": "Issue body",
                "comments": [],
            }
        ),
        encoding="utf-8",
    )
