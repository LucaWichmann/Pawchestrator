"""Local agent runner interfaces and implementations."""

from __future__ import annotations

import asyncio
import json
import shutil
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import ClaudeRunnerSettings, CodexRunnerSettings


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
    ) -> None:
        self.config = config or ClaudeRunnerSettings()
        self.debug = debug

    async def check_health(self) -> tuple[bool, str]:
        path = shutil.which(self.config.binary)
        if path is None:
            return False, f"{self.config.binary} binary not found on PATH"
        return True, f"found at {path}"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        cmd = [
            self.config.binary,
            "-p",
            task.prompt,
            "--model",
            self.config.model,
            "--effort",
            self.config.effort,
            "--output-format",
            "json",
            "--allowedTools",
            "Read,Bash,Glob,Grep",
            "--dangerously-skip-permissions",
        ]
        _debug_print_command(
            enabled=self.debug,
            runner_id=self.id,
            task=task,
            cmd=cmd,
            prompt_index=2,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(task.cwd),
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
    ) -> None:
        self.config = config or CodexRunnerSettings()
        self.debug = debug

    async def check_health(self) -> tuple[bool, str]:
        path = _resolve_binary(self.config.binary)
        if path is None:
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
        codex_path = _resolve_binary(self.config.binary)
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

        cmd = [
            codex_path,
            "exec",
            task.prompt,
            "-C",
            str(task.cwd),
            "-s",
            "workspace-write",
            "--model",
            self.config.model,
            "-c",
            f'model_reasoning_effort="{self.config.reasoning_effort}"',
        ]
        stdout, stderr, exit_code = await _run_process(
            cmd,
            cwd=task.cwd,
            debug=self.debug,
            runner_id=self.id,
            task=task,
            prompt_index=2,
        )
        if exit_code != 0 and "CreateProcessWithLogonW failed: 1326" in stderr:
            fallback_cmd = [
                codex_path,
                "exec",
                task.prompt,
                "-C",
                str(task.cwd),
                "--dangerously-bypass-approvals-and-sandbox",
                "--model",
                self.config.model,
                "-c",
                f'model_reasoning_effort="{self.config.reasoning_effort}"',
            ]
            stdout, stderr, exit_code = await _run_process(
                fallback_cmd,
                cwd=task.cwd,
                debug=self.debug,
                runner_id=self.id,
                task=task,
                prompt_index=2,
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


async def _run_process(
    cmd: list[str],
    cwd: Path,
    *,
    debug: bool = False,
    runner_id: str | None = None,
    task: RunnerTask | None = None,
    prompt_index: int | None = None,
) -> tuple[str, str, int]:
    if debug and runner_id and task:
        _debug_print_command(
            enabled=True,
            runner_id=runner_id,
            task=task,
            cmd=cmd,
            prompt_index=prompt_index,
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        return "", str(error), 127

    stdout_bytes, stderr_bytes = await proc.communicate()
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


def _resolve_binary(name: str) -> str | None:
    return shutil.which(name)


def _debug_print_command(
    *,
    enabled: bool,
    runner_id: str,
    task: RunnerTask,
    cmd: list[str],
    prompt_index: int | None,
) -> None:
    if not enabled:
        return

    rendered_cmd = list(cmd)
    if prompt_index is not None and 0 <= prompt_index < len(rendered_cmd):
        rendered_cmd[prompt_index] = f"<prompt chars={len(rendered_cmd[prompt_index])}>"

    print(
        f"[pawchestrator:debug] run={task.run_id} stage={task.stage_name} "
        f"runner={runner_id} cwd={task.cwd}",
        flush=True,
    )
    print(
        f"[pawchestrator:debug] argv={shlex.join(rendered_cmd)}",
        flush=True,
    )


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
        return None

    if not isinstance(parsed, dict):
        return None

    result = parsed.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            result_parsed = json.loads(result)
        except json.JSONDecodeError:
            return parsed
        if isinstance(result_parsed, dict):
            return result_parsed

    return parsed
