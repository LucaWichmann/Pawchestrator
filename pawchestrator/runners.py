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
from time import monotonic
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
class RunnerFailedError(Exception):
    public_message: str
    exit_code: int
    stderr: str
    stdout: str

    def __str__(self) -> str:
        return self.public_message


@dataclass(frozen=True)
class RunnerResult:
    exit_code: int
    stdout: str
    stderr: str
    artifact: dict[str, Any] | None
    diff: str = ""


@dataclass(frozen=True)
class RunnerHealth:
    available: bool
    version: str | None


_RUNNER_HEALTH_TTL_SECONDS = 60.0
_runner_health_cache: dict[str, tuple[float, RunnerHealth]] = {}
_runner_health_lock = asyncio.Lock()
CLAUDE_TERSE_SYSTEM_PROMPT = (
    "Suppress narrative progress updates such as 'I have now implemented X', "
    "'I will next do Y', and 'I am at a turning point'. Emit only necessary tool "
    "calls and the final structured JSON artifact requested by the prompt. Do not "
    "omit or alter the required JSON artifact; it must remain valid and parseable."
)


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
        self.stage_overrides = {} if stage_overrides is None else stage_overrides

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
        stdout, stderr, exit_code = await _run_process(
            [path, "--version"],
            cwd=Path.cwd(),
        )
        if exit_code != 0:
            message = (
                stderr.splitlines()[0]
                if stderr
                else f"{self.config.binary} --version failed"
            )
            return False, message
        version = stdout.strip().splitlines()[0] if stdout.strip() else "version unknown"
        return True, f"found at {path} ({version})"

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
            "--append-system-prompt",
            CLAUDE_TERSE_SYSTEM_PROMPT,
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
        self.stage_overrides = {} if stage_overrides is None else stage_overrides

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
                artifact=_parse_json_artifact(stdout),
                diff=diff,
            )

        cmd = _codex_command(
            codex_path,
            task=task,
            config=config,
            bypass=config.bypass_sandbox,
        )
        resume_cmd = _codex_resume_command(codex_path)
        stdout, stderr, exit_code = await _run_codex_process_with_recovery(
            cmd,
            resume_cmd=resume_cmd,
            cwd=task.cwd,
            stdin_text=task.prompt,
            debug=self.debug,
            runner_id=self.id,
            task=task,
            prompt_index=_prompt_index(cmd, "-"),
            prompt_stdin_chars=len(task.prompt),
            attempts=config.previous_response_not_found_attempts,
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
            artifact=_parse_json_artifact(stdout),
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
        resume_cmd = _codex_resume_command(binary)
        prepared_resume = await _prepare_wsl_command(
            resume_cmd,
            cwd=task.cwd,
            distro=config.wsl_distro,
            enabled=config.wsl_enabled,
            linux_cwd=linux_cwd,
        )
        if prepared_resume is None:
            return None
        wsl_resume_cmd, _ = prepared_resume
        stdout, stderr, exit_code = await _run_codex_process_with_recovery(
            wsl_cmd,
            resume_cmd=wsl_resume_cmd,
            cwd=native_cwd,
            stdin_text=task.prompt,
            debug=self.debug,
            runner_id=self.id,
            task=task,
            prompt_index=_prompt_index(wsl_cmd, "-"),
            prompt_stdin_chars=len(task.prompt),
            attempts=config.previous_response_not_found_attempts,
        )
        await _write_runner_log(task, stdout=stdout, stderr=stderr)
        diff = await _capture_git_diff(task.cwd)
        return RunnerResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            artifact=_parse_json_artifact(stdout),
            diff=diff,
        )


RUNNERS: dict[str, Runner] = {
    "claude": ClaudeRunner(),
    "codex": CodexRunner(),
}


