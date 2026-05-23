import asyncio
import json
from pathlib import Path

import pytest

from pawchestrator.runners import ClaudeRunner, RunnerTask


def test_claude_runner_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)

    healthy, message = asyncio.run(ClaudeRunner().check_health())

    assert healthy is False
    assert message == "claude binary not found on PATH"


def test_claude_runner_invokes_expected_command_and_parses_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (
                json.dumps(
                    {
                        "result": json.dumps(
                            {
                                "schema": "pawchestrator.scout_report.v1",
                                "status": "success",
                                "readiness": "ready",
                            }
                        )
                    }
                ).encode(),
                b"warning",
            )

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls["cmd"] = list(cmd)
        calls["cwd"] = kwargs["cwd"]
        calls["stdout"] = kwargs["stdout"]
        calls["stderr"] = kwargs["stderr"]
        return FakeProcess()

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    task = RunnerTask(
        prompt="repo scout prompt",
        cwd=tmp_path,
        run_id="run-123",
        stage_name="scout",
    )

    result = asyncio.run(ClaudeRunner().run_task(task))

    assert calls["cmd"] == [
        "claude",
        "-p",
        "repo scout prompt",
        "--output-format",
        "json",
        "--allowedTools",
        "Read,Bash,Glob,Grep",
        "--dangerously-skip-permissions",
    ]
    assert calls["cwd"] == str(tmp_path)
    assert result.exit_code == 0
    assert result.stderr == "warning"
    assert result.artifact == {
        "schema": "pawchestrator.scout_report.v1",
        "status": "success",
        "readiness": "ready",
    }
