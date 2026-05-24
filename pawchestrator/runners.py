"""Local agent runner interfaces and implementations."""

from __future__ import annotations

import asyncio
import json
import shutil
import shlex
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import (
    ClaudeRunnerSettings,
    CodexRunnerSettings,
    Settings,
    StageSettings,
)


@dataclass(frozen=True)
class RunnerTask:
    prompt: str
    cwd: Path
    run_id: str
    stage_name: str


@dataclass(frozen=True)
class RunnerResult:
    exit_code: int
    stdout: str
    stderr: str
    artifact: dict[str, Any] | None
    diff: str = ""


class Runner(ABC):
    id: str
    kind: str

    @abstractmethod
    async def check_health(self) -> tuple[bool, str]:
        """Return whether this runner is available and a human-readable status."""

    @abstractmethod
    async def run_task(self, task: RunnerTask) -> RunnerResult:
        """Run a task and return captured output plus parsed structured artifact."""


class ClaudeRunner(Runner):
    id = "claude"
    kind = "agent"

    def __init__(
        self,
        config: ClaudeRunnerSettings | None = None,
        *,
        debug: bool = False,
        stage_overrides: dict[str, StageSettings] | None = None,
    ) -> None:
        self.config = config or ClaudeRunnerSettings()
        self.debug = debug
        self.stage_overrides = stage_overrides or {}

    async def check_health(self) -> tuple[bool, str]:
        if self.config.execution == "wsl":
            return await _check_wsl_binary(
                self.config.wsl_binary or self.config.binary,
                distro=self.config.wsl_distro,
                enabled=self.config.wsl_enabled,
                label=self.config.binary,
            )
        path = shutil.which(self.config.binary)
        if path is None:
            return False, f"{self.config.binary} binary not found on PATH"
        return True, f"found at {path}"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        config = _effective_claude_config(self.config, self.stage_overrides, task.stage_name)
        binary = config.wsl_binary or config.binary
        cmd = [
            binary,
            "-p",
            task.prompt,
            "--model",
            config.model,
            "--effort",
            config.effort,
            "--output-format",
            "json",
            "--allowedTools",
            ",".join(config.allowed_tools),
        ]
        if config.bypass_permissions:
            cmd.append("--dangerously-skip-permissions")
        cwd = task.cwd
        if config.execution == "wsl":
            prepared = await _prepare_wsl_command(
                cmd,
                cwd=task.cwd,
                distro=config.wsl_distro,
                enabled=config.wsl_enabled,
            )
            if prepared is None:
                return RunnerResult(
                    exit_code=127,
                    stdout="",
                    stderr="WSL is not available",
                    artifact=None,
                )
            cmd, cwd = prepared
        prompt_index = _prompt_index(cmd, task.prompt)
        _debug_print_command(
            enabled=self.debug,
            runner_id=self.id,
            task=task,
            cmd=cmd,
            prompt_index=prompt_index,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        _debug_print_result(
            enabled=self.debug,
            runner_id=self.id,
            task=task,
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )
        return RunnerResult(
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            artifact=_parse_json_artifact(stdout),
        )


class CodexRunner(Runner):
    id = "codex"
    kind = "agent"

    def __init__(
        self,
        config: CodexRunnerSettings | None = None,
        *,
        debug: bool = False,
        stage_overrides: dict[str, StageSettings] | None = None,
    ) -> None:
        self.config = config or CodexRunnerSettings()
        self.debug = debug
        self.stage_overrides = stage_overrides or {}

    async def check_health(self) -> tuple[bool, str]:
        if self.config.execution == "wsl":
            return await _check_wsl_binary(
                self.config.wsl_binary or self.config.binary,
                distro=self.config.wsl_distro,
                enabled=self.config.wsl_enabled,
                label=self.config.binary,
            )

        path = _resolve_binary(self.config.binary)
        if path is None:
            if self.config.execution == "auto":
                return await _check_wsl_binary(
                    self.config.wsl_binary or self.config.binary,
                    distro=self.config.wsl_distro,
                    enabled=self.config.wsl_enabled,
                    label=self.config.binary,
                )
            return False, f"{self.config.binary} not found"

        try:
            proc = await asyncio.create_subprocess_exec(
                path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return False, "codex binary not found on PATH"

        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            version = stdout.splitlines()[0] if stdout else "version unknown"
            return True, f"found at {path} ({version})"

        message = stderr.splitlines()[0] if stderr else "codex --version failed"
        return False, message

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        config = _effective_codex_config(self.config, self.stage_overrides, task.stage_name)
        codex_path = _resolve_binary(self.config.binary)
        if config.execution == "wsl":
            unavailable_reasons: list[str] = []
            wsl_result = await self._run_task_wsl(
                task,
                config,
                unavailable_reasons=unavailable_reasons,
            )
            if wsl_result is not None:
                return wsl_result
            return await _codex_wsl_unavailable_result(
                task,
                config,
                unavailable_reasons[0] if unavailable_reasons else None,
            )

        if codex_path is None and config.execution == "auto":
            wsl_result = await self._run_task_wsl(task, config)
            if wsl_result is not None:
                return wsl_result

        if codex_path is None:
            stdout = ""
            stderr = f"{self.config.binary} binary not found on PATH"
            exit_code = 127
            await _write_runner_log(task, stdout=stdout, stderr=stderr)
            diff = await _capture_git_diff(task.cwd)
            return RunnerResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                artifact=None,
                diff=diff,
            )

        cmd = _codex_command(
            codex_path,
            task=task,
            config=config,
            bypass=config.bypass_sandbox,
        )
        stdout, stderr, exit_code = await _run_process(
            cmd,
            cwd=task.cwd,
            stdin_text=task.prompt,
            debug=self.debug,
            runner_id=self.id,
            task=task,
            prompt_index=_prompt_index(cmd, "-"),
            prompt_stdin_chars=len(task.prompt),
        )
        diff = await _capture_git_diff(task.cwd)
        if (
            not config.bypass_sandbox
            and config.execution == "auto"
            and _looks_like_windows_sandbox_error(stdout, stderr)
            and (exit_code != 0 or not diff.strip())
        ):
            unavailable_reasons: list[str] = []
            wsl_result = await self._run_task_wsl(
                task,
                config,
                unavailable_reasons=unavailable_reasons,
            )
            if wsl_result is not None:
                return wsl_result
            stderr = _append_wsl_fallback_unavailable(
                stderr,
                config,
                unavailable_reasons[0] if unavailable_reasons else None,
            )

        await _write_runner_log(task, stdout=stdout, stderr=stderr)
        return RunnerResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            artifact=None,
            diff=diff,
        )

    async def _run_task_wsl(
        self,
        task: RunnerTask,
        config: CodexRunnerSettings,
        *,
        unavailable_reasons: list[str] | None = None,
    ) -> RunnerResult | None:
        if not config.wsl_enabled:
            _record_wsl_unavailable(unavailable_reasons, "WSL execution disabled")
            return None
        if not sys.platform.startswith("win"):
            _record_wsl_unavailable(
                unavailable_reasons,
                "WSL execution is only supported on Windows",
            )
            return None
        if (_resolve_binary("wsl.exe") or _resolve_binary("wsl")) is None:
            _record_wsl_unavailable(unavailable_reasons, "wsl.exe not found")
            return None
        binary = config.wsl_binary or config.binary
        linux_cwd = await _wslpath(task.cwd, distro=config.wsl_distro)
        if linux_cwd is None:
            _record_wsl_unavailable(
                unavailable_reasons,
                f"could not convert worktree path for WSL: {task.cwd}",
            )
            return None
        healthy, message = await _check_wsl_binary(
            binary,
            distro=config.wsl_distro,
            enabled=config.wsl_enabled,
            label=config.binary,
        )
        if not healthy:
            _record_wsl_unavailable(unavailable_reasons, message)
            return None
        cmd = _codex_command(
            binary,
            task=task,
            config=config,
            bypass=config.bypass_sandbox,
            cwd_arg=linux_cwd,
        )
        prepared = await _prepare_wsl_command(
            cmd,
            cwd=task.cwd,
            distro=config.wsl_distro,
            enabled=config.wsl_enabled,
            linux_cwd=linux_cwd,
        )
        if prepared is None:
            return None
        wsl_cmd, native_cwd = prepared
        stdout, stderr, exit_code = await _run_process(
            wsl_cmd,
            cwd=native_cwd,
            stdin_text=task.prompt,
            debug=self.debug,
            runner_id=self.id,
            task=task,
            prompt_index=_prompt_index(wsl_cmd, "-"),
            prompt_stdin_chars=len(task.prompt),
        )
        await _write_runner_log(task, stdout=stdout, stderr=stderr)
        diff = await _capture_git_diff(task.cwd)
        return RunnerResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            artifact=None,
            diff=diff,
        )