async def get_runner_health(settings: Settings) -> dict[str, dict[str, object]]:
    """Return cached runner availability and versions for the status endpoint."""

    async with _runner_health_lock:
        now = monotonic()
        cached = {
            runner_id: health
            for runner_id, (checked_at, health) in _runner_health_cache.items()
            if now - checked_at < _RUNNER_HEALTH_TTL_SECONDS
        }
        if set(cached) == {"claude", "codex"}:
            return {
                runner_id: _runner_health_payload(cached[runner_id])
                for runner_id in ("claude", "codex")
            }

        runners: dict[str, Runner] = {
            "claude": ClaudeRunner(settings.runners.claude, debug=settings.debug),
            "codex": CodexRunner(settings.runners.codex, debug=settings.debug),
        }
        for runner_id, runner in runners.items():
            if runner_id in cached:
                continue
            available, message = await runner.check_health()
            health = RunnerHealth(
                available=available,
                version=_extract_health_version(message) if available else None,
            )
            _runner_health_cache[runner_id] = (monotonic(), health)
            cached[runner_id] = health

        return {
            runner_id: _runner_health_payload(cached[runner_id])
            for runner_id in ("claude", "codex")
        }


def clear_runner_health_cache() -> None:
    _runner_health_cache.clear()


def _runner_health_payload(health: RunnerHealth) -> dict[str, object]:
    return {"available": health.available, "version": health.version}


def _extract_health_version(message: str) -> str | None:
    open_paren = message.rfind("(")
    close_paren = message.rfind(")")
    if open_paren != -1 and close_paren > open_paren:
        return message[open_paren + 1 : close_paren]
    return None


def runner_tool_mismatch_warning(
    runner: Runner,
    *,
    stage_name: str,
    required_tools: list[str],
) -> str | None:
    """Return a warning when Claude's effective allowlist misses stage tools."""

    if not isinstance(runner, ClaudeRunner):
        return None

    config = _effective_claude_config(
        runner.config,
        runner.stage_overrides,
        stage_name,
    )
    allowed_tools = set(config.allowed_tools)
    missing_tools = [tool for tool in required_tools if tool not in allowed_tools]
    if not missing_tools:
        return None

    return (
        f"stage {stage_name} requires tools not allowed for ClaudeRunner: "
        f"{', '.join(missing_tools)}"
    )


def claude_usage_limit_exhausted(result: RunnerResult) -> bool:
    """Return true for Claude usage/session exhaustion, not generic failures."""

    if result.exit_code == 0:
        return False

    combined = f"{result.stdout}\n{result.stderr}"
    structured = _parse_json_object(result.stdout) or _extract_json_object(result.stdout)
    if structured is None:
        structured = _parse_json_object(result.stderr) or _extract_json_object(
            result.stderr
        )

    status = _nested_value(structured, "api_error_status")
    if status is None:
        status = _nested_value(structured, "status")
    is_error = _nested_value(structured, "is_error")

    has_429 = status == 429 or status == "429"
    has_error_signal = is_error is True or has_429
    return has_error_signal and _has_usage_limit_wording(combined.lower())


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
    updates: dict[str, object] = {}
    if override is not None and override.claude.model is not None:
        updates["model"] = override.claude.model
    if override is not None and override.claude.effort is not None:
        updates["effort"] = override.claude.effort
    if override is not None and override.claude.allowed_tools is not None:
        updates["allowed_tools"] = override.claude.allowed_tools
    if override is not None and override.claude.bypass_permissions is not None:
        updates["bypass_permissions"] = override.claude.bypass_permissions
    if stage_name == "criteria_dedupe" and (
        override is None or override.claude.model is None
    ):
        updates["model"] = "haiku"
    if stage_name == "grill":
        updates["allowed_tools"] = ["Read", "Glob", "Grep"]
        updates["bypass_permissions"] = False
    if not updates:
        return config
    return config.model_copy(update=updates)


