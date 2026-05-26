"""Dependency checks for `pawchestrator doctor`."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST, Settings
from pawchestrator.db import count_registered_repos, init_db
from pawchestrator import grill, implement, plan, scout
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    resolve_runner,
    runner_tool_mismatch_warning,
)
from pawchestrator.sessions import load_sessions

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STAGE_TOOL_REQUIREMENTS: tuple[tuple[str, str, list[str]], ...] = (
    ("scout", "claude", scout.REQUIRED_TOOLS),
    ("plan", "claude", plan.REQUIRED_TOOLS),
    ("grill", "claude", grill.REQUIRED_TOOLS),
    ("implement", "codex", implement.REQUIRED_TOOLS),
)


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
        check_binary(
            "pawchestrator",
            required=False,
            hint=(
                "pawchestrator not on PATH — run: uv tool install "
                "git+https://github.com/LucaWichmann/Pawchestrator.git"
            ),
        ),
        check_gh_auth(),
        check_wsl(settings),
        check_claude_runner(settings),
        check_codex_runner(settings),
        check_cross_review_runners(settings),
        check_port_available(port),
        *check_stage_tool_mismatches(settings),
        check_backend_routes(settings, port),
        check_sqlite_writable(settings),
        check_repo_registry(settings),
    ]


def has_required_failures(results: list[CheckResult]) -> bool:
    return any(result.required and result.status == STATUS_FAIL for result in results)


def check_binary(name: str, required: bool, hint: str | None = None) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, STATUS_PASS, f"found at {path}", required=required)

    status = STATUS_FAIL if required else STATUS_WARN
    need = "required" if required else "optional"
    message = hint or f"{need} binary not found on PATH"
    return CheckResult(name, status, message, required=required)


def check_claude_runner(settings: Settings | None = None) -> CheckResult:
    runtime_settings = settings or Settings()
    healthy, message = asyncio.run(
        ClaudeRunner(
            runtime_settings.runners.claude,
            debug=runtime_settings.debug,
        ).check_health()
    )
    status = STATUS_PASS if healthy else STATUS_WARN
    return CheckResult("claude", status, message, required=False)


def check_codex_runner(settings: Settings | None = None) -> CheckResult:
    runtime_settings = settings or Settings()
    healthy, message = asyncio.run(
        CodexRunner(
            runtime_settings.runners.codex,
            debug=runtime_settings.debug,
        ).check_health()
    )
    status = STATUS_PASS if healthy else STATUS_WARN
    return CheckResult("codex", status, message, required=False)


def check_cross_review_runners(settings: Settings) -> CheckResult:
    if not settings.review.cross_review:
        return CheckResult(
            "cross review",
            STATUS_PASS,
            "disabled",
            required=False,
        )

    claude_healthy, _ = asyncio.run(
        ClaudeRunner(
            settings.runners.claude,
            debug=settings.debug,
        ).check_health()
    )
    codex_healthy, _ = asyncio.run(
        CodexRunner(
            settings.runners.codex,
            debug=settings.debug,
        ).check_health()
    )
    healthy_count = int(claude_healthy) + int(codex_healthy)
    if healthy_count == 1:
        return CheckResult(
            "cross review",
            STATUS_WARN,
            "cross_review is enabled but only one runner is available",
            required=False,
        )
    if healthy_count == 0:
        return CheckResult(
            "cross review",
            STATUS_WARN,
            "cross_review is enabled but no runners are available",
            required=False,
        )
    return CheckResult(
        "cross review",
        STATUS_PASS,
        "both runners available",
        required=False,
    )


def check_stage_tool_mismatches(settings: Settings) -> list[CheckResult]:
    results: list[CheckResult] = []
    for stage_name, default_runner, required_tools in STAGE_TOOL_REQUIREMENTS:
        runner = resolve_runner(settings, stage_name, default_runner)
        warning = runner_tool_mismatch_warning(
            runner,
            stage_name=stage_name,
            required_tools=required_tools,
        )
        if warning is None:
            continue
        results.append(
            CheckResult(
                f"{stage_name} tools",
                STATUS_WARN,
                warning,
                required=False,
            )
        )
    return results


def check_wsl(settings: Settings | None = None) -> CheckResult:
    runtime_settings = settings or Settings()
    if not _uses_wsl(runtime_settings):
        return CheckResult("WSL", STATUS_PASS, "not configured for runner use", required=False)
    if not sys.platform.startswith("win"):
        return CheckResult("WSL", STATUS_WARN, "WSL runner execution is Windows-only", required=False)
    wsl_path = shutil.which("wsl.exe") or shutil.which("wsl")
    if wsl_path is None:
        return CheckResult("WSL", STATUS_WARN, "wsl.exe not found", required=False)

    try:
        completed = subprocess.run(
            [wsl_path, "--status"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("WSL", STATUS_WARN, "wsl.exe --status timed out", required=False)

    if completed.returncode == 0:
        distro = runtime_settings.runners.codex.wsl_distro or "default distro"
        return CheckResult("WSL", STATUS_PASS, f"available via {distro}", required=False)
    message = (completed.stderr or completed.stdout).strip() or "wsl.exe --status failed"
    return CheckResult("WSL", STATUS_WARN, message.splitlines()[0], required=False)


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


def check_backend_routes(settings: Settings, port: int = DEFAULT_PORT) -> CheckResult:
    sessions = load_sessions(settings)
    tokens = sessions.get("tokens", [])
    if not tokens:
        return CheckResult(
            "backend routes",
            STATUS_WARN,
            "no pairing token; live route check skipped",
            required=False,
        )

    try:
        response = httpx.get(
            f"http://{LOCAL_HOST}:{port}/openapi.json",
            headers={"X-Pawchestrator-Token": str(tokens[-1])},
            timeout=2.0,
        )
    except httpx.ConnectError:
        return CheckResult(
            "backend routes",
            STATUS_WARN,
            f"backend not running on {LOCAL_HOST}:{port}; live route check skipped",
            required=False,
        )
    except httpx.TimeoutException:
        return CheckResult(
            "backend routes",
            STATUS_FAIL,
            f"backend on {LOCAL_HOST}:{port} timed out during route check",
            required=True,
        )
    except httpx.HTTPError as error:
        return CheckResult(
            "backend routes",
            STATUS_FAIL,
            f"backend route check failed: {error}",
            required=True,
        )

    if response.status_code != 200:
        return CheckResult(
            "backend routes",
            STATUS_FAIL,
            f"openapi returned HTTP {response.status_code}",
            required=True,
        )

    paths = response.json().get("paths", {})
    if "/issue/grill" not in paths:
        return CheckResult(
            "backend routes",
            STATUS_FAIL,
            "live backend is missing /issue/grill; stop stale serve process and restart",
            required=True,
        )

    return CheckResult(
        "backend routes",
        STATUS_PASS,
        "live backend exposes /issue/grill",
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


def check_repo_registry(settings: Settings) -> CheckResult:
    try:
        count = asyncio.run(count_registered_repos(settings))
    except Exception as error:  # pragma: no cover - exact SQLite errors vary.
        return CheckResult(
            "repo registry",
            STATUS_WARN,
            f"repo registry unavailable: {error}",
            required=False,
        )

    if count == 0:
        return CheckResult(
            "repo registry",
            STATUS_WARN,
            "0 repos registered — run pawchestrator repo add <path>",
            required=False,
        )

    return CheckResult(
        "repo registry",
        STATUS_PASS,
        f"{count} repo(s) registered",
        required=False,
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


def _uses_wsl(settings: Settings) -> bool:
    return (
        settings.runners.claude.execution == "wsl"
        or settings.runners.codex.execution in {"auto", "wsl"}
    ) and (
        settings.runners.claude.wsl_enabled or settings.runners.codex.wsl_enabled
    )
