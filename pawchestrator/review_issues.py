"""Formatting helpers for review-suggested GitHub issues."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.github import (
    GitHubIssueClient,
    get_gh_token,
    with_generated_attribution,
)
from pawchestrator.runners import Runner, RunnerTask, resolve_runner
from pawchestrator.stage_fallback import (
    run_task_with_usage_limit_fallback,
    usage_limit_fallback_runner,
)

REVIEW_ISSUE_FORMAT_SCHEMA = "pawchestrator.review_issue_format.v1"
REVIEW_ISSUE_FORMAT_STAGE = "review_issue_format"
SOURCE_SNIPPET_CONTEXT_LINES = 15
MAX_ISSUE_TITLE_LENGTH = 256
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormattedReviewIssue:
    title: str
    body: str


async def format_and_create_issues(
    settings: Settings,
    *,
    run_id: str,
    cwd: Path,
    owner: str,
    repo: str,
    pr_summary: str,
    suggested_issues: list[dict[str, Any]],
    inline_comments: list[dict[str, Any]],
    artifact_path: Path,
    write_created_issues_report,
    created_issue_urls: list[str] | None = None,
    repo_path: Path | None = None,
    client: GitHubIssueClient | None = None,
) -> list[str]:
    inline_comments_by_anchor = {
        (comment.get("file"), comment.get("line")): comment
        for comment in inline_comments
        if isinstance(comment, dict)
    }

    format_tasks = []
    for issue in suggested_issues:
        hint = _require_non_empty_str(issue.get("hint"), "suggested issue hint")
        file = _require_non_empty_str(issue.get("file"), "suggested issue file")
        line = _require_positive_int(issue.get("line"), "suggested issue line")
        inline_comment = inline_comments_by_anchor.get((file, line))
        if inline_comment is None:
            raise ValueError("suggested issue file and line must match an inline comment")
        format_tasks.append(
            review_issue_format(
                settings,
                run_id=run_id,
                cwd=cwd,
                hint=hint,
                pr_summary=pr_summary,
                inline_comment=inline_comment,
                repo_path=repo_path,
            )
        )

    formatted_issues = await asyncio.gather(*format_tasks)
    github_client = client or GitHubIssueClient(get_gh_token())
    if created_issue_urls is None:
        created_issue_urls = []
    for issue in formatted_issues:
        issue_url = await github_client.create_issue(
            owner,
            repo,
            title=issue.title,
            body=issue.body,
        )
        created_issue_urls.append(issue_url)
        write_created_issues_report(artifact_path, created_issue_urls)
    return created_issue_urls


def fetch_source_snippet(
    repo_path: Path | None,
    file: str,
    line: int,
    *,
    context_lines: int = SOURCE_SNIPPET_CONTEXT_LINES,
) -> str | None:
    if repo_path is None:
        return None
    if line < 1:
        return None

    source_path = repo_path / file
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None

    start_line = max(1, line - context_lines)
    end_line = min(len(lines), line + context_lines)
    width = len(str(end_line))
    return "\n".join(
        f"{line_number:>{width}} | {lines[line_number - 1]}"
        for line_number in range(start_line, end_line + 1)
    )


def build_review_issue_prompt(
    *,
    hint: str,
    pr_summary: str,
    inline_comment_body: str,
    source_snippet: str | None,
) -> str:
    payload = {
        "task": (
            "Format a GitHub follow-up issue from the review suggestion. "
            "Return only the structured JSON artifact. Do not include Markdown framing."
        ),
        "hint": hint,
        "pr_summary": pr_summary,
        "inline_comment_body": inline_comment_body,
        "source_snippet": source_snippet,
        "output_schema": {
            "schema": REVIEW_ISSUE_FORMAT_SCHEMA,
            "title": "string",
            "problem": "string",
            "acceptance_criteria": ["string"],
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def review_issue_format(
    settings: Settings,
    *,
    run_id: str,
    cwd: Path,
    hint: str,
    pr_summary: str,
    inline_comment: dict[str, Any],
    repo_path: Path | None,
    runner: Runner | None = None,
) -> FormattedReviewIssue:
    file = _require_non_empty_str(inline_comment.get("file"), "inline comment file")
    line = _require_positive_int(inline_comment.get("line"), "inline comment line")
    inline_comment_body = _require_non_empty_str(
        inline_comment.get("body"),
        "inline comment body",
    )
    source_snippet = fetch_source_snippet(repo_path, file, line)

    active_runner = runner or resolve_runner(settings, REVIEW_ISSUE_FORMAT_STAGE, "claude")
    fallback_runner = usage_limit_fallback_runner(
        settings,
        REVIEW_ISSUE_FORMAT_STAGE,
        active_runner,
    )
    task = RunnerTask(
        prompt=build_review_issue_prompt(
            hint=hint,
            pr_summary=pr_summary,
            inline_comment_body=inline_comment_body,
            source_snippet=source_snippet,
        ),
        cwd=cwd.resolve(),
        run_id=run_id,
        stage_name=REVIEW_ISSUE_FORMAT_STAGE,
    )
    result = await run_task_with_usage_limit_fallback(
        settings=settings,
        run_id=run_id,
        stage_name=REVIEW_ISSUE_FORMAT_STAGE,
        active_runner=active_runner,
        fallback_runner=fallback_runner,
        task=task,
        log_path=_review_issue_format_log_path(settings, run_id),
        write_attempt_log=_write_review_issue_format_attempt_log,
        logger=LOGGER,
    )
    formatted = validate_review_issue_response(result.artifact)
    title = formatted["title"][:MAX_ISSUE_TITLE_LENGTH]
    body = assemble_issue_body(
        file=file,
        line=line,
        problem=formatted["problem"],
        acceptance_criteria=formatted["acceptance_criteria"],
    )
    return FormattedReviewIssue(title=title, body=body)


def validate_review_issue_response(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("review issue formatter did not produce a structured artifact")

    title = artifact.get("title")
    if not isinstance(title, str):
        raise ValueError("review issue formatter title must be a string")

    problem = artifact.get("problem")
    if not isinstance(problem, str):
        raise ValueError("review issue formatter problem must be a string")

    acceptance_criteria = artifact.get("acceptance_criteria")
    if not isinstance(acceptance_criteria, list) or not all(
        isinstance(item, str) for item in acceptance_criteria
    ):
        raise ValueError(
            "review issue formatter acceptance_criteria must be a list of strings"
        )

    return {
        "title": title,
        "problem": problem,
        "acceptance_criteria": acceptance_criteria,
    }


def assemble_issue_body(
    *,
    file: str,
    line: int,
    problem: str,
    acceptance_criteria: list[str],
) -> str:
    criteria = "\n".join(f"- [ ] {criterion}" for criterion in acceptance_criteria)
    body = f"**Where:** `{file}:{line}`\n\n{problem}\n\n## Acceptance Criteria"
    if criteria:
        body = f"{body}\n\n{criteria}"
    return with_generated_attribution(body)


def _review_issue_format_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "review_issue_format.log"


def _write_review_issue_format_attempt_log(
    path: Path,
    runner_id: str,
    result: Any,
    append: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as file_obj:
        file_obj.write(f"runner: {runner_id}\n")
        file_obj.write(f"exit_code: {result.exit_code}\n")
        if result.stdout:
            file_obj.write("\nstdout:\n")
            file_obj.write(result.stdout)
            file_obj.write("\n")
        if result.stderr:
            file_obj.write("\nstderr:\n")
            file_obj.write(result.stderr)
            file_obj.write("\n")


def _require_non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value
