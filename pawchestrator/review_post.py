"""Submit review artifacts to GitHub pull request reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_review_post_run,
    fail_review_post_run,
    get_run_state,
    insert_run_warning,
    lookup_repo_path,
    start_review_post_run,
)
from pawchestrator.github import (
    GitHubIssueClient,
    get_gh_token,
    parse_commentable_added_lines,
)
from pawchestrator.review import REVIEW_VERDICTS, fetch_pr_diff, review_report_path


@dataclass(frozen=True)
class ReviewPostResult:
    run_id: str
    submitted_comments: int
    skipped_comments: int
    review_id: int | None


async def run_review_post(
    run_id: str,
    settings: Settings,
    *,
    client: GitHubIssueClient | None = None,
    diff_text: str | None = None,
) -> ReviewPostResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    stage_id = await start_review_post_run(settings, run_id=run_id)
    try:
        owner = str(state["owner"])
        repo = str(state["repo"])
        pr_number = int(state["pr_number"])
        report = read_review_report(review_report_path(settings, run_id))
        repo_path = await lookup_repo_path(settings, owner=owner, repo=repo)
        cwd = repo_path or Path.cwd()
        diff = diff_text
        if diff is None:
            diff = await fetch_pr_diff(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                cwd=cwd,
            )
        commentable_lines = parse_commentable_added_lines(diff)
        comments, skipped = await build_review_comments(
            settings,
            run_id=run_id,
            report=report,
            commentable_lines=commentable_lines,
        )
        active_client = client or GitHubIssueClient(get_gh_token())
        review_id = await active_client.post_pr_review(
            owner,
            repo,
            pr_number,
            body=str(report["summary"]),
            event=str(report["verdict"]),
            comments=comments,
        )
        await complete_review_post_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
        )
    except Exception:
        await fail_review_post_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error="Stage failed. See local run logs.",
        )
        raise

    return ReviewPostResult(
        run_id=run_id,
        submitted_comments=len(comments),
        skipped_comments=skipped,
        review_id=review_id,
    )


def read_review_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"review report not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"review report is not valid JSON: {path}") from error

    if not isinstance(report, dict):
        raise ValueError("review report must be a JSON object")
    if not isinstance(report.get("summary"), str) or not report["summary"]:
        raise ValueError("review report summary must be a non-empty string")
    if report.get("verdict") not in REVIEW_VERDICTS:
        raise ValueError(
            "review report verdict must be REQUEST_CHANGES, APPROVE, or COMMENT"
        )
    inline_comments = report.get("inline_comments")
    if not isinstance(inline_comments, list):
        raise ValueError("review report inline_comments must be a list")
    return report


async def build_review_comments(
    settings: Settings,
    *,
    run_id: str,
    report: dict[str, Any],
    commentable_lines: list[dict[str, object]],
) -> tuple[list[dict[str, Any]], int]:
    comments: list[dict[str, Any]] = []
    commentable = {
        (str(line.get("path") or ""), line.get("line"))
        for line in commentable_lines
        if isinstance(line, dict)
    }
    skipped = 0
    for raw_comment in report.get("inline_comments", []):
        if not isinstance(raw_comment, dict):
            continue
        file_path = str(raw_comment.get("file") or "")
        line = raw_comment.get("line")
        body = str(raw_comment.get("body") or "")
        if not file_path or not isinstance(line, int) or not body:
            continue
        if (file_path, line) not in commentable:
            skipped += 1
            await insert_run_warning(
                settings,
                run_id=run_id,
                stage_name="post",
                code="review_comment_line_not_in_diff",
                message=(
                    f"Skipped review comment for {file_path}:{line}; line is not "
                    "commentable in the PR diff."
                ),
            )
            continue
        comments.append(
            {
                "path": file_path,
                "line": line,
                "side": "RIGHT",
                "body": body,
            }
        )
    return comments, skipped
