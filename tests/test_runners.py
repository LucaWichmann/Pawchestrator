import asyncio
import json
from pathlib import Path

import pytest

from pawchestrator.runners import RUNNERS, ClaudeRunner, CodexRunner, Runner, RunnerTask


def test_runner_registry_contains_both_agent_runners() -> None:
    assert set(RUNNERS) == {"claude", "codex"}
    assert isinstance(RUNNERS["claude"], ClaudeRunner)
    assert isinstance(RUNNERS["codex"], CodexRunner)
    assert all(isinstance(runner, Runner) for runner in RUNNERS.values())


def test_claude_runner_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)

    healthy, message = asyncio.run(ClaudeRunner().check_health())

    assert healthy is False
    assert message == "claude binary not found on PATH"


def test_claude_runner_reports_found_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: "C:\\bin\\claude.exe" if name == "claude" else None,
    )

    healthy, message = asyncio.run(ClaudeRunner().check_health())

    assert healthy is True
    assert message == "found at C:\\bin\\claude.exe"


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


def test_codex_runner_reports_found_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    codex_path = "C:\\bin\\codex.CMD"

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"codex 1.2.3", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        assert list(cmd) == [codex_path, "--version"]
        assert kwargs["stdout"] == asyncio.subprocess.PIPE
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        return FakeProcess()

    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path if name == "codex" else None,
    )
    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    healthy, message = asyncio.run(CodexRunner().check_health())

    assert healthy is True
    assert message == f"found at {codex_path} (codex 1.2.3)"


def test_codex_runner_reports_spawn_failure_as_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: "C:\\bin\\codex.exe" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    healthy, message = asyncio.run(CodexRunner().check_health())

    assert healthy is False
    assert message == "codex binary not found on PATH"


def test_run_process_reports_missing_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    from pawchestrator.runners import _run_process

    stdout, stderr, exit_code = asyncio.run(_run_process(["missing"], tmp_path))

    assert stdout == ""
    assert "[WinError 2]" in stderr
    assert exit_code == 127


def test_codex_runner_invokes_expected_command_logs_and_captures_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
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
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path if name == "codex" else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-123",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0]["cmd"] == [
        codex_path,
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
    codex_path = "C:\\bin\\codex.CMD"
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
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path if name == "codex" else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0] == [
        codex_path,
        "exec",
        "implement issue",
        "-C",
        str(tmp_path),
        "-s",
        "workspace-write",
    ]
    assert calls[1] == [
        codex_path,
        "exec",
        "implement issue",
        "-C",
        str(tmp_path),
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert result.exit_code == 0
    assert result.stdout == "fallback stdout"


def test_codex_runner_reports_missing_binary_at_run_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"diff --git a/file.py b/file.py\n", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        return FakeProcess()

    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-missing",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls == [["git", "diff", "HEAD"]]
    assert result.exit_code == 127
    assert result.stdout == ""
    assert result.stderr == "codex binary not found on PATH"
    assert result.diff == "diff --git a/file.py b/file.py\n"
    assert result.artifact is None
    assert (tmp_path / "runs" / "run-missing" / "stdout" / "implement.log").read_text(
        encoding="utf-8"
    ) == "codex binary not found on PATH"
