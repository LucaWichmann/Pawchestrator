import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pawchestrator.config import (
    ClaudeRunnerSettings,
    CodexRunnerSettings,
    Settings,
    StageSettings,
)
from pawchestrator.runners import (
    RUNNERS,
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerTask,
    clear_runner_health_cache,
    get_runner_health,
    resolve_runner,
)


def test_runner_registry_contains_both_agent_runners() -> None:
    assert set(RUNNERS) == {"claude", "codex"}
    assert isinstance(RUNNERS["claude"], ClaudeRunner)
    assert isinstance(RUNNERS["codex"], CodexRunner)
    assert all(isinstance(runner, Runner) for runner in RUNNERS.values())


def test_resolve_runner_uses_default_without_stage_override() -> None:
    settings = Settings()

    runner = resolve_runner(settings, "implement", "codex")

    assert isinstance(runner, CodexRunner)
    assert runner.config is settings.runners.codex
    assert runner.stage_overrides is settings.stages


def test_resolve_runner_uses_claude_stage_override() -> None:
    settings = Settings(
        stages={
            "implement": StageSettings(
                runner="claude",
                claude={"allowed_tools": ["Read"], "bypass_permissions": True},
            )
        }
    )

    runner = resolve_runner(settings, "implement", "codex")

    assert isinstance(runner, ClaudeRunner)
    assert runner.config is settings.runners.claude
    assert runner.stage_overrides is settings.stages
    assert runner.stage_overrides["implement"].claude.allowed_tools == ["Read"]
    assert runner.stage_overrides["implement"].claude.bypass_permissions is True


def test_resolve_runner_uses_codex_stage_override() -> None:
    settings = Settings(
        stages={
            "implement": StageSettings(
                runner="codex",
                codex={"sandbox": "danger-full-access", "approval_policy": "never"},
            )
        }
    )

    runner = resolve_runner(settings, "implement", "claude")

    assert isinstance(runner, CodexRunner)
    assert runner.config is settings.runners.codex
    assert runner.stage_overrides is settings.stages
    assert runner.stage_overrides["implement"].codex.sandbox == "danger-full-access"
    assert runner.stage_overrides["implement"].codex.approval_policy == "never"


def test_stage_settings_rejects_invalid_runner_value() -> None:
    with pytest.raises(ValidationError) as error:
        StageSettings(runner="unknown")

    assert "runner" in str(error.value)
    assert "claude" in str(error.value)
    assert "codex" in str(error.value)


def test_claude_runner_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)

    healthy, message = asyncio.run(ClaudeRunner().check_health())

    assert healthy is False
    assert message == "claude binary not found on PATH"


def test_claude_runner_reports_found_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b"claude 1.2.3", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        assert list(cmd) == ["C:\\bin\\claude.exe", "--version"]
        return FakeProcess()

    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: "C:\\bin\\claude.exe" if name == "claude" else None,
    )
    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    healthy, message = asyncio.run(ClaudeRunner().check_health())

    assert healthy is True
    assert message == "found at C:\\bin\\claude.exe (claude 1.2.3)"


def test_runner_health_cache_reuses_version_checks_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_runner_health_cache()
    calls: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        def __init__(self, stdout: bytes) -> None:
            self._stdout = stdout

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[0] == "C:\\bin\\claude.exe":
            return FakeProcess(b"claude 1.2.3\n")
        if cmd[0] == "C:\\bin\\codex.exe":
            return FakeProcess(b"codex 4.5.6\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: f"C:\\bin\\{name}.exe" if name in {"claude", "codex"} else None,
    )
    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    settings = Settings()
    settings.runners.codex.execution = "native"

    first = asyncio.run(get_runner_health(settings))
    second = asyncio.run(get_runner_health(settings))

    assert first == {
        "claude": {"available": True, "version": "claude 1.2.3"},
        "codex": {"available": True, "version": "codex 4.5.6"},
    }
    assert second == first
    assert calls == [
        ["C:\\bin\\claude.exe", "--version"],
        ["C:\\bin\\codex.exe", "--version"],
    ]
    clear_runner_health_cache()


