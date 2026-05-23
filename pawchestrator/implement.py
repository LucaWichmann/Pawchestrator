"""Implementation stage orchestration."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_implement_run,
    fail_implement_run,
    get_run_state,
    start_implement_run,
    upsert_worktree_record,
)
from pawchestrator.runners import CodexRunner, Runner, RunnerTask

IMPLEMENTATION_REPORT_SCHEMA = "pawchestrator.implementation_report.v1"
SLUG_MAX_LENGTH = 40


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str
    reused: bool


@dataclass(frozen=True)
class ImplementationResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    worktree_path: Path
    branch: str
    report: dict[str, Any]


async def run_implement(
    run_id: str,
    settings: Settings,
    *,
    repo_path: Path | None = None,
    runner: Runner | None = None,
) -> ImplementationResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    stage_id = await start_implement_run(settings, run_id=run_id)
    source_repo_path = (repo_path or Path.cwd()).resolve()
    active_runner = runner or CodexRunner()
    log_path = _implement_log_path(settings, run_id)
    artifact_path = _implementation_report_path(settings, run_id)
    worktree_info: WorktreeInfo | None = None

    try:
        snapshot_path = _snapshot_artifact_path(settings, run_id)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"issue snapshot not found: {snapshot_path}")

        plan_path = _plan_artifact_path(settings, run_id)
        if not plan_path.exists():
            raise FileNotFoundError(f"implementation plan not found: {plan_path}")

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        implementation_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        worktree_info = await ensure_issue_worktree(
            settings,
            snapshot=snapshot,
            source_repo_path=source_repo_path,
        )
        await upsert_worktree_record(
            settings,
            run_id=run_id,
            owner=str(snapshot.get("owner") or state["owner"]),
            repo=str(snapshot.get("repo") or state["repo"]),
            issue_number=int(snapshot.get("number") or state["issue_number"]),
            branch=worktree_info.branch,
            path=worktree_info.path,
        )

        healthy, message = await active_runner.check_health()
        if not healthy:
            raise RuntimeError(message)

        result = await active_runner.run_task(
            RunnerTask(
                prompt=build_implement_prompt(
                    snapshot,
                    implementation_plan,
                    worktree_info.path,
                ),
                cwd=worktree_info.path,
                run_id=run_id,
                stage_name="implement",
            )
        )
        _write_implement_log(log_path, result.stdout, result.stderr)

        files_changed = files_changed_from_diff(result.diff)
        report = build_implementation_report(
            status="success" if result.exit_code == 0 else "error",
            files_changed=files_changed,
            diff=result.diff,
            stdout=result.stdout,
            stderr=result.stderr,
            error=None,
        )
        _write_report(artifact_path, report)

        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Codex runner failed"
            report["error"] = detail
            report["status"] = "error"
            _write_report(artifact_path, report)
            raise RuntimeError(detail)

        await complete_implement_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        if not log_path.exists():
            _write_implement_log(log_path, "", str(error))
        if not artifact_path.exists():
            _write_report(
                artifact_path,
                build_implementation_report(
                    status="error",
                    files_changed=[],
                    diff="",
                    stdout="",
                    stderr="",
                    error=str(error),
                ),
            )
        await fail_implement_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return ImplementationResult(
        run_id=run_id,
        artifact_path=artifact_path,
        log_path=log_path,
        worktree_path=worktree_info.path,
        branch=worktree_info.branch,
        report=report,
    )


async def ensure_issue_worktree(
    settings: Settings,
    *,
    snapshot: dict[str, Any],
    source_repo_path: Path,
) -> WorktreeInfo:
    owner = str(snapshot.get("owner") or "")
    repo = str(snapshot.get("repo") or "")
    number = int(snapshot.get("number") or 0)
    title = str(snapshot.get("title") or "")
    branch = f"paw/issue-{number}-{slugify(title)}"
    path = settings.app_dir / "worktrees" / owner / repo / f"issue-{number}"

    if path.exists():
        if (path / ".git").exists():
            return WorktreeInfo(path=path, branch=branch, reused=True)
        raise RuntimeError(f"worktree path exists but is not a git worktree: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = await _git_branch_exists(source_repo_path, branch)
    if branch_exists:
        await _run_git_checked(["worktree", "add", str(path), branch], source_repo_path)
    else:
        await _run_git_checked(
            ["worktree", "add", "-b", branch, str(path)],
            source_repo_path,
        )
    return WorktreeInfo(path=path, branch=branch, reused=False)


def build_implement_prompt(
    snapshot: dict[str, Any],
    implementation_plan: dict[str, Any],
    worktree_path: Path,
) -> str:
    return f"""You are implementing a GitHub issue in a local git worktree.

Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}
Working directory: {worktree_path}

Issue body:
{snapshot.get("body", "")}

IssueSnapshot JSON:
{json.dumps(snapshot, indent=2, sort_keys=True)}

Implementation plan:
{json.dumps(implementation_plan, indent=2, sort_keys=True)}

Implement the changes described in the plan. Make granular, well-named commits as you go.
Commit message format: `type(scope): description` (conventional commits).
Do not run build or test commands - verification is handled separately.
"""


def build_implementation_report(
    *,
    status: str,
    files_changed: list[str],
    diff: str,
    stdout: str,
    stderr: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "schema": IMPLEMENTATION_REPORT_SCHEMA,
        "status": status,
        "files_changed": files_changed,
        "diff_summary": summarize_diff(files_changed, diff),
        "codex_output": f"{stdout}{stderr}",
        "error": error,
    }


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH] or "issue"


def files_changed_from_diff(diff: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path not in seen:
            seen.add(path)
            files.append(path)
    return files


def summarize_diff(files_changed: list[str], diff: str) -> str:
    if not diff.strip():
        return "0 files changed"
    count = len(files_changed)
    if count == 0:
        return "diff captured"
    names = ", ".join(files_changed[:5])
    suffix = "" if count <= 5 else f", and {count - 5} more"
    plural = "file" if count == 1 else "files"
    return f"{count} {plural} changed: {names}{suffix}"


async def _git_branch_exists(source_repo_path: Path, branch: str) -> bool:
    _, _, exit_code = await _run_git(
        ["rev-parse", "--verify", f"refs/heads/{branch}"],
        source_repo_path,
    )
    return exit_code == 0


async def _run_git_checked(args: list[str], cwd: Path) -> str:
    stdout, stderr, exit_code = await _run_git(args, cwd)
    if exit_code != 0:
        detail = stderr.strip() or stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return stdout


async def _run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return stdout, stderr, proc.returncode or 0


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _plan_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_plan.json"


def _implementation_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_report.json"


def _implement_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "implement.log"


def _write_implement_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
