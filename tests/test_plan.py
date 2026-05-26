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
from pawchestrator.plan import (
    build_plan_prompt,
    normalize_implementation_plan,
    run_plan,
)
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerFailedError,
    RunnerResult,
    RunnerTask,
)


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
                "schema": "pawchestrator.implementation_plan.v1",
                "approach_summary": "Add plan stage using existing runner flow.",
                "steps": [
                    {
                        "order": 1,
                        "description": "Add plan orchestration.",
                        "files_to_modify": ["pawchestrator/plan.py"],
                        "notes": "Mirror scout stage.",
                    },
                    {
                        "order": 2,
                        "description": "Wire CLI command.",
                        "files_to_modify": ["pawchestrator/cli.py"],
                        "notes": "Print summary.",
                    },
                ],
                "files_to_modify": [
                    "pawchestrator/plan.py",
                    "pawchestrator/cli.py",
                ],
                "estimated_risk": "low",
            },
        )
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return self.healthy, "fake unhealthy"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.task = task
        return self.result


def test_build_plan_prompt_includes_issue_and_scout_report() -> None:
    snapshot = {
        "owner": "owner",
        "repo": "repo",
        "number": 42,
        "title": "Add plan",
        "body": "Issue body",
        "comments": [{"author": "alice", "body": "Needs tests"}],
    }
    scout_report = {
        "schema": "pawchestrator.scout_report.v1",
        "findings": [{"kind": "scope", "text": "Small change"}],
    }

    prompt = build_plan_prompt(snapshot, scout_report)

    assert "Issue: #42 - Add plan" in prompt
    assert "Repository: owner/repo" in prompt
    assert "Issue body" in prompt
    assert '"author": "alice"' in prompt
    assert '"text": "Small change"' in prompt
    assert "pawchestrator.implementation_plan.v1" in prompt
    assert "No prose. No progress updates. Emit valid JSON artifact only." in prompt


def test_build_plan_prompt_truncates_scout_findings_and_risks_for_prompt_only() -> None:
    scout_report = {
        "schema": "pawchestrator.scout_report.v1",
        "findings": [{"kind": "scope", "text": f"finding-{index}"} for index in range(6)],
        "risks": [{"level": "low", "text": f"risk-{index}"} for index in range(6)],
    }

    prompt = build_plan_prompt(
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "title": "Add plan",
            "body": "Issue body",
            "comments": [],
        },
        scout_report,
    )

    assert "finding-4" in prompt
    assert "finding-5" not in prompt
    assert "risk-4" in prompt
    assert "risk-5" not in prompt
    assert len(scout_report["findings"]) == 6
    assert len(scout_report["risks"]) == 6


def test_run_plan_writes_artifact_log_and_records_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)
    runner = FakeRunner()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    result = asyncio.run(run_plan(run_id, settings, repo_path=repo_path, runner=runner))

    assert runner.task is not None
    assert runner.task.cwd == repo_path.resolve()
    assert runner.task.stage_name == "plan"
    assert "Issue: #42 - Add plan" in runner.task.prompt
    assert result.artifact_path == tmp_path / "runs" / run_id / "implementation_plan.json"
    assert result.log_path == tmp_path / "runs" / run_id / "stdout" / "plan.log"
    assert result.plan["estimated_risk"] == "low"

    plan = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    log = result.log_path.read_text(encoding="utf-8")
    assert plan["steps"][0]["description"] == "Add plan orchestration."
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
            WHERE run_id = ? AND stage_name = 'plan'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'implementation_plan'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("plan_complete", "plan")
    assert stage == ("complete", None)
    assert artifact == ("implementation_plan", str(result.artifact_path))


def test_run_plan_uses_codex_runner_when_stage_overrides_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"plan": StageSettings(runner="codex")},
    )
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)
    seen: dict[str, RunnerTask] = {}

    async def fake_check_health(self: CodexRunner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_run_task(self: CodexRunner, task: RunnerTask) -> RunnerResult:
        seen["task"] = task
        return FakeRunner().result

    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "run_task", fake_run_task)

    asyncio.run(run_plan(run_id, settings, repo_path=tmp_path))

    assert seen["task"].stage_name == "plan"


def test_run_plan_falls_back_to_codex_for_claude_usage_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={
            "plan": StageSettings(
                codex={"sandbox": "danger-full-access", "bypass_sandbox": True}
            )
        },
    )
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)
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
        seen["task"] = task
        seen["sandbox"] = self.stage_overrides["plan"].codex.sandbox
        seen["bypass_sandbox"] = self.stage_overrides["plan"].codex.bypass_sandbox
        return FakeRunner().result

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(run_plan(run_id, settings, repo_path=tmp_path))

    assert result.plan["estimated_risk"] == "low"
    assert isinstance(seen["task"], RunnerTask)
    assert seen["sandbox"] == "read-only"
    assert seen["bypass_sandbox"] is False
    log = result.log_path.read_text(encoding="utf-8")
    assert "[claude stdout]" in log
    assert "[codex stdout]" in log
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == ["plan_usage_limit_fallback"]
    assert warnings[0]["message"] == "Claude usage limit exhausted; using Codex for plan."

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage_count = db.execute(
            """
            SELECT COUNT(*) FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'plan'
            """,
            (run_id,),
        ).fetchone()[0]

    assert stage_count == 1


