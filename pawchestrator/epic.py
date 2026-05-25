"""Epic issue orchestration for sequential sub-issue runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pawchestrator.config import Settings
from pawchestrator.github import GitHubIssueClient, get_gh_token, parse_issue_url
from pawchestrator.pipeline import run_pipeline

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class SubRunResult:
    issue_number: int
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
) -> EpicResult:
    reference = parse_issue_url(issue_url)
    client = GitHubIssueClient(get_gh_token())
    sub_issues = await client.fetch_sub_issues(reference)
    sub_runs: list[SubRunResult] = []

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
            )
        except Exception:
            if settings.pipeline.epic_fail_fast:
                raise
            progress(f"[epic] sub-issue #{issue_number} failed; continuing")
            continue
        sub_runs.append(
            SubRunResult(
                issue_number=issue_number,
                run_id=pipeline_result.run_id,
                pr_url=pipeline_result.pr_url,
            )
        )

    return EpicResult(group_id=group_id, sub_runs=sub_runs)
