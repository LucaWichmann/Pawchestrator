"""Dependency checks for `pawchestrator doctor`."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST, Settings
from pawchestrator.db import init_db
from pawchestrator.runners import ClaudeRunner

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    label: str
    status: str
    message: str
    required: bool = True


def run_checks(settings: Settings, port: int = DEFAULT_PORT) -> list[CheckResult]:
    """Run all required and optional doctor checks."""

    return [
        check_binary("git", required=True),
        check_binary("gh", required=True),
        check_gh_auth(),
        check_claude_runner(),
        check_binary("codex", required=False),
        check_port_available(port),
        check_sqlite_writable(settings),
    ]


def has_required_failures(results: list[CheckResult]) -> bool:
    return any(result.required and result.status == STATUS_FAIL for result in results)


def check_binary(name: str, required: bool) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, STATUS_PASS, f"found at {path}", required=required)

    status = STATUS_FAIL if required else STATUS_WARN
    need = "required" if required else "optional"
    return CheckResult(name, status, f"{need} binary not found on PATH", required=required)


def check_claude_runner() -> CheckResult:
    healthy, message = asyncio.run(ClaudeRunner().check_health())
    status = STATUS_PASS if healthy else STATUS_WARN
    return CheckResult("claude", status, message, required=False)


def check_gh_auth() -> CheckResult:
    if not shutil.which("gh"):
        return CheckResult("gh auth", STATUS_FAIL, "gh binary not found", required=True)

    try:
        completed = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("gh auth", STATUS_FAIL, "gh auth status timed out", required=True)

    combined_output = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode == 0:
        account = _first_logged_in_account(combined_output)
        message = f"authenticated as {account}" if account else "authenticated"
        return CheckResult("gh auth", STATUS_PASS, message, required=True)

    message = combined_output.splitlines()[0] if combined_output else "gh auth status failed"
    return CheckResult("gh auth", STATUS_FAIL, message, required=True)


def check_port_available(port: int) -> CheckResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((LOCAL_HOST, port))
        except OSError as error:
            return CheckResult(
                "backend port",
                STATUS_FAIL,
                f"{LOCAL_HOST}:{port} unavailable ({error})",
                required=True,
            )

    return CheckResult(
        "backend port",
        STATUS_PASS,
        f"{LOCAL_HOST}:{port} available",
        required=True,
    )


def check_sqlite_writable(settings: Settings) -> CheckResult:
    try:
        database_path = asyncio.run(init_db(settings))
    except Exception as error:  # pragma: no cover - exact OS errors vary.
        return CheckResult(
            "SQLite",
            STATUS_FAIL,
            f"database not writable: {error}",
            required=True,
        )

    return CheckResult(
        "SQLite",
        STATUS_PASS,
        f"database writable at {_display_path(database_path)}",
        required=True,
    )


def _first_logged_in_account(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if "Logged in to github.com account" in stripped:
            return stripped.split("account", 1)[1].split("(", 1)[0].strip()
    return None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