def test_run_plan_respects_disabled_usage_limit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"plan": StageSettings(usage_limit_fallback_runner="none")},
    )
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)

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
        asyncio.run(run_plan(run_id, settings, repo_path=tmp_path))

    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_run_plan_codex_primary_does_not_self_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"plan": StageSettings(runner="codex")},
    )
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)

    async def fake_check_health(self: CodexRunner) -> tuple[bool, str]:
        return True, "ok"

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

    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
        asyncio.run(run_plan(run_id, settings, repo_path=tmp_path))

    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_run_plan_fallback_failure_uses_dual_exit_code_public_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)

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
        asyncio.run(run_plan(run_id, settings, repo_path=tmp_path))

    assert str(error.value) == "Claude exited with code 1; Codex fallback exited with code 1"
    assert error.value.exit_code == 1
    assert "codex failed" in error.value.stderr
    assert "usage limit reached" in error.value.stdout


def test_run_plan_records_failure_and_log(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)
    _write_scout_report(settings, run_id)
    runner = FakeRunner(
        result=RunnerResult(
            exit_code=1,
            stdout="",
            stderr="not signed in",
            artifact=None,
        )
    )

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
        asyncio.run(run_plan(run_id, settings, repo_path=tmp_path, runner=runner))

    log_path = tmp_path / "runs" / run_id / "stdout" / "plan.log"
    assert "not signed in" in log_path.read_text(encoding="utf-8")
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'plan'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("plan_failed", "plan")
    assert stage == ("failed", "Runner exited with code 1")
    assert "not signed in" not in stage[1]


def test_run_plan_sanitizes_generic_exception_in_db(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_scout_run(settings, run_id))
    _write_snapshot(settings, run_id)

    with pytest.raises(FileNotFoundError, match="scout report not found"):
        asyncio.run(run_plan(run_id, settings, repo_path=tmp_path, runner=FakeRunner()))

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage_error = db.execute(
            """
            SELECT error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'plan'
            """,
            (run_id,),
        ).fetchone()[0]

    assert stage_error == "Stage failed. See local run logs."
    assert "scout report not found" not in stage_error


def test_run_plan_reports_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with pytest.raises(ValueError, match="run not found: missing"):
        asyncio.run(run_plan("missing", settings, repo_path=tmp_path, runner=FakeRunner()))


def test_run_plan_requires_snapshot(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_scout_run(settings, "run-123"))
    _write_scout_report(settings, "run-123")

    with pytest.raises(FileNotFoundError, match="issue snapshot not found"):
        asyncio.run(run_plan("run-123", settings, repo_path=tmp_path, runner=FakeRunner()))


def test_run_plan_requires_scout_report(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(_insert_scout_run(settings, "run-123"))
    _write_snapshot(settings, "run-123")

    with pytest.raises(FileNotFoundError, match="scout report not found"):
        asyncio.run(run_plan("run-123", settings, repo_path=tmp_path, runner=FakeRunner()))


def test_normalize_implementation_plan_dedupes_files_and_defaults() -> None:
    plan = normalize_implementation_plan(
        {
            "approach_summary": "Edit plan.",
            "steps": [
                {
                    "description": "Edit plan",
                    "files_to_modify": ["pawchestrator/plan.py", "pawchestrator/plan.py"],
                }
            ],
            "estimated_risk": "unknown",
        }
    )

    assert plan["schema"] == "pawchestrator.implementation_plan.v1"
    assert plan["approach_summary"] == "Edit plan."
    assert plan["steps"][0]["order"] == 1
    assert plan["files_to_modify"] == ["pawchestrator/plan.py"]
    assert plan["estimated_risk"] == "medium"


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ({}, "approach_summary"),
        ({"approach_summary": "Do it."}, "steps"),
        (
            {
                "approach_summary": "Do it.",
                "steps": [{"description": "Edit", "files_to_modify": []}],
            },
            "files_to_modify",
        ),
    ],
)
def test_normalize_implementation_plan_rejects_missing_required_content(
    artifact: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_implementation_plan(artifact)


def test_run_plan_command_prints_human_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    async def fake_run_plan(run_id: str, settings: Settings) -> Any:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path

        class Result:
            plan = {
                "approach_summary": "Use existing scout shape.",
                "steps": [
                    {"order": 1, "description": "Add plan module."},
                    {"order": 2, "description": "Wire CLI."},
                ],
            }

        return Result()

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)

    result = CliRunner().invoke(cli.app, ["run", "plan", "run-123"])

    assert result.exit_code == 0
    assert "Use existing scout shape." in result.output
    assert "1. Add plan module." in result.output
    assert '"approach_summary"' not in result.output


def test_run_plan_command_reports_missing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    result = CliRunner().invoke(cli.app, ["run", "plan", "missing"])

    assert result.exit_code == 1
    assert "Plan failed: run not found: missing" in result.output


async def _insert_scout_run(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'scout_complete', 'scout',
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
              'stage-123', ?, 'scout', 'complete',
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
                "title": "Add plan",
                "body": "Issue body",
                "comments": [],
            }
        ),
        encoding="utf-8",
    )


def _write_scout_report(settings: Settings, run_id: str) -> None:
    path = settings.app_dir / "runs" / run_id / "scout_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.scout_report.v1",
                "readiness": "ready",
                "findings": [{"kind": "scope", "text": "Small change"}],
            }
        ),
        encoding="utf-8",
    )
