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
