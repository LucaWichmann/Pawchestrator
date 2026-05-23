import asyncio
import json
from pathlib import Path

import pytest

from pawchestrator.runners import RUNNERS, ClaudeRunner, CodexRunner, RunnerTask


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


def test_codex_runner_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)

    healthy, message = asyncio.run(CodexRunner().check_health())

    assert healthy is False
    assert message == "codex not found"


def test_codex_runner_is_registered() -> None:
    assert isinstance(RUNNERS["codex"], CodexRunner)


def test_codex_runner_invokes_expected_command_logs_and_captures_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(
            {
                "cmd": list(cmd),
                "cwd": kwargs["cwd"],
                "stdout": kwargs["stdout"],
                "stderr": kwargs["stderr"],
            }
        )
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"diff --git a/file.py b/file.py\n", b"")
        return FakeProcess(0, b"codex stdout\n", b"codex stderr\n")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-123",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0]["cmd"] == [
        "codex",
        "exec",
        "implement issue",
        "-C",
        str(tmp_path),
        "-s",
        "workspace-write",
    ]
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[1]["cmd"] == ["git", "diff", "HEAD"]
    assert calls[1]["cwd"] == str(tmp_path)
    assert result.exit_code == 0
    assert result.stdout == "codex stdout\n"
    assert result.stderr == "codex stderr\n"
    assert result.diff == "diff --git a/file.py b/file.py\n"
    assert result.artifact is None
    assert (tmp_path / "runs" / "run-123" / "stdout" / "implement.log").read_text(
        encoding="utf-8"
    ) == "codex stdout\ncodex stderr\n"


def test_codex_runner_falls_back_for_windows_sandbox_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if len(calls) == 1:
            return FakeProcess(1, b"", b"CreateProcessWithLogonW failed: 1326")
        if len(calls) == 2:
            return FakeProcess(0, b"fallback stdout", b"")
        return FakeProcess(0, b"fallback diff", b"")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0] == [
        "codex",
        "exec",
        "implement issue",
        "-C",
        str(tmp_path),
        "-s",
        "workspace-write",
    ]
    assert calls[1] == [
        "codex",
        "exec",
        "implement issue",
        "-C",
        str(tmp_path),
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert result.exit_code == 0
    assert result.stdout == "fallback stdout"