RUNNERS: dict[str, Runner] = {
    "claude": ClaudeRunner(),
    "codex": CodexRunner(),
}


def resolve_runner(settings: Settings, stage_name: str, default: str) -> Runner:
    """Resolve the configured runner for a stage."""

    stage_settings = settings.stages.get(stage_name)
    runner_id = stage_settings.runner if stage_settings is not None else None
    runner_id = runner_id or default

    if runner_id == "claude":
        return ClaudeRunner(
            settings.runners.claude,
            debug=settings.debug,
            stage_overrides=settings.stages,
        )
    if runner_id == "codex":
        return CodexRunner(
            settings.runners.codex,
            debug=settings.debug,
            stage_overrides=settings.stages,
        )
    raise ValueError(f"unknown runner: {runner_id}")


async def _run_process(
    cmd: list[str],
    cwd: Path,
    *,
    stdin_text: str | None = None,
    debug: bool = False,
    runner_id: str | None = None,
    task: RunnerTask | None = None,
    prompt_index: int | None = None,
    prompt_stdin_chars: int | None = None,
) -> tuple[str, str, int]:
    if debug and runner_id and task:
        _debug_print_command(
            enabled=True,
            runner_id=runner_id,
            task=task,
            cmd=cmd,
            prompt_index=prompt_index,
            prompt_stdin_chars=prompt_stdin_chars,
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        return "", str(error), 127

    if debug and runner_id and task and hasattr(proc, "stdout") and hasattr(proc, "stderr"):
        stdout_bytes, stderr_bytes = await _communicate_with_debug_streaming(
            proc,
            stdin_text=stdin_text,
        )
    elif stdin_text is None:
        stdout_bytes, stderr_bytes = await proc.communicate()
    else:
        stdout_bytes, stderr_bytes = await proc.communicate(
            stdin_text.encode("utf-8")
        )
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    exit_code = proc.returncode or 0
    if debug and runner_id and task:
        _debug_print_result(
            enabled=True,
            runner_id=runner_id,
            task=task,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    return stdout, stderr, exit_code


async def _communicate_with_debug_streaming(
    proc: asyncio.subprocess.Process,
    *,
    stdin_text: str | None,
) -> tuple[bytes, bytes]:
    async def write_stdin() -> None:
        if stdin_text is None or proc.stdin is None:
            return
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        if hasattr(proc.stdin, "wait_closed"):
            await proc.stdin.wait_closed()

    async def read_stream(
        stream: asyncio.StreamReader | None,
        label: str,
    ) -> bytes:
        if stream is None:
            return b""
        chunks: list[bytes] = []
        while chunk := await stream.read(4096):
            chunks.append(chunk)
            text = chunk.decode("utf-8", errors="replace")
            print(f"[pawchestrator:debug] {label}:\n{text}", end="", flush=True)
            if not text.endswith("\n"):
                print(flush=True)
        return b"".join(chunks)

    _, stdout_bytes, stderr_bytes = await asyncio.gather(
        write_stdin(),
        read_stream(proc.stdout, "stdout"),
        read_stream(proc.stderr, "stderr"),
    )
    await proc.wait()
    return stdout_bytes, stderr_bytes


def _resolve_binary(name: str) -> str | None:
    return shutil.which(name)


def _effective_claude_config(
    config: ClaudeRunnerSettings,
    stage_overrides: dict[str, StageSettings],
    stage_name: str,
) -> ClaudeRunnerSettings:
    override = stage_overrides.get(stage_name)
    if override is None:
        return config

    updates: dict[str, object] = {}
    if override.claude.allowed_tools is not None:
        updates["allowed_tools"] = override.claude.allowed_tools
    if override.claude.bypass_permissions is not None:
        updates["bypass_permissions"] = override.claude.bypass_permissions
    return config.model_copy(update=updates)


def _effective_codex_config(
    config: CodexRunnerSettings,
    stage_overrides: dict[str, StageSettings],
    stage_name: str,
) -> CodexRunnerSettings:
    override = stage_overrides.get(stage_name)
    if override is None:
        return config

    updates: dict[str, object] = {}
    if override.codex.execution is not None:
        updates["execution"] = override.codex.execution
    if override.codex.wsl_enabled is not None:
        updates["wsl_enabled"] = override.codex.wsl_enabled
    if override.codex.wsl_distro is not None:
        updates["wsl_distro"] = override.codex.wsl_distro
    if override.codex.wsl_binary is not None:
        updates["wsl_binary"] = override.codex.wsl_binary
    if override.codex.sandbox is not None:
        updates["sandbox"] = override.codex.sandbox
    if override.codex.approval_policy is not None:
        updates["approval_policy"] = override.codex.approval_policy
    if override.codex.bypass_sandbox is not None:
        updates["bypass_sandbox"] = override.codex.bypass_sandbox
    return config.model_copy(update=updates)


def _codex_command(
    codex_path: str,
    *,
    task: RunnerTask,
    config: CodexRunnerSettings,
    bypass: bool,
    cwd_arg: str | None = None,
) -> list[str]:
    cmd = [
        codex_path,
        "exec",
        "-C",
        cwd_arg or str(task.cwd),
    ]
    if bypass:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        cmd.extend(["-s", config.sandbox])
    cmd.extend(
        [
            "--model",
            config.model,
            "-c",
            f'model_reasoning_effort="{config.reasoning_effort}"',
            "-c",
            f'approval_policy="{config.approval_policy}"',
            "-",
        ]
    )
    return cmd


async def _prepare_wsl_command(
    cmd: list[str],
    *,
    cwd: Path,
    distro: str | None,
    enabled: bool,
    linux_cwd: str | None = None,
) -> tuple[list[str], Path] | None:
    if not enabled or not sys.platform.startswith("win"):
        return None
    wsl_path = _resolve_binary("wsl.exe") or _resolve_binary("wsl")
    if wsl_path is None:
        return None
    linux_cwd = linux_cwd or await _wslpath(cwd, distro=distro)
    if linux_cwd is None:
        return None
    wsl_cmd = [wsl_path]
    if distro:
        wsl_cmd.extend(["-d", distro])
    wsl_cmd.extend(["--cd", linux_cwd, "--exec", *cmd])
    return wsl_cmd, cwd


async def _wslpath(path: Path, *, distro: str | None) -> str | None:
    wsl_path = _resolve_binary("wsl.exe") or _resolve_binary("wsl")
    if wsl_path is None:
        return None
    cmd = [wsl_path]
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["--exec", "wslpath", "-a", str(path)])
    stdout, _, exit_code = await _run_process(cmd, cwd=path)
    if exit_code != 0:
        return None
    converted = stdout.strip().replace("\x00", "")
    return converted or None


async def _check_wsl_binary(
    binary: str,
    *,
    distro: str | None,
    enabled: bool,
    label: str,
) -> tuple[bool, str]:
    if not enabled:
        return False, "WSL execution disabled"
    if not sys.platform.startswith("win"):
        return False, "WSL execution is only supported on Windows"
    wsl_path = _resolve_binary("wsl.exe") or _resolve_binary("wsl")
    if wsl_path is None:
        return False, "wsl.exe not found"
    cmd = [wsl_path]
    if distro:
        cmd.extend(["-d", distro])
    quoted_binary = shlex.quote(binary)
    probe = (
        f"resolved=$(command -v {quoted_binary}) || exit 127; "
        'printf "%s\\n" "$resolved"; '
        f"{quoted_binary} --version"
    )
    cmd.extend(["--exec", "sh", "-lc", probe])
    stdout, stderr, exit_code = await _run_process(cmd, cwd=Path.cwd())
    if exit_code != 0:
        detail = _wsl_binary_unavailable_message(binary, stdout, stderr)
        return False, detail
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    resolved = lines[0] if lines else binary
    version = f" ({lines[1]})" if len(lines) > 1 else ""
    prefix = f"found in WSL distro {distro}" if distro else "found in WSL"
    return True, f"{prefix}: {label} at {resolved}{version}"


async def _codex_wsl_unavailable_result(
    task: RunnerTask,
    config: CodexRunnerSettings,
    reason: str | None = None,
) -> RunnerResult:
    stderr = _wsl_fallback_unavailable_message(config, reason)
    await _write_runner_log(task, stdout="", stderr=stderr)
    diff = await _capture_git_diff(task.cwd)
    return RunnerResult(
        exit_code=127,
        stdout="",
        stderr=stderr,
        artifact=None,
        diff=diff,
    )


def _append_wsl_fallback_unavailable(
    stderr: str,
    config: CodexRunnerSettings,
    reason: str | None = None,
) -> str:
    message = _wsl_fallback_unavailable_message(config, reason)
    if stderr.strip():
        return f"{stderr.rstrip()}\n{message}\n"
    return f"{message}\n"


def _wsl_fallback_unavailable_message(
    config: CodexRunnerSettings,
    reason: str | None = None,
) -> str:
    binary = config.wsl_binary or config.binary
    reason_text = f": {reason}" if reason else ""
    return (
        "WSL Codex fallback unavailable: "
        f"{binary} is not installed or runnable inside WSL{reason_text}. "
        'Install it with `wsl --exec sh -lc "npm install -g '
        '@openai/codex@latest && codex --version"` or set '
        "`[runners.codex] wsl_enabled = false`."
    )


def _wsl_binary_unavailable_message(binary: str, stdout: str, stderr: str) -> str:
    detail = (stderr.strip() or stdout.strip()).replace("\x00", "")
    if detail:
        first_line = detail.splitlines()[0]
        return f"{binary} is not runnable in WSL: {first_line}"
    return f"{binary} not found in WSL"


def _record_wsl_unavailable(reasons: list[str] | None, reason: str) -> None:
    if reasons is not None:
        reasons.append(reason)


def _looks_like_windows_sandbox_error(stdout: str, stderr: str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    combined = f"{stdout}\n{stderr}"
    return any(
        marker in combined
        for marker in (
            "CreateProcessWithLogonW failed: 1326",
            "windows sandbox:",
            "spawn setup refresh",
        )
    )


def _debug_print_command(
    *,
    enabled: bool,
    runner_id: str,
    task: RunnerTask,
    cmd: list[str],
    prompt_index: int | None,
    prompt_stdin_chars: int | None = None,
) -> None:
    if not enabled:
        return

    rendered_cmd = list(cmd)
    if prompt_index is not None and 0 <= prompt_index < len(rendered_cmd):
        if prompt_stdin_chars is None:
            rendered_cmd[prompt_index] = f"<prompt chars={len(rendered_cmd[prompt_index])}>"
        else:
            rendered_cmd[prompt_index] = f"<prompt stdin chars={prompt_stdin_chars}>"

    print(
        f"[pawchestrator:debug] run={task.run_id} stage={task.stage_name} "
        f"runner={runner_id} cwd={task.cwd}",
        flush=True,
    )
    print(
        f"[pawchestrator:debug] argv={shlex.join(rendered_cmd)}",
        flush=True,
    )


def _prompt_index(cmd: list[str], prompt: str) -> int | None:
    try:
        return cmd.index(prompt)
    except ValueError:
        return None


def _debug_print_result(
    *,
    enabled: bool,
    runner_id: str,
    task: RunnerTask,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    if not enabled:
        return

    print(
        f"[pawchestrator:debug] run={task.run_id} stage={task.stage_name} "
        f"runner={runner_id} exit_code={exit_code}",
        flush=True,
    )
    if stdout:
        print(f"[pawchestrator:debug] stdout:\n{stdout}", flush=True)
    if stderr:
        print(f"[pawchestrator:debug] stderr:\n{stderr}", flush=True)


async def _write_runner_log(task: RunnerTask, stdout: str, stderr: str) -> None:
    log_path = task.cwd / "runs" / task.run_id / "stdout" / "implement.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"{stdout}{stderr}", encoding="utf-8")


async def _capture_git_diff(cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        "HEAD",
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, _ = await proc.communicate()
    return stdout_bytes.decode("utf-8", errors="replace")


def _parse_json_artifact(stdout: str) -> dict[str, Any] | None:
    stripped = stdout.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return _extract_json_object(stripped)

    if not isinstance(parsed, dict):
        return None

    result = parsed.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        result_parsed = _parse_json_object(result)
        if result_parsed is not None:
            return result_parsed
        extracted = _extract_json_object(result)
        if extracted is not None:
            return extracted
        return parsed

    return parsed


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = _extract_fenced_json_object(text)
    if fenced is not None:
        return fenced

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_fenced_json_object(text: str) -> dict[str, Any] | None:
    fence = "```"
    start = 0
    while True:
        open_index = text.find(fence, start)
        if open_index == -1:
            return None
        content_start = open_index + len(fence)
        first_newline = text.find("\n", content_start)
        if first_newline == -1:
            return None
        language = text[content_start:first_newline].strip().lower()
        close_index = text.find(fence, first_newline + 1)
        if close_index == -1:
            return None
        if language in {"json", ""}:
            parsed = _parse_json_object(text[first_newline + 1 : close_index])
            if parsed is not None:
                return parsed
        start = close_index + len(fence)