def test_claude_runner_invokes_expected_command_and_parses_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
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

    result = asyncio.run(
        ClaudeRunner(
            ClaudeRunnerSettings(binary="claude-beta", model="sonnet", effort="medium")
        ).run_task(task)
    )

    assert calls["cmd"] == [
        "claude-beta",
        "-p",
        "repo scout prompt",
        "--model",
        "sonnet",
        "--effort",
        "medium",
        "--output-format",
        "json",
        "--allowedTools",
        "Read,Glob,Grep",
    ]
    assert calls["cwd"] == str(tmp_path)
    assert result.exit_code == 0
    assert result.stderr == "warning"
    assert result.artifact == {
        "schema": "pawchestrator.scout_report.v1",
        "status": "success",
        "readiness": "ready",
    }


def test_claude_runner_parses_fenced_json_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return (
                json.dumps(
                    {
                        "result": (
                            "Done.\n\n```json\n"
                            '{"schema":"pawchestrator.scout_report.v1",'
                            '"findings":[{"kind":"scope","text":"Small"}]}'
                            "\n```"
                        )
                    }
                ).encode(),
                b"",
            )

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
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

    assert result.artifact == {
        "schema": "pawchestrator.scout_report.v1",
        "findings": [{"kind": "scope", "text": "Small"}],
    }


def test_claude_runner_parses_direct_fenced_json_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return (
                b'Done.\n```json\n{"schema":"pawchestrator.scout_report.v1"}\n```',
                b"",
            )

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
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

    assert result.artifact == {"schema": "pawchestrator.scout_report.v1"}


def test_claude_runner_uses_stage_permission_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b'{"result": {"status": "success"}}', b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls["cmd"] = list(cmd)
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

    asyncio.run(
        ClaudeRunner(
            stage_overrides={
                "scout": StageSettings(
                    claude={
                        "allowed_tools": ["Read"],
                        "bypass_permissions": True,
                    }
                )
            }
        ).run_task(task)
    )

    assert "--allowedTools" in calls["cmd"]
    assert calls["cmd"][calls["cmd"].index("--allowedTools") + 1] == "Read"
    assert "--dangerously-skip-permissions" in calls["cmd"]


def test_claude_runner_wsl_mode_invokes_wsl_and_preserves_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    wsl_path = "C:\\Windows\\System32\\wsl.exe"

    class FakeProcess:
        def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append({"cmd": list(cmd), "cwd": kwargs["cwd"]})
        if "wslpath" in cmd:
            return FakeProcess(b"/mnt/c/repo\n")
        return FakeProcess(b'{"result": {"status": "success"}}')

    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: wsl_path if name in {"wsl.exe", "wsl"} else None,
    )
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

    result = asyncio.run(
        ClaudeRunner(
            ClaudeRunnerSettings(
                execution="wsl",
                wsl_distro="Ubuntu",
                wsl_binary="claude-linux",
                allowed_tools=["Read"],
            )
        ).run_task(task)
    )

    assert result.exit_code == 0
    assert calls[1]["cmd"] == [
        wsl_path,
        "-d",
        "Ubuntu",
        "--cd",
        "/mnt/c/repo",
        "--exec",
        "claude-linux",
        "-p",
        "repo scout prompt",
        "--model",
        "sonnet",
        "--effort",
        "low",
        "--output-format",
        "json",
        "--allowedTools",
        "Read",
    ]


def test_claude_runner_debug_prints_command_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b'{"result": {"status": "success"}}', b"claude warning"

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    task = RunnerTask(
        prompt="debug prompt",
        cwd=tmp_path,
        run_id="run-debug",
        stage_name="scout",
    )

    asyncio.run(ClaudeRunner(debug=True).run_task(task))

    output = capsys.readouterr().out
    assert "[pawchestrator:debug] run=run-debug stage=scout runner=claude" in output
    assert "<prompt chars=12>" in output
    assert "--model sonnet --effort low" in output
    assert '{"result": {"status": "success"}}' in output
    assert "claude warning" in output


