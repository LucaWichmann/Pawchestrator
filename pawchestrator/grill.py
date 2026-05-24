"""Grill action orchestration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_grill_run,
    fail_grill_run,
    lookup_repo_path,
    start_grill_run,
)
from pawchestrator.github import (
    PAWCHESTRATOR_LABELS,
    GitHubIssueClient,
    get_gh_token,
)
from pawchestrator.issues import snapshot_issue
from pawchestrator.runners import Runner, RunnerTask, resolve_runner

GRILL_REPORT_SCHEMA = "pawchestrator.grill_report.v1"
SUGGESTED_CRITERIA_HEADING = "## Pawchestrator Suggested Criteria"


@dataclass(frozen=True)
class GrillReport:
    schema: str
    status: str
    suggested_criteria: list[str]
    unanswerable_questions: list[str]
    body_updated: bool
    comment_posted: bool
    comment_id: int | None


@dataclass(frozen=True)
class GrillResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    report: GrillReport


async def run_grill(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str | None = None,
    repo_path: Path | None = None,
    runner: Runner | None = None,
    github_client: GitHubIssueClient | None = None,
) -> GrillResult:
    if run_id is not None and _snapshot_artifact_path(settings, run_id).exists():
        active_run_id = run_id
    else:
        snapshot_result = await snapshot_issue(issue_url, settings, run_id=run_id)
        active_run_id = snapshot_result.run_id
    snapshot_path = _snapshot_artifact_path(settings, active_run_id)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    stage_id = await start_grill_run(settings, run_id=active_run_id)
    artifact_path = _grill_artifact_path(settings, active_run_id)
    log_path = _grill_log_path(settings, active_run_id)

    try:
        local_repo_path = await _resolve_repo_path(settings, snapshot, repo_path)
        report_payload = await _build_report_payload(
            active_run_id,
            settings,
            snapshot,
            local_repo_path=local_repo_path,
            runner=runner,
            log_path=log_path,
        )
        client = github_client or GitHubIssueClient(get_gh_token())
        report = await _publish_report(client, snapshot, report_payload)

        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await complete_grill_run(
            settings,
            run_id=active_run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        if not log_path.exists():
            _write_grill_log(log_path, "", str(error))
        await fail_grill_run(
            settings,
            run_id=active_run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return GrillResult(
        run_id=active_run_id,
        artifact_path=artifact_path,
        log_path=log_path,
        report=report,
    )


def build_grill_prompt(
    snapshot: dict[str, Any],
    *,
    codebase_available: bool = True,
) -> str:
    mode = (
        "Use your Read, Glob, Grep tools to explore the repository before answering."
        if codebase_available
        else "No local repository is registered. Do not claim codebase facts; list only questions needed to infer criteria."
    )
    return f"""You are grilling a GitHub issue for precise acceptance criteria.

Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}

Issue body:
{snapshot.get("body", "")}

{mode}

Return a JSON object matching this schema exactly:
{{
  "schema": "pawchestrator.grill_report.v1",
  "status": "success" | "needs_info" | "error",
  "suggested_criteria": ["string"],
  "unanswerable_questions": ["string"]
}}

Suggested criteria must be concrete, testable bullets inferred from the issue and, when available, codebase context.
Only include questions that cannot be answered from the issue or repository context.
Return minimal valid JSON. No prose outside JSON fields.
"""


def append_suggested_criteria(body: str, suggested_criteria: list[str]) -> tuple[str, bool]:
    if SUGGESTED_CRITERIA_HEADING in body:
        return body, False
    if not suggested_criteria:
        return body, False

    rendered = "\n".join(f"- [ ] {criterion}" for criterion in suggested_criteria)
    separator = "\n\n" if body.strip() else ""
    return f"{body.rstrip()}{separator}{SUGGESTED_CRITERIA_HEADING}\n\n{rendered}\n", True


def normalize_grill_payload(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("Claude did not return a JSON artifact")
    criteria = [str(item) for item in _list_value(artifact.get("suggested_criteria"))]
    questions = [str(item) for item in _list_value(artifact.get("unanswerable_questions"))]
    return {
        "schema": str(artifact.get("schema") or GRILL_REPORT_SCHEMA),
        "status": str(artifact.get("status") or ("needs_info" if questions else "success")),
        "suggested_criteria": criteria,
        "unanswerable_questions": questions,
    }


async def _build_report_payload(
    run_id: str,
    settings: Settings,
    snapshot: dict[str, Any],
    *,
    local_repo_path: Path | None,
    runner: Runner | None,
    log_path: Path,
) -> dict[str, Any]:
    if local_repo_path is None:
        return {
            "schema": GRILL_REPORT_SCHEMA,
            "status": "needs_info",
            "suggested_criteria": [],
            "unanswerable_questions": [
                "Which local repository should Pawchestrator inspect for this issue?"
            ],
        }

    # ClaudeRunner enforces grill read-only tools in runner config resolution.
    # CodexRunner has no tool allowlist equivalent, so assigning codex to grill
    # intentionally removes that read-only guarantee.
    active_runner = runner or resolve_runner(settings, "grill", "claude")
    healthy, message = await active_runner.check_health()
    if not healthy:
        raise RuntimeError(message)

    result = await active_runner.run_task(
        RunnerTask(
            prompt=build_grill_prompt(snapshot),
            cwd=local_repo_path.resolve(),
            run_id=run_id,
            stage_name="grill",
        )
    )
    _write_grill_log(log_path, result.stdout, result.stderr)
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Claude runner failed"
        raise RuntimeError(detail)
    return normalize_grill_payload(result.artifact)


async def _publish_report(
    client: GitHubIssueClient,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
) -> GrillReport:
    owner = str(snapshot.get("owner") or "")
    repo = str(snapshot.get("repo") or "")
    number = int(snapshot.get("number") or 0)
    criteria = [str(item) for item in payload["suggested_criteria"]]
    questions = [str(item) for item in payload["unanswerable_questions"]]

    updated_body, body_updated = append_suggested_criteria(
        str(snapshot.get("body") or ""),
        criteria,
    )
    if body_updated:
        await client.patch_issue_body(owner, repo, number, updated_body)

    comment_id: int | None = None
    label_name, _ = PAWCHESTRATOR_LABELS["needs-info"]
    if questions:
        comment_id = await client.post_comment(
            owner,
            repo,
            number,
            _format_questions_comment(questions),
        )
        await client.add_label(owner, repo, number, label_name)
    else:
        await client.remove_label(owner, repo, number, label_name)

    return GrillReport(
        schema=GRILL_REPORT_SCHEMA,
        status=str(payload.get("status") or ("needs_info" if questions else "success")),
        suggested_criteria=criteria,
        unanswerable_questions=questions,
        body_updated=body_updated,
        comment_posted=bool(questions),
        comment_id=comment_id,
    )


async def _resolve_repo_path(
    settings: Settings,
    snapshot: dict[str, Any],
    repo_path: Path | None,
) -> Path | None:
    if repo_path is not None:
        return repo_path.resolve()
    registered = await lookup_repo_path(
        settings,
        owner=str(snapshot.get("owner") or ""),
        repo=str(snapshot.get("repo") or ""),
    )
    return registered.resolve() if registered is not None else None


def _format_questions_comment(questions: list[str]) -> str:
    lines = ["## Pawchestrator questions", ""]
    lines.extend(f"- {question}" for question in questions)
    return "\n".join(lines)


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _grill_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "grill_report.json"


def _grill_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "grill.log"


def _write_grill_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )
