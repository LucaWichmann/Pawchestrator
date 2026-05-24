"""Verification stage orchestration."""

from __future__ import annotations

import asyncio
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_verify_run,
    fail_verify_run,
    get_run_state,
    get_worktree_record,
    skip_verify_run,
    start_verify_run,
)

VERIFICATION_REPORT_SCHEMA = "pawchestrator.verification_report.v1"
VERIFY_COMMAND_ORDER = ("build", "test", "lint")
REPO_VERIFY_CONFIG_PATH = Path(".pawchestrator") / "verify.toml"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 600
SUMMARY_MAX_CHARS = 500


@dataclass(frozen=True)
class CommandSpec:
    name: str
    command: str


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class VerificationResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    report: dict[str, Any]


class ShellRunner:
    id = "shell"
    kind = "shell"

    def __init__(
        self,
        *,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        debug: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.debug = debug
        self.run_id = run_id

    async def run_command(self, name: str, command: str, cwd: Path) -> CommandResult:
        if self.debug:
            _debug_print_command(self.run_id, name, command)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes, timed_out = await _communicate_process(
                proc,
                timeout=self.timeout_seconds,
                debug=self.debug,
            )
        except TimeoutError:
            # Defensive fallback; _communicate_process normally converts timeouts.
            stdout_bytes, stderr_bytes = await proc.communicate()
            timed_out = True

        if timed_out:
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace")
                + f"\nCommand timed out after {self.timeout_seconds} seconds."
            )
            if self.debug:
                _debug_print_result(self.run_id, name, 124)
            return CommandResult(
                name=name,
                command=command,
                exit_code=124,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr,
            )

        exit_code = proc.returncode or 0
        if self.debug:
            _debug_print_result(self.run_id, name, exit_code)
        return CommandResult(
            name=name,
            command=command,
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )


async def _communicate_process(
    proc: asyncio.subprocess.Process,
    *,
    timeout: int,
    debug: bool,
) -> tuple[bytes, bytes, bool]:
    async def read_stream(
        stream: asyncio.StreamReader | None,
        label: str,
    ) -> bytes:
        if stream is None:
            return b""
        chunks: list[bytes] = []
        printed_label = False
        ended_with_newline = True
        while chunk := await stream.read(4096):
            chunks.append(chunk)
            if debug:
                text = chunk.decode("utf-8", errors="replace")
                if not printed_label:
                    print(f"[pawchestrator:debug] {label}:", flush=True)
                    printed_label = True
                print(text, end="", flush=True)
                ended_with_newline = text.endswith(("\n", "\r"))
        if debug and printed_label and not ended_with_newline:
            print(flush=True)
        return b"".join(chunks)

    stdout_task = asyncio.create_task(read_stream(proc.stdout, "stdout"))
    stderr_task = asyncio.create_task(read_stream(proc.stderr, "stderr"))
    wait_task = asyncio.create_task(proc.wait())
    done, _pending = await asyncio.wait(
        {stdout_task, stderr_task, wait_task},
        timeout=timeout,
    )

    timed_out = wait_task not in done
    if timed_out:
        proc.kill()
        await wait_task

    stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
    return stdout_bytes, stderr_bytes, timed_out


def _debug_print_command(run_id: str | None, name: str, command: str) -> None:
    run = run_id or "unknown"
    print(
        f"[pawchestrator:debug] run={run} stage=verify command={name}",
        flush=True,
    )
    print(f"[pawchestrator:debug] shell={command}", flush=True)


def _debug_print_result(run_id: str | None, name: str, exit_code: int) -> None:
    run = run_id or "unknown"
    print(
        (
            f"[pawchestrator:debug] run={run} stage=verify "
            f"command={name} exit_code={exit_code}"
        ),
        flush=True,
    )