def test_codex_runner_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.runners.shutil.which", lambda name: None)

    healthy, message = asyncio.run(CodexRunner().check_health())

    assert healthy is False
    assert message == "wsl.exe not found"


def test_codex_runner_reports_found_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    codex_path = "C:\\bin\\codex.CMD"

    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
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

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            calls[-1]["stdin_input"] = input
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
        prompt="implement issue\nsecond line",
        cwd=tmp_path,
        run_id="run-123",
        stage_name="implement",
    )

    result = asyncio.run(
        CodexRunner(
            CodexRunnerSettings(
                binary="codex",
                model="gpt-5.5",
                reasoning_effort="low",
            )
        ).run_task(task)
    )

    assert calls[0]["cmd"] == [
        codex_path,
        "exec",
        "-C",
        str(tmp_path),
        "-s",
        "workspace-write",
        "--model",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'approval_policy="never"',
        "-",
    ]
    assert "implement issue\nsecond line" not in calls[0]["cmd"]
    assert calls[0]["stdin_input"] == b"implement issue\nsecond line"
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


def test_codex_runner_debug_prints_command_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    codex_path = "C:\\bin\\codex.CMD"

    class FakeProcess:
        def __init__(self, stdout: bytes, stderr: bytes) -> None:
            self.returncode = 0
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(b"", b"")
        return FakeProcess(b"codex stdout", b"codex stderr")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path if name == "codex" else None,
    )

    task = RunnerTask(
        prompt="debug prompt",
        cwd=tmp_path,
        run_id="run-debug",
        stage_name="implement",
    )

    asyncio.run(CodexRunner(debug=True).run_task(task))

    output = capsys.readouterr().out
    assert "[pawchestrator:debug] run=run-debug stage=implement runner=codex" in output
    assert "<prompt stdin chars=12>" in output
    assert "--model gpt-5.5" in output
    assert "-c 'model_reasoning_effort=\"low\"'" in output
    assert "codex stdout" in output
    assert "codex stderr" in output


def test_codex_runner_auto_retries_wsl_for_windows_sandbox_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []
    stdins: list[bytes | None] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            stdins.append(input)
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"fallback diff", b"")
        if cmd[0] == codex_path:
            return FakeProcess(1, b"", b"CreateProcessWithLogonW failed: 1326")
        if "wslpath" in cmd:
            return FakeProcess(0, b"/mnt/c/repo\n", b"")
        if cmd[0] == wsl_path and "sh" in cmd:
            return FakeProcess(0, b"/usr/local/bin/codex\ncodex-cli 0.133.0\n", b"")
        if cmd[0] == wsl_path:
            return FakeProcess(0, b"wsl stdout", b"")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path
        if name == "codex"
        else wsl_path
        if name in {"wsl.exe", "wsl"}
        else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(
        CodexRunner(
            CodexRunnerSettings(
                binary="codex",
                model="gpt-5.5-fast",
                reasoning_effort="medium",
            )
        ).run_task(task)
    )

    assert calls[0] == [
        codex_path,
        "exec",
        "-C",
        str(tmp_path),
        "-s",
        "workspace-write",
        "--model",
        "gpt-5.5-fast",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'approval_policy="never"',
        "-",
    ]
    assert calls[1] == ["git", "diff", "HEAD"]
    assert calls[2] == [wsl_path, "--exec", "wslpath", "-a", str(tmp_path)]
    assert calls[3][:5] == [wsl_path, "--exec", "sh", "-lc", calls[3][4]]
    assert "codex --version" in calls[3][4]
    assert calls[4] == [
        wsl_path,
        "--cd",
        "/mnt/c/repo",
        "--exec",
        "codex",
        "exec",
        "-C",
        "/mnt/c/repo",
        "-s",
        "workspace-write",
        "--model",
        "gpt-5.5-fast",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'approval_policy="never"',
        "-",
    ]
    assert stdins[0] == b"implement issue"
    assert stdins[4] == b"implement issue"
    assert all("--dangerously-bypass-approvals-and-sandbox" not in call for call in calls)
    assert result.exit_code == 0
    assert result.stdout == "wsl stdout"


