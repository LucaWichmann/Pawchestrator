"""Pull request review stage orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_review_run,
    fail_review_run,
    get_run_state,
    lookup_repo_path,
    start_review_run,
)
from pawchestrator.runners import (
    Runner,
    RunnerFailedError,
    RunnerTask,
    resolve_review_runner,
)

REVIEW_REPORT_SCHEMA = "pawchestrator.review_report.v1"
REVIEW_VERDICTS = frozenset({"REQUEST_CHANGES", "APPROVE", "COMMENT"})


@dataclass(frozen=True)
class ReviewContext:
    description: str
    diff: str


@dataclass(frozen=True)
class ReviewResult:
    run_id: str
    artifact_path: Path
    report: dict[str, Any]
    runner_id: str


async def run_review(
    run_id: str,
    settings: Settings,
    *,
    implement_runner: str | None = None,
    runner: Runner | None = None,
) -> ReviewResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    stage_id = await start_review_run(settings, run_id=run_id)
    artifact_path = review_report_path(settings, run_id)

    try:
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
        await complete_review_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        db_error = (
            error.public_message
            if isinstance(error, RunnerFailedError)
            else "Stage failed. See local run logs."
        )
        await fail_review_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=db_error,
        )
        raise

    return ReviewResult(
        run_id=run_id,
        artifact_path=artifact_path,
        report=report,
        runner_id=selected_runner.id,
    )


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
) -> str:
    return f"""Review pull request {owner}/{repo}#{pr_number}.

Use the PR description and diff below. Produce only a valid JSON object with this
exact shape:
{{
  "inline_comments": [{{"file": "path/to/file", "line": 123, "body": "comment"}}],
  "summary": "short review summary",
  "verdict": "REQUEST_CHANGES|APPROVE|COMMENT",
  "suggested_issues": ["optional follow-up issue titles"]
}}

Rules:
- `verdict` must be one of `REQUEST_CHANGES`, `APPROVE`, or `COMMENT`.
- `inline_comments` must contain review comments tied to changed file lines only.
- Use `REQUEST_CHANGES` for correctness, safety, data-loss, or test-blocking problems.
- Use `APPROVE` only when no actionable issues remain.
- Use `COMMENT` for non-blocking feedback.

PR description:
{description}

PR diff:
{diff}
"""


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

    suggested_issues = artifact.get("suggested_issues")
    if not isinstance(suggested_issues, list) or not all(
        isinstance(issue, str) for issue in suggested_issues
    ):
        raise ValueError("review artifact suggested_issues must be a list of strings")

    return {
        "schema": REVIEW_REPORT_SCHEMA,
        "inline_comments": normalized_comments,
        "summary": summary,
        "verdict": verdict,
        "suggested_issues": suggested_issues,
    }


def write_review_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def review_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "review_report.json"


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
