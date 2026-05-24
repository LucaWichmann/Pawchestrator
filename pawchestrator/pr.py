"""Draft pull request stage orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_pr_run,
    fail_pr_run,
    get_run_state,
    get_worktree_record,
    start_pr_run,
)

PR_DRAFT_SCHEMA = "pawchestrator.pr_draft.v1"
DEFAULT_BASE_BRANCH = "main"


@dataclass(frozen=True)
class PrDraftResult:
    run_id: str
    artifact_path: Path
    pr_url: str
    branch: str
    title: str
    draft: dict[str, Any]


async def run_pr(
    run_id: str,
    settings: Settings,
    *,
    allow_empty_commit: bool = False,
) -> PrDraftResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    stage_id = await start_pr_run(settings, run_id=run_id)
    artifact_path = _pr_draft_path(settings, run_id)

    try:
        worktree = await get_worktree_record(settings, run_id=run_id)
        if worktree is None:
            raise RuntimeError(f"worktree record not found for run: {run_id}")

        worktree_path = Path(str(worktree["path"]))
        if not worktree_path.exists():
            raise RuntimeError(f"worktree path not found: {worktree_path}")

        snapshot = _read_json(_snapshot_artifact_path(settings, run_id))
        plan = _read_json(_plan_artifact_path(settings, run_id))
        verification = _read_json(_verification_report_path(settings, run_id))

        branch = str(worktree["branch"])
        issue_number = int(state["issue_number"])
        issue_title = str(snapshot.get("title") or f"Issue {issue_number}")
        title = f"fix: {issue_title} (#{issue_number})"
        body = build_pr_body(state, plan, verification)

        await _ensure_branch_has_pr_commits(
            worktree_path,
            issue_number=issue_number,
            allow_empty_commit=allow_empty_commit,
        )
        await _run_git_checked(["push", "-u", "origin", branch], worktree_path)
        pr_url = await _create_or_find_pr(
            title=title,
            body=body,
            base=DEFAULT_BASE_BRANCH,
            branch=branch,
            cwd=worktree_path,
            draft=settings.pr.draft,
        )
        draft = build_pr_draft(
            pr_url=pr_url,
            branch=branch,
            base=DEFAULT_BASE_BRANCH,
            title=title,
        )
        _write_report(artifact_path, draft)
        await complete_pr_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
            pr_url=pr_url,
        )
    except Exception as error:
        await fail_pr_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return PrDraftResult(
        run_id=run_id,
        artifact_path=artifact_path,
        pr_url=pr_url,
        branch=branch,
        title=title,
        draft=draft,
    )


def build_pr_body(
    run: dict[str, Any],
    plan: dict[str, Any],
    verify: dict[str, Any],
) -> str:
    steps = _plan_steps(plan)
    verify_section = _verification_section(verify)
    run_id = str(run["id"])
    issue_number = int(run["issue_number"])
    return f"""## Summary

{plan.get("approach_summary") or "Pawchestrator implemented the issue plan."}

## Linked issue

Fixes #{issue_number}

## What Pawchestrator did

{steps}

## Verification

{verify_section}

## Local artifacts

Internal artifacts are stored locally under run `{run_id}` and were not posted publicly.
"""


def build_pr_draft(*, pr_url: str, branch: str, base: str, title: str) -> dict[str, Any]:
    return {
        "schema": PR_DRAFT_SCHEMA,
        "pr_url": pr_url,
        "branch": branch,
        "base": base,
        "title": title,
    }


async def _create_or_find_pr(
    *,
    title: str,
    body: str,
    base: str,
    branch: str,
    cwd: Path,
    draft: bool,
) -> str:
    cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--base",
        base,
        "--head",
        branch,
    ]
    if draft:
        cmd.insert(3, "--draft")

    stdout, stderr, exit_code = await _run_process(cmd, cwd)
    if exit_code == 0:
        return _extract_pr_url(stdout)

    detail = stderr.strip() or stdout.strip() or "gh pr create failed"
    if _looks_like_existing_pr(detail):
        view_stdout, view_stderr, view_exit = await _run_process(
            ["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
            cwd,
        )
        if view_exit == 0:
            return _extract_pr_url(view_stdout)
        view_detail = view_stderr.strip() or view_stdout.strip() or "gh pr view failed"
        raise RuntimeError(f"{detail}; failed to retrieve existing PR: {view_detail}")

    raise RuntimeError(detail)


async def _ensure_branch_has_pr_commits(
    cwd: Path,
    *,
    issue_number: int,
    allow_empty_commit: bool,
) -> None:
    commit_count = (await _run_git_checked(
        ["rev-list", "--count", f"{DEFAULT_BASE_BRANCH}..HEAD"],
        cwd,
    )).strip()
    if commit_count != "0":
        return

    if not allow_empty_commit:
        raise RuntimeError(
            f"branch has no commits relative to {DEFAULT_BASE_BRANCH}; cannot create PR"
        )

    await _run_git_checked(
        [
            "commit",
            "--allow-empty",
            "-m",
            f"chore(paw): record no-op for issue #{issue_number}",
        ],
        cwd,
    )


async def _run_git_checked(args: list[str], cwd: Path) -> str:
    stdout, stderr, exit_code = await _run_process(["git", *args], cwd)
    if exit_code != 0:
        detail = stderr.strip() or stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return stdout


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


def _verification_section(verify: dict[str, Any]) -> str:
    status = str(verify.get("status") or "")
    commands = list(verify.get("commands") or [])
    if status == "passed":
        return "All checks passed."
    if status == "skipped":
        reason = str(verify.get("skip_reason") or "Verification skipped.")
        return reason
    if commands:
        return "\n".join(
            f"- `{command.get('command', '')}` exit {command.get('exit_code', '')}"
            for command in commands
        )
    return "Verification failed."


def _plan_steps(plan: dict[str, Any]) -> str:
    steps = list(plan.get("steps") or [])
    if not steps:
        return "- No plan steps recorded."
    return "\n".join(f"- {step.get('description', '')}" for step in steps)


def _extract_pr_url(output: str) -> str:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("https://") and "/pull/" in line:
            return line
    raise RuntimeError("gh did not return a pull request URL")


def _looks_like_existing_pr(message: str) -> bool:
    normalized = message.lower()
    return "already exists" in normalized and "pull request" in normalized


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact was not a JSON object: {path}")
    return payload


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _plan_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_plan.json"


def _verification_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "verification_report.json"


def _pr_draft_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "pr_draft.json"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
