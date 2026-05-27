"""Pull request review stage orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    get_run_state,
    lookup_repo_path,
)
from pawchestrator.runners import (
    Runner,
    RunnerFailedError,
    RunnerTask,
    resolve_review_runner,
)
from pawchestrator.github import parse_commentable_added_lines
from pawchestrator.skill_loader import load_skill
from pawchestrator.stage_lifecycle import StageResult, run_stage_lifecycle

REVIEW_REPORT_SCHEMA = "pawchestrator.review_report.v1"
REVIEW_VERDICTS = frozenset({"REQUEST_CHANGES", "APPROVE", "COMMENT"})
_REVIEW_FALLBACK = """Review GitHub PR -> JSON only:
{
  "inline_comments": [{"file": "path/to/file", "line": 123, "body": "comment"}],
  "summary": "short review summary",
  "verdict": "REQUEST_CHANGES|APPROVE|COMMENT",
  "suggested_issues": [{"hint": "optional follow-up issue hint", "file": "path/to/file", "line": 123}]
}

Rules: inline_comments changed lines only. Copy `file` + `line` exactly from Commentable added lines. Do not use diff positions, hunk offsets, or raw-diff line counts as `line`.
Each suggested_issues entry must copy `file` + `line` from a matching inline_comments entry.

Verdict: REQUEST_CHANGES = correctness | safety | data-loss | test-blocking. APPROVE = no actionable issues. COMMENT = non-blocking feedback.
No prose. No progress updates. Emit valid JSON artifact only."""


@dataclass(frozen=True)
class ReviewContext:
    description: str
    diff: str


async def run_review(
    run_id: str,
    settings: Settings,
    *,
    implement_runner: str | None = None,
    runner: Runner | None = None,
) -> StageResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    artifact_path = review_report_path(settings, run_id)

    async def body(_log_path: Path) -> tuple[dict[str, Any], Path]:
        owner = str(state["owner"])
        repo = str(state["repo"])
        pr_number = int(state["pr_number"])
        repo_path = await lookup_repo_path(settings, owner=owner, repo=repo)
        cwd = repo_path or Path.cwd()
        context = await fetch_review_context(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            cwd=cwd,
        )
        selected_runner = runner or await resolve_review_runner(
            settings,
            implement_runner,
        )
        result = await selected_runner.run_task(
            RunnerTask(
                prompt=build_review_prompt(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    description=context.description,
                    diff=context.diff,
                    app_dir=settings.app_dir,
                ),
                cwd=cwd,
                run_id=run_id,
                stage_name="review",
            )
        )
        if result.exit_code != 0:
            raise RunnerFailedError(
                public_message="Review agent failed. See local run logs.",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        report = parse_review_artifact(result.artifact)
        write_review_report(artifact_path, report)
        return report, artifact_path

    return await run_stage_lifecycle(settings, run_id, "review", body)


async def fetch_review_context(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    cwd: Path,
) -> ReviewContext:
    description, diff = await asyncio.gather(
        fetch_pr_description(owner=owner, repo=repo, pr_number=pr_number, cwd=cwd),
        fetch_pr_diff(owner=owner, repo=repo, pr_number=pr_number, cwd=cwd),
    )
    return ReviewContext(description=description, diff=diff)


async def fetch_pr_description(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    cwd: Path,
) -> str:
    stdout = await _run_gh_checked(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            f"{owner}/{repo}",
            "--json",
            "title,body",
        ],
        cwd,
    )
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("gh pr view did not return a JSON object")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    return f"# {title}\n\n{body}".strip()


async def fetch_pr_diff(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    cwd: Path,
) -> str:
    return await _run_gh_checked(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}"],
        cwd,
    )


def build_review_prompt(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    description: str,
    diff: str,
    app_dir: Path | None = None,
) -> str:
    commentable_lines = _render_commentable_added_lines(diff)
    instructions = load_skill("PullRequestReview", app_dir) or _REVIEW_FALLBACK
    data_section = f"""Pull request: {owner}/{repo}#{pr_number}

PR description:
{description}

Commentable added lines:
{commentable_lines}

PR diff:
{diff}
"""
    return f"{instructions}\n\n{data_section}"


def parse_review_artifact(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("review agent did not produce a structured artifact")

    inline_comments = artifact.get("inline_comments")
    if not isinstance(inline_comments, list):
        raise ValueError("review artifact inline_comments must be a list")

    normalized_comments: list[dict[str, Any]] = []
    for comment in inline_comments:
        if not isinstance(comment, dict):
            raise ValueError("review artifact inline_comments entries must be objects")
        file_path = comment.get("file")
        line = comment.get("line")
        body = comment.get("body")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("review inline comment file must be a non-empty string")
        if not isinstance(line, int) or line <= 0:
            raise ValueError("review inline comment line must be a positive integer")
        if not isinstance(body, str) or not body:
            raise ValueError("review inline comment body must be a non-empty string")
        normalized_comments.append({"file": file_path, "line": line, "body": body})

    summary = artifact.get("summary")
    if not isinstance(summary, str) or not summary:
        raise ValueError("review artifact summary must be a non-empty string")

    verdict = artifact.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(
            "review artifact verdict must be one of REQUEST_CHANGES, APPROVE, COMMENT"
        )

    comment_anchors = {
        (comment["file"], comment["line"]) for comment in normalized_comments
    }
    suggested_issues = artifact.get("suggested_issues")
    if not isinstance(suggested_issues, list):
        raise ValueError("review artifact suggested_issues must be a list")
    normalized_issues: list[dict[str, Any]] = []
    for issue in suggested_issues:
        if not isinstance(issue, dict):
            raise ValueError("review artifact suggested_issues entries must be objects")
        hint = issue.get("hint")
        file_path = issue.get("file")
        line = issue.get("line")
        if not isinstance(hint, str) or not hint:
            raise ValueError("review suggested issue hint must be a non-empty string")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("review suggested issue file must be a non-empty string")
        if not isinstance(line, int) or line <= 0:
            raise ValueError("review suggested issue line must be a positive integer")
        if (file_path, line) not in comment_anchors:
            raise ValueError(
                "review suggested issue file and line must match an inline comment"
            )
        normalized_issues.append({"hint": hint, "file": file_path, "line": line})

    return {
        "schema": REVIEW_REPORT_SCHEMA,
        "inline_comments": normalized_comments,
        "summary": summary,
        "verdict": verdict,
        "suggested_issues": normalized_issues,
    }


def write_review_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def review_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "review_report.json"


def _render_commentable_added_lines(diff: str) -> str:
    lines = parse_commentable_added_lines(diff)
    if not lines:
        return "(none)"
    rendered: list[str] = []
    for line in lines:
        rendered.append(f"{line['path']}:{line['line']} | {line['text']}")
    return "\n".join(rendered)


async def _run_gh_checked(cmd: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"{cmd[0]} command failed"
        raise RuntimeError(detail)
    return stdout
