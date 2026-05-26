import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings, StageSettings
from pawchestrator.db import get_run_warnings, init_db
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerFailedError,
    RunnerResult,
    RunnerTask,
)
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
    assert "No prose. No progress updates. Emit valid JSON artifact only." in prompt


def test_build_scout_prompt_truncates_comments_for_prompt_only() -> None:
    comments = [
        {
            "author": f"user-{index}",
            "created_at": "2026-05-23T00:00:00Z",
            "body": "x" * 401 if index == 0 else f"comment-{index}",
        }
        for index in range(11)
    ]

    prompt = build_scout_prompt(
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "title": "Add scout",
            "body": "Body text",
            "comments": comments,
        }
    )

    assert "[truncated]" + ("x" * 400) in prompt
    assert "comment-10" not in prompt
    assert comments[0]["body"] == "x" * 401


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
    assert "[fake stdout]" in log
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


def test_run_scout_uses_codex_runner_when_stage_overrides_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"scout": StageSettings(runner="codex")},
    )
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)
    seen: dict[str, RunnerTask] = {}

    async def fake_check_health(self: CodexRunner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_run_task(self: CodexRunner, task: RunnerTask) -> RunnerResult:
        seen["task"] = task
        return FakeRunner().result

    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "run_task", fake_run_task)

    asyncio.run(run_scout(run_id, settings, repo_path=tmp_path))

    assert seen["task"].stage_name == "scout"


def test_run_scout_falls_back_to_codex_for_claude_usage_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={
            "scout": StageSettings(
                codex={"sandbox": "danger-full-access", "bypass_sandbox": True}
            )
        },
    )
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)
    seen: dict[str, object] = {}

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 429,
                    "result": "You've hit your session limit \u00b7 resets 12:20am (Europe/Berlin)",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        seen["task"] = task
        seen["sandbox"] = self.stage_overrides["scout"].codex.sandbox
        seen["bypass_sandbox"] = self.stage_overrides["scout"].codex.bypass_sandbox
        return FakeRunner().result

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(run_scout(run_id, settings, repo_path=tmp_path))

    assert result.report["readiness"] == "ready"
    assert isinstance(seen["task"], RunnerTask)
    assert seen["sandbox"] == "read-only"
    assert seen["bypass_sandbox"] is False
    log = result.log_path.read_text(encoding="utf-8")
    assert "[claude stdout]" in log
    assert "[codex stdout]" in log
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == [
        "scout_usage_limit_fallback"
    ]
    assert warnings[0]["message"] == "Claude usage limit exhausted; using Codex for scout."

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage_count = db.execute(
            """
            SELECT COUNT(*) FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'scout'
            """,
            (run_id,),
        ).fetchone()[0]

    assert stage_count == 1


def test_run_scout_does_not_fallback_for_non_usage_claude_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps({"is_error": True, "api_error_status": 500}),
            stderr="not signed in",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        raise AssertionError("codex should not run")

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError) as error:
        asyncio.run(run_scout(run_id, settings, repo_path=tmp_path))

    assert str(error.value) == "Runner exited with code 1"
    assert error.value.stderr == "not signed in"
    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_run_scout_respects_disabled_usage_limit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"scout": StageSettings(usage_limit_fallback_runner="none")},
    )
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        raise AssertionError("codex should not run")

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
        asyncio.run(run_scout(run_id, settings, repo_path=tmp_path))

    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_run_scout_fallback_failure_uses_dual_exit_code_public_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_snapshot_run(settings, run_id))
    _write_snapshot(settings, run_id)

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout="",
            stderr="codex failed",
            artifact=None,
        )

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError) as error:
        asyncio.run(run_scout(run_id, settings, repo_path=tmp_path))

    assert str(error.value) == "Claude exited with code 1; Codex fallback exited with code 1"
    assert error.value.exit_code == 1
    assert "codex failed" in error.value.stderr
    assert "usage limit reached" in error.value.stdout


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

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
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
    assert stage == ("failed", "Runner exited with code 1")


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
    assert stage[1] == "Stage failed. See local run logs."


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
