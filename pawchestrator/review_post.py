"""Submit review artifacts to GitHub pull request reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    get_run_state,
    insert_run_warning,
    lookup_repo_path,
)
from pawchestrator.github import (
    GitHubIssueClient,
    PAWCHESTRATOR_LABELS,
    get_gh_token,
    parse_commentable_added_lines,
    with_generated_attribution,
)
from pawchestrator.review import REVIEW_VERDICTS, fetch_pr_diff, review_report_path
from pawchestrator.stage_lifecycle import run_stage_lifecycle


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

    async def body(_log_path: Path) -> tuple[dict[str, Any], None]:
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
        review_body = str(report["summary"])
        review_event = str(report["verdict"])
        downgraded_verdict: str | None = None
        if review_event in {"REQUEST_CHANGES", "APPROVE"}:
            author, authenticated_user = await _fetch_review_identities(
                active_client,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
            )
            if author.lower() == authenticated_user.lower():
                downgraded_verdict = review_event
                review_event = "COMMENT"
                review_body = f"Pawchestrator verdict: {downgraded_verdict}\n\n{review_body}"
                await insert_run_warning(
                    settings,
                    run_id=run_id,
                    stage_name="post",
                    code="review_verdict_downgraded_for_self_pr",
                    message=(
                        f"Submitted {downgraded_verdict} review as COMMENT because "
                        "GitHub rejects approving or requesting changes on your own PR."
                    ),
                )
        review_body = with_generated_attribution(review_body)
        review_id = await active_client.post_pr_review(
            owner,
            repo,
            pr_number,
            body=review_body,
            event=review_event,
            comments=comments,
        )
        if downgraded_verdict is not None:
            await _sync_self_review_labels(
                settings,
                active_client,
                run_id=run_id,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                verdict=downgraded_verdict,
            )
        return {
            "submitted_comments": len(comments),
            "skipped_comments": skipped,
            "review_id": review_id,
        }, None

    result = await run_stage_lifecycle(settings, run_id, "post", body)

    return ReviewPostResult(
        run_id=run_id,
        submitted_comments=int(result.report["submitted_comments"]),
        skipped_comments=int(result.report["skipped_comments"]),
        review_id=(
            None if result.report["review_id"] is None else int(result.report["review_id"])
        ),
    )


async def _fetch_review_identities(
    client: GitHubIssueClient,
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> tuple[str, str]:
    return await client.fetch_pr_author_login(
        owner,
        repo,
        pr_number,
    ), await client.fetch_authenticated_user_login()


async def _sync_self_review_labels(
    settings: Settings,
    client: GitHubIssueClient,
    *,
    run_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    verdict: str,
) -> None:
    changes_label = PAWCHESTRATOR_LABELS["review-changes-requested"][0]
    approved_label = PAWCHESTRATOR_LABELS["review-approved"][0]
    add_label = changes_label if verdict == "REQUEST_CHANGES" else approved_label
    remove_label = approved_label if verdict == "REQUEST_CHANGES" else changes_label
    try:
        await client.add_label(owner, repo, pr_number, add_label)
        await client.remove_label(owner, repo, pr_number, remove_label)
    except Exception as error:
        await insert_run_warning(
            settings,
            run_id=run_id,
            stage_name="post",
            code="review_verdict_label_sync_failed",
            message=f"Could not sync self-review verdict labels: {error}",
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
                "body": with_generated_attribution(body),
            }
        )
    return comments, skipped