def test_codex_runner_auto_does_not_bypass_when_wsl_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if len(calls) == 1:
            return FakeProcess(1, b"", b"windows sandbox: spawn setup refresh")
        return FakeProcess(0, b"", b"")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
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

    result = asyncio.run(
        CodexRunner(
            CodexRunnerSettings(wsl_enabled=False)
        ).run_task(task)
    )

    assert len(calls) == 2
    assert calls[0][0] == codex_path
    assert calls[1] == ["git", "diff", "HEAD"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in calls[0]
    assert result.exit_code == 1


def test_codex_runner_auto_retries_wsl_when_successful_run_only_reports_sandbox_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"", b"")
        if cmd[0] == codex_path:
            return FakeProcess(
                0,
                b"execution error: windows sandbox: spawn setup refresh\n",
                b"",
            )
        if "wslpath" in cmd:
            return FakeProcess(0, b"/mnt/c/repo\n", b"")
        if cmd[0] == wsl_path and "sh" in cmd:
            return FakeProcess(0, b"/usr/local/bin/codex\ncodex-cli 0.133.0\n", b"")
        if cmd[0] == wsl_path:
            return FakeProcess(0, b"wsl stdout", b"")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path
        if name == "codex"
        else wsl_path
        if name in {"wsl.exe", "wsl"}
        else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0][0] == codex_path
    assert calls[1] == ["git", "diff", "HEAD"]
    assert calls[2] == [wsl_path, "--exec", "wslpath", "-a", str(tmp_path)]
    assert calls[3][:5] == [wsl_path, "--exec", "sh", "-lc", calls[3][4]]
    assert "codex --version" in calls[3][4]
    assert calls[4][0] == wsl_path
    assert all("--dangerously-bypass-approvals-and-sandbox" not in call for call in calls)
    assert result.exit_code == 0
    assert result.stdout == "wsl stdout"


def test_codex_runner_auto_keeps_successful_sandbox_output_when_diff_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"diff --git a/file.py b/file.py\n", b"")
        if cmd[0] == codex_path:
            return FakeProcess(
                0,
                b"execution error: windows sandbox: spawn setup refresh\n",
                b"",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path
        if name == "codex"
        else wsl_path
        if name in {"wsl.exe", "wsl"}
        else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert len(calls) == 2
    assert calls[0][0] == codex_path
    assert calls[1] == ["git", "diff", "HEAD"]
    assert result.exit_code == 0
    assert "windows sandbox: spawn setup refresh" in result.stdout
    assert result.diff == "diff --git a/file.py b/file.py\n"


def test_codex_runner_auto_keeps_successful_sandbox_output_when_wsl_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"", b"")
        return FakeProcess(0, b"windows sandbox: spawn setup refresh\n", b"")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
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

    assert calls[0][0] == codex_path
    assert calls[1] == ["git", "diff", "HEAD"]
    assert len(calls) == 2
    assert result.exit_code == 0
    assert result.stdout == "windows sandbox: spawn setup refresh\n"
    assert result.diff == ""