def _effective_codex_config(
    config: CodexRunnerSettings,
    stage_overrides: dict[str, StageSettings],
    stage_name: str,
) -> CodexRunnerSettings:
    override = stage_overrides.get(stage_name)
    if override is None:
        if stage_name == "criteria_dedupe":
            return config.model_copy(
                update={"model": "gpt-5.4-mini", "reasoning_effort": "low"}
            )
        return config

    updates: dict[str, object] = {}
    if override.codex.model is not None:
        updates["model"] = override.codex.model
    if override.codex.reasoning_effort is not None:
        updates["reasoning_effort"] = override.codex.reasoning_effort
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
    if stage_name == "criteria_dedupe":
        if override.codex.model is None:
            updates["model"] = "gpt-5.4-mini"
        if override.codex.reasoning_effort is None:
            updates["reasoning_effort"] = "low"
    return config.model_copy(update=updates)


def _codex_command(
    codex_path: str,
    *,
    task: RunnerTask,
    config: CodexRunnerSettings,
    bypass: bool,
    cwd_arg: str | None = None,
) -> list[str]:
    # Codex has no Claude-style --append-system-prompt hook here; stage prompts carry
    # any required terse-output guidance for Codex runs.
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


def _codex_resume_command(codex_path: str) -> list[str]:
    return [
        codex_path,
        "exec",
        "resume",
        "--last",
        "-",
    ]


async def _run_codex_process_with_recovery(
    cmd: list[str],
    *,
    resume_cmd: list[str],
    cwd: Path,
    stdin_text: str,
    debug: bool,
    runner_id: str,
    task: RunnerTask,
    prompt_index: int | None,
    prompt_stdin_chars: int,
    attempts: int,
) -> tuple[str, str, int]:
    notes: list[str] = []
    stdout = ""
    stderr = ""
    exit_code = 0
    for attempt in range(1, attempts + 1):
        active_cmd = cmd if attempt == 1 else resume_cmd
        active_prompt_index = (
            _prompt_index(active_cmd, "-") if prompt_index is not None else None
        )
        stdout, stderr, exit_code = await _run_process(
            active_cmd,
            cwd=cwd,
            stdin_text=stdin_text,
            debug=debug,
            runner_id=runner_id,
            task=task,
            prompt_index=active_prompt_index,
            prompt_stdin_chars=prompt_stdin_chars,
        )
        if not _looks_like_previous_response_not_found(stdout, stderr, exit_code):
            return stdout, _append_codex_retry_notes(stderr, notes), exit_code
        if attempt == attempts:
            notes.append(
                "pawchestrator: exhausted Codex previous_response_not_found "
                f"recovery after {attempts} total attempts."
            )
            return stdout, _append_codex_retry_notes(stderr, notes), exit_code
        notes.append(
            "pawchestrator: Codex previous_response_not_found on attempt "
            f"{attempt}/{attempts}; retrying with `codex exec resume --last -`."
        )
    return stdout, _append_codex_retry_notes(stderr, notes), exit_code


def _append_codex_retry_notes(stderr: str, notes: list[str]) -> str:
    if not notes:
        return stderr
    note_text = "\n".join(notes)
    if stderr.strip():
        return f"{stderr.rstrip()}\n{note_text}\n"
    return f"{note_text}\n"


def _looks_like_previous_response_not_found(
    stdout: str,
    stderr: str,
    exit_code: int,
) -> bool:
    if exit_code == 0:
        return False
    combined = f"{stdout}\n{stderr}"
    return (
        "previous_response_not_found" in combined
        and "previous_response_id" in combined
    )


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


def _nested_value(value: object, key: str) -> object:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _nested_value(child, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _nested_value(child, key)
            if found is not None:
                return found
    return None


def _has_usage_limit_wording(text: str) -> bool:
    exhaustion_terms = ("exhaust", "exceeded", "reached", "limit reached", "too many")
    if "usage" in text and "limit" in text:
        return any(term in text for term in exhaustion_terms)
    if "session" in text and "limit" in text:
        return any(term in text for term in exhaustion_terms)
    return False


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
