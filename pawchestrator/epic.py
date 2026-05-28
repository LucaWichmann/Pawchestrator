"""Epic issue orchestration for sequential sub-issue runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Callable

import aiosqlite

from pawchestrator.config import Settings
from pawchestrator.db import (
    get_latest_pipeline_runs_by_group_issue,
    get_run_state,
    set_run_pr_url,
    upsert_worktree_record,
    utc_now_iso,
)
from pawchestrator.github import (
    GitHubIssueClient,
    IssueReference,
    get_gh_token,
    parse_issue_url,
    with_generated_attribution,
)
from pawchestrator.implement import DEFAULT_BASE_BRANCH, ensure_issue_worktree, slugify
from pawchestrator.pipeline import run_pipeline
from pawchestrator.pr import create_worktree_pr
from pawchestrator.stage_lifecycle import StageResult

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class SubRunResult:
    issue_number: int
    title: str
    run_id: str
    pr_url: str


@dataclass(frozen=True)
class EpicResult:
    group_id: str
    sub_runs: list[SubRunResult]


async def run_epic(
    issue_url: str,
    settings: Settings,
    *,
    repo_path: Path,
    progress: ProgressFn = print,
    group_id: str,
    parent_run_id: str,
) -> EpicResult:
    reference = parse_issue_url(issue_url)
    client = GitHubIssueClient(get_gh_token())
    sub_issues = await client.fetch_sub_issues(reference)
    title = await _fetch_epic_title(client, reference)
    parent_state = await get_run_state(settings, parent_run_id)
    mode = str(
        (parent_state or {}).get("epic_branch_mode")
        or settings.pipeline.epic_branch_mode
    )
    is_resume = (parent_state or {}).get("status") == "epic_failed"
    parent_pr_url = str((parent_state or {}).get("pr_url") or "")
    epic_branch = f"paw/epic-{reference.number}-{slugify(title)}"
    epic_path = (
        settings.app_dir
        / "worktrees"
        / reference.owner
        / reference.repo
        / f"epic-{reference.number}"
    )
    sub_runs: list[SubRunResult] = []

    await _mark_epic_status(settings, run_id=parent_run_id, status="epic_running")
    try:
        epic_worktree = await ensure_issue_worktree(
            settings,
            snapshot={
                "owner": reference.owner,
                "repo": reference.repo,
                "number": reference.number,
                "title": title,
            },
            source_repo_path=repo_path,
            branch_override=epic_branch,
            path_override=epic_path,
            base_branch=DEFAULT_BASE_BRANCH,
            allow_dirty_existing_worktree=is_resume and mode == "epic",
        )
        await upsert_worktree_record(
            settings,
            run_id=parent_run_id,
            owner=reference.owner,
            repo=reference.repo,
            issue_number=reference.number,
            branch=epic_worktree.branch,
            path=epic_worktree.path,
        )

        if mode == "epic-with-sub-issues":
            if parent_pr_url:
                progress(f"[epic] draft PR already exists - {parent_pr_url}")
            else:
                epic_pr = await _create_epic_pr(
                    settings=settings,
                    run_id=parent_run_id,
                    issue_number=reference.number,
                    title=title,
                    branch=epic_worktree.branch,
                    worktree_path=epic_worktree.path,
                    draft=True,
                    allow_empty_commit=True,
                    sub_runs=[],
                )
                parent_pr_url = _stage_pr_url(epic_pr)
                await set_run_pr_url(
                    settings,
                    run_id=parent_run_id,
                    pr_url=parent_pr_url,
                )
                progress(f"[epic] draft PR ready - {parent_pr_url}")

        latest_child_runs = await get_latest_pipeline_runs_by_group_issue(
            settings,
            group_id,
        )
        for sub_issue in sub_issues:
            issue_number = int(sub_issue["number"])
            completed_child = latest_child_runs.get(issue_number)
            if completed_child is not None and completed_child.get("status") == "completed":
                progress(f"[epic] skipping completed sub-issue #{issue_number}")
                sub_runs.append(
                    SubRunResult(
                        issue_number=issue_number,
                        title=str(sub_issue.get("title") or ""),
                        run_id=str(completed_child["id"]),
                        pr_url=str(completed_child.get("pr_url") or ""),
                    )
                )
                continue

            child_issue_url = str(
                sub_issue.get("url")
                or f"https://github.com/{reference.owner}/{reference.repo}/issues/{issue_number}"
            )
            progress(f"[epic] running sub-issue #{issue_number}")
            try:
                pipeline_result = await run_pipeline(
                    child_issue_url,
                    settings,
                    repo_path=repo_path,
                    progress=progress,
                    group_id=group_id,
                    create_pr=mode == "epic-with-sub-issues",
                    worktree_branch=epic_worktree.branch if mode == "epic" else None,
                    worktree_path=epic_worktree.path if mode == "epic" else None,
                    base_branch=(
                        DEFAULT_BASE_BRANCH
                        if mode == "epic"
                        else epic_worktree.branch
                    ),
                    pr_base_branch=epic_worktree.branch,
                    allow_dirty_existing_worktree=mode == "epic",
                    defer_verification=mode == "epic",
                )
            except Exception:
                if settings.pipeline.epic_fail_fast:
                    raise
                progress(f"[epic] sub-issue #{issue_number} failed; continuing")
                continue
            sub_runs.append(
                SubRunResult(
                    issue_number=issue_number,
                    title=str(sub_issue.get("title") or ""),
                    run_id=pipeline_result.run_id,
                    pr_url=pipeline_result.pr_url,
                )
            )

        epic_pr_url = None
        if mode == "epic":
            if len(sub_runs) != len(sub_issues):
                raise RuntimeError(
                    "epic final PR blocked because not all sub-issues completed"
                )
            epic_pr = await _create_epic_pr(
                settings=settings,
                run_id=parent_run_id,
                issue_number=reference.number,
                title=title,
                branch=epic_worktree.branch,
                worktree_path=epic_worktree.path,
                draft=settings.pr.draft,
                allow_empty_commit=False,
                sub_runs=sub_runs,
            )
            epic_pr_url = _stage_pr_url(epic_pr)
            progress(f"[epic] PR ready - {epic_pr_url}")
        await _mark_epic_status(
            settings,
            run_id=parent_run_id,
            status="epic_complete",
            pr_url=epic_pr_url,
        )
    except Exception:
        await _mark_epic_status(settings, run_id=parent_run_id, status="epic_failed")
        raise

    return EpicResult(group_id=group_id, sub_runs=sub_runs)


async def _mark_epic_status(
    settings: Settings,
    *,
    run_id: str,
    status: str,
    pr_url: str | None = None,
) -> None:
    now = utc_now_iso()
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = ?, current_stage = 'epic',
                pr_url = COALESCE(?, pr_url), updated_at = ?
            WHERE id = ?
            """,
            (status, pr_url, now, run_id),
        )
        await db.commit()


