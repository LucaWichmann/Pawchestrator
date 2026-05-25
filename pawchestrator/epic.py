"""Epic issue orchestration for sequential sub-issue runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_epic_run,
    fail_epic_run,
    set_run_pr_url,
    start_epic_run,
    upsert_worktree_record,
)
from pawchestrator.github import GitHubIssueClient, IssueReference, get_gh_token, parse_issue_url
from pawchestrator.implement import DEFAULT_BASE_BRANCH, ensure_issue_worktree, slugify
from pawchestrator.pipeline import run_pipeline
from pawchestrator.pr import PrDraftResult, create_worktree_pr

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
    mode = settings.pipeline.epic_branch_mode
    epic_branch = f"paw/epic-{reference.number}-{slugify(title)}"
    epic_path = (
        settings.app_dir
        / "worktrees"
        / reference.owner
        / reference.repo
        / f"epic-{reference.number}"
    )
    sub_runs: list[SubRunResult] = []

    await start_epic_run(settings, run_id=parent_run_id)
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
            epic_pr = await _create_epic_pr(
                settings=settings,
                run_id=parent_run_id,
                issue_number=reference.number,
                title=title,
                branch=epic_worktree.branch,
                worktree_path=epic_worktree.path,
                draft=True,
                allow_empty_commit=True,
            )
            await set_run_pr_url(settings, run_id=parent_run_id, pr_url=epic_pr.pr_url)
            progress(f"[epic] draft PR ready - {epic_pr.pr_url}")

        for sub_issue in sub_issues:
            issue_number = int(sub_issue["number"])
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
            epic_pr = await _create_epic_pr(
                settings=settings,
                run_id=parent_run_id,
                issue_number=reference.number,
                title=title,
                branch=epic_worktree.branch,
                worktree_path=epic_worktree.path,
                draft=settings.pr.draft,
                allow_empty_commit=False,
            )
            epic_pr_url = epic_pr.pr_url
            progress(f"[epic] PR ready - {epic_pr_url}")
        await complete_epic_run(settings, run_id=parent_run_id, pr_url=epic_pr_url)
    except Exception:
        await fail_epic_run(settings, run_id=parent_run_id)
        raise

    return EpicResult(group_id=group_id, sub_runs=sub_runs)


async def _fetch_epic_title(
    client: GitHubIssueClient,
    reference: IssueReference,
) -> str:
    try:
        return await client.fetch_issue_title(reference)
    except AttributeError:
        return "epic"


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
) -> PrDraftResult:
    return await create_worktree_pr(
        settings=settings,
        run_id=run_id,
        worktree_path=worktree_path,
        branch=branch,
        base_branch=DEFAULT_BASE_BRANCH,
        title=f"feat: {title or f'Epic {issue_number}'} (#{issue_number})",
        body=_epic_pr_body(run_id=run_id, issue_number=issue_number),
        draft=draft,
        issue_number=issue_number,
        allow_empty_commit=allow_empty_commit,
        assignees=[],
    )


def _epic_pr_body(*, run_id: str, issue_number: int) -> str:
    return f"""## Summary

Pawchestrator implemented epic sub-issues.

## Linked issue

Fixes #{issue_number}

## Local artifacts

Internal artifacts are stored locally under epic run `{run_id}` and were not posted publicly.
"""