def test_codex_runner_auto_keeps_native_result_when_wsl_codex_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"", b"")
        if cmd[0] == codex_path:
            return FakeProcess(0, b"windows sandbox: spawn setup refresh\n", b"")
        if "wslpath" in cmd:
            return FakeProcess(0, b"/mnt/c/repo\n", b"")
        if cmd[0] == wsl_path and "sh" in cmd:
            return FakeProcess(
                1,
                b"/mnt/c/Users/lucam/AppData/Roaming/npm/codex\n",
                b"Error: Missing optional dependency @openai/codex-linux-x64\n",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: codex_path
        if name == "codex"
        else wsl_path
        if name in {"wsl.exe", "wsl"}
        else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(CodexRunner().run_task(task))

    assert calls[0][0] == codex_path
    assert calls[1] == ["git", "diff", "HEAD"]
    assert calls[2] == [wsl_path, "--exec", "wslpath", "-a", str(tmp_path)]
    assert calls[3][:4] == [wsl_path, "--exec", "sh", "-lc"]
    assert len(calls) == 4
    assert result.exit_code == 0
    assert result.stdout == "windows sandbox: spawn setup refresh\n"
    assert "WSL Codex fallback unavailable" in result.stderr
    assert "npm install -g @openai/codex@latest" in result.stderr
    assert result.diff == ""


def test_codex_runner_explicit_wsl_reports_unavailable_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []

    class FakeProcess:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        if "wslpath" in cmd:
            return FakeProcess(0, b"/mnt/c/repo\n", b"")
        if cmd[0] == wsl_path and "sh" in cmd:
            return FakeProcess(
                1,
                b"/mnt/c/Users/lucam/AppData/Roaming/npm/codex\n",
                b"Error: Missing optional dependency @openai/codex-linux-x64\n",
            )
        if cmd[:3] == ("git", "diff", "HEAD"):
            return FakeProcess(0, b"", b"")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: wsl_path if name in {"wsl.exe", "wsl"} else None,
    )

    task = RunnerTask(
        prompt="implement issue",
        cwd=tmp_path,
        run_id="run-456",
        stage_name="implement",
    )

    result = asyncio.run(
        CodexRunner(CodexRunnerSettings(execution="wsl")).run_task(task)
    )

    assert calls[0] == [wsl_path, "--exec", "wslpath", "-a", str(tmp_path)]
    assert calls[1][:4] == [wsl_path, "--exec", "sh", "-lc"]
    assert calls[2] == ["git", "diff", "HEAD"]
    assert len(calls) == 3
    assert result.exit_code == 127
    assert result.stdout == ""
    assert "WSL Codex fallback unavailable" in result.stderr
    assert "npm install -g @openai/codex@latest" in result.stderr


def test_codex_runner_wsl_health_checks_version_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wsl_path = "C:\\Windows\\System32\\wsl.exe"
    calls: list[list[str]] = []

    class FakeProcess:
        returncode = 1

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return (
                b"/mnt/c/Users/lucam/AppData/Roaming/npm/codex\n",
                b"Error: Missing optional dependency @openai/codex-linux-x64\n",
            )

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        return FakeProcess()

    monkeypatch.setattr(
        "pawchestrator.runners.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("pawchestrator.runners.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.runners.shutil.which",
        lambda name: wsl_path if name in {"wsl.exe", "wsl"} else None,
    )

    healthy, message = asyncio.run(
        CodexRunner(CodexRunnerSettings(execution="wsl")).check_health()
    )

    assert healthy is False
    assert calls[0][:4] == [wsl_path, "--exec", "sh", "-lc"]
    assert "codex --version" in calls[0][4]
    assert "not runnable in WSL" in message


def test_codex_runner_uses_explicit_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = "C:\\bin\\codex.CMD"
    calls: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_create_subprocess_exec(*cmd, **kwargs) -> FakeProcess:
        calls.append(list(cmd))
        return FakeProcess()

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

    result = asyncio.run(
        CodexRunner(CodexRunnerSettings(bypass_sandbox=True)).run_task(task)
    )

    assert len(calls) == 2
    assert "--dangerously-bypass-approvals-and-sandbox" in calls[0]
    assert "-s" not in calls[0]
    assert result.exit_code == 0


def test_codex_runner_reports_missing_binary_at_run_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
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
