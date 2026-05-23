"""Local agent runner interfaces and implementations."""

from __future__ import annotations

import asyncio
import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    async def check_health(self) -> tuple[bool, str]:
        path = shutil.which("claude")
        if path is None:
            return False, "claude binary not found on PATH"
        return True, f"found at {path}"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        cmd = [
            "claude",
            "-p",
            task.prompt,
            "--output-format",
            "json",
            "--allowedTools",
            "Read,Bash,Glob,Grep",
            "--dangerously-skip-permissions",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(task.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return RunnerResult(
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            artifact=_parse_json_artifact(stdout),
        )


class CodexRunner(Runner):
    id = "codex"
    kind = "agent"

    async def check_health(self) -> tuple[bool, str]:
        path = shutil.which("codex")
        if path is None:
            return False, "codex not found"

        proc = await asyncio.create_subprocess_exec(
            "codex",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            version = stdout.splitlines()[0] if stdout else "version unknown"
            return True, f"found at {path} ({version})"

        message = stderr.splitlines()[0] if stderr else "codex --version failed"
        return False, message

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        cmd = [
            "codex",
            "exec",
            task.prompt,
            "-C",
            str(task.cwd),
            "-s",
            "workspace-write",
        ]
        stdout, stderr, exit_code = await _run_process(cmd, cwd=task.cwd)
        if exit_code != 0 and "CreateProcessWithLogonW failed: 1326" in stderr:
            fallback_cmd = [
                "codex",
                "exec",
                task.prompt,
                "-C",
                str(task.cwd),
                "--dangerously-bypass-approvals-and-sandbox",
            ]
            stdout, stderr, exit_code = await _run_process(fallback_cmd, cwd=task.cwd)

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


async def _run_process(cmd: list[str], cwd: Path) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return stdout, stderr, proc.returncode or 0


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