async def _fetch_epic_title(
    client: GitHubIssueClient,
    reference: IssueReference,
) -> str:
    try:
        return await client.fetch_issue_title(reference)
    except AttributeError:
        return "epic"


def _stage_pr_url(result: Any) -> str:
    if hasattr(result, "pr_url"):
        return str(result.pr_url)
    return str(result.report["pr_url"])


async def _create_epic_pr(
    *,
    settings: Settings,
    run_id: str,
    issue_number: int,
    title: str,
    branch: str,
    worktree_path: Path,
    draft: bool,
    allow_empty_commit: bool,
    sub_runs: list[SubRunResult],
) -> StageResult:
    return await create_worktree_pr(
        settings=settings,
        run_id=run_id,
        worktree_path=worktree_path,
        branch=branch,
        base_branch=DEFAULT_BASE_BRANCH,
        title=f"feat: {title or f'Epic {issue_number}'} (#{issue_number})",
        body=_epic_pr_body(
            settings=settings,
            run_id=run_id,
            issue_number=issue_number,
            sub_runs=sub_runs,
        ),
        draft=draft,
        issue_number=issue_number,
        allow_empty_commit=allow_empty_commit,
        assignees=[],
    )


def _epic_pr_body(
    *,
    settings: Settings,
    run_id: str,
    issue_number: int,
    sub_runs: list[SubRunResult],
) -> str:
    if not sub_runs:
        body = f"""## Summary

Pawchestrator opened this PR to collect epic sub-issue work.

## Linked issues

Implements epic #{issue_number}

## Local artifacts

Internal artifacts are stored locally under epic run `{run_id}` and were not posted publicly.
"""
        return with_generated_attribution(body)

    completed_issue_refs = ", ".join(f"#{sub_run.issue_number}" for sub_run in sub_runs)
    closing_refs = ", ".join(f"closes #{sub_run.issue_number}" for sub_run in sub_runs)
    completed_sub_issues = "\n".join(
        _completed_sub_issue_line(sub_run) for sub_run in sub_runs
    )
    changed_lines = "\n".join(
        _change_summary_line(settings, sub_run) for sub_run in sub_runs
    )
    verification_lines = "\n".join(
        _verification_summary_line(settings, sub_run) for sub_run in sub_runs
    )

    body = f"""## Summary

Pawchestrator implemented the completed sub-issues for epic #{issue_number}.

## Linked issues

Implements epic #{issue_number}

Sub-issues completed: {completed_issue_refs}

{closing_refs.capitalize()}

## Completed sub-issues

{completed_sub_issues}

## What changed

{changed_lines}

## Verification

{verification_lines}

## Local artifacts

Internal artifacts are stored locally under epic run `{run_id}` and child run ids listed above.
"""
    return with_generated_attribution(body)


def _completed_sub_issue_line(sub_run: SubRunResult) -> str:
    title = f" - {sub_run.title}" if sub_run.title else ""
    return f"- #{sub_run.issue_number}{title} (run `{sub_run.run_id}`)"


def _change_summary_line(settings: Settings, sub_run: SubRunResult) -> str:
    report = _read_optional_json(
        settings.app_dir / "runs" / sub_run.run_id / "implementation_report.json"
    )
    summary = "not recorded"
    if report is not None:
        summary = str(report.get("diff_summary") or "")
        if not summary:
            files_changed = _string_list(report.get("files_changed"))
            summary = (
                _files_changed_summary(files_changed) if files_changed else "not recorded"
            )
    return f"- #{sub_run.issue_number}: {summary}"


def _verification_summary_line(settings: Settings, sub_run: SubRunResult) -> str:
    report = _read_optional_json(
        settings.app_dir / "runs" / sub_run.run_id / "verification_report.json"
    )
    if report is None:
        return f"- #{sub_run.issue_number}: not recorded"

    status = str(report.get("status") or "not recorded")
    if status == "skipped":
        reason = str(report.get("skip_reason") or "no reason recorded")
        return f"- #{sub_run.issue_number}: skipped - {reason}"
    return f"- #{sub_run.issue_number}: {status}"


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _files_changed_summary(files_changed: list[str]) -> str:
    names = ", ".join(files_changed[:5])
    suffix = "" if len(files_changed) <= 5 else f", and {len(files_changed) - 5} more"
    plural = "file" if len(files_changed) == 1 else "files"
    return f"{len(files_changed)} {plural} changed: {names}{suffix}"