async def run_verify(
    run_id: str,
    settings: Settings,
    *,
    runner: ShellRunner | None = None,
) -> VerificationResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    stage_id = await start_verify_run(settings, run_id=run_id)
    log_path = _verify_log_path(settings, run_id)
    artifact_path = _verification_report_path(settings, run_id)
    active_runner = runner or ShellRunner(debug=settings.debug, run_id=run_id)

    try:
        worktree = await get_worktree_record(settings, run_id=run_id)
        if worktree is None:
            raise RuntimeError(f"worktree record not found for run: {run_id}")

        worktree_path = Path(str(worktree["path"]))
        if not worktree_path.exists():
            raise RuntimeError(f"worktree path not found: {worktree_path}")

        repo_config_path = repo_verify_config_path_for(worktree_path)
        commands = load_verify_commands(repo_config_path)
        if commands is None:
            reason = "[verify] skipped - no repo config found"
            report = build_verification_report(
                status="skipped",
                commands=[],
                skip_reason=reason,
            )
            _write_verify_log(log_path, reason + "\n", [])
            _write_report(artifact_path, report)
            await skip_verify_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=artifact_path,
                reason=reason,
            )
            return VerificationResult(run_id, artifact_path, log_path, report)

        if not any(command.name in {"build", "test"} for command in commands):
            reason = "[verify] skipped - no build or test commands configured"
            report = build_verification_report(
                status="skipped",
                commands=[],
                skip_reason=reason,
            )
            _write_verify_log(log_path, reason + "\n", [])
            _write_report(artifact_path, report)
            await skip_verify_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=artifact_path,
                reason=reason,
            )
            return VerificationResult(run_id, artifact_path, log_path, report)

        results: list[CommandResult] = []
        for command in commands:
            result = await active_runner.run_command(
                command.name,
                command.command,
                worktree_path,
            )
            results.append(result)
            if result.exit_code != 0:
                break

        status = "failed" if any(result.exit_code != 0 for result in results) else "passed"
        report = build_verification_report(
            status=status,
            commands=results,
            skip_reason=None,
        )
        _write_verify_log(log_path, "", results)
        _write_report(artifact_path, report)
        await complete_verify_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
            passed=status == "passed",
            error=_verification_error(results) if status == "failed" else None,
        )
    except Exception as error:
        if not log_path.exists():
            _write_verify_log(log_path, str(error) + "\n", [])
        if not artifact_path.exists():
            _write_report(
                artifact_path,
                build_verification_report(
                    status="failed",
                    commands=[],
                    skip_reason=None,
                ),
            )
        await fail_verify_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return VerificationResult(run_id, artifact_path, log_path, report)


def repo_verify_config_path_for(worktree_path: Path) -> Path:
    return worktree_path / REPO_VERIFY_CONFIG_PATH


def load_verify_commands(path: Path) -> list[CommandSpec] | None:
    if not path.exists():
        return None

    with path.open("rb") as config_file:
        data = tomllib.load(config_file)
    raw_commands = data.get("commands", {})
    commands: list[CommandSpec] = []
    for name in VERIFY_COMMAND_ORDER:
        command = str(raw_commands.get(name) or "").strip()
        if command:
            commands.append(CommandSpec(name=name, command=command))
    return commands


def build_verification_report(
    *,
    status: str,
    commands: list[CommandResult],
    skip_reason: str | None,
) -> dict[str, Any]:
    return {
        "schema": VERIFICATION_REPORT_SCHEMA,
        "status": status,
        "commands": [
            {
                "command": command.command,
                "exit_code": command.exit_code,
                "stdout_summary": summarize_output(command.stdout),
                "stderr_summary": summarize_output(command.stderr),
            }
            for command in commands
        ],
        "skip_reason": skip_reason,
    }


def summarize_output(output: str) -> str:
    summary = " ".join(output.strip().split())
    if len(summary) <= SUMMARY_MAX_CHARS:
        return summary
    return summary[: SUMMARY_MAX_CHARS - 3].rstrip() + "..."


def _verification_error(results: list[CommandResult]) -> str | None:
    failed = next((result for result in results if result.exit_code != 0), None)
    if failed is None:
        return None
    detail = summarize_output(failed.stderr) or summarize_output(failed.stdout)
    message = f"{failed.name} exited {failed.exit_code}"
    if detail:
        return f"{message}: {detail}"
    return message


def _verification_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "verification_report.json"


def _verify_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "verify.log"


def _write_verify_log(
    log_path: Path,
    prelude: str,
    results: list[CommandResult],
) -> None:
    lines: list[str] = []
    if prelude:
        lines.append(prelude.rstrip())
    for result in results:
        lines.extend(
            [
                f"[command] {result.name}: {result.command}",
                f"[exit_code] {result.exit_code}",
                "[stdout]",
                result.stdout.rstrip(),
                "[stderr]",
                result.stderr.rstrip(),
            ]
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
