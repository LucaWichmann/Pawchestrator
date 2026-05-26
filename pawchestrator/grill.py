"""Grill action orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.skill_loader import load_skill
from pawchestrator.db import (
    complete_grill_run,
    fail_grill_run,
    lookup_repo_path,
    start_grill_run,
)
from pawchestrator.github import (
    CHECKED_CHECKBOX_RE,
    HEADING_RE,
    PAWCHESTRATOR_LABELS,
    UNCHECKED_CHECKBOX_RE,
    GitHubIssueClient,
    get_gh_token,
)
from pawchestrator.issues import snapshot_issue
from pawchestrator.runners import (
    Runner,
    RunnerFailedError,
    RunnerResult,
    RunnerTask,
    resolve_runner,
    runner_tool_mismatch_warning,
)
from pawchestrator.stage_fallback import (
    run_task_with_usage_limit_fallback,
    usage_limit_fallback_runner,
)

GRILL_REPORT_SCHEMA = "pawchestrator.grill_report.v1"
CRITERIA_DEDUPE_SCHEMA = "pawchestrator.criteria_dedupe.v1"
REQUIRED_TOOLS: list[str] = ["Read", "Glob", "Grep"]
SUGGESTED_CRITERIA_HEADING = "## Pawchestrator Suggested Criteria"
LOGGER = logging.getLogger(__name__)


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
        report = await _publish_report(
            client,
            snapshot,
            report_payload,
            settings=settings,
            run_id=active_run_id,
            repo_path=local_repo_path,
        )

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
        if isinstance(error, RunnerFailedError):
            db_error = error.public_message
        else:
            db_error = "Stage failed. See local run logs."
        await fail_grill_run(
            settings,
            run_id=active_run_id,
            stage_id=stage_id,
            error=db_error,
        )
        raise

    return GrillResult(
        run_id=active_run_id,
        artifact_path=artifact_path,
        log_path=log_path,
        report=report,
    )


_GRILL_FALLBACK = "Analyze this issue for acceptance criteria and return a GrillReport JSON artifact with suggested_criteria and unanswerable_questions."


def build_grill_prompt(
    snapshot: dict[str, Any],
    *,
    codebase_available: bool = True,
    app_dir: Path | None = None,
) -> str:
    mode = (
        "Use your Read, Glob, Grep tools to explore the repository before answering."
        if codebase_available
        else "No local repository is registered. Do not claim codebase facts; list only questions needed to infer criteria."
    )
    instructions = load_skill("IssueGrill", app_dir) or _GRILL_FALLBACK
    data_section = f"""Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}

Issue body:
{snapshot.get("body", "")}

Issue comments:
{_format_prompt_comments(snapshot)}

{mode}"""

    return f"{instructions}\n\n{data_section}"


_DEDUPE_FALLBACK_TASK = "Return only proposed criteria that are genuinely new. Treat paraphrases or same-requirement restatements of existing criteria as duplicates."


def build_dedupe_prompt(
    existing_criteria: list[str],
    proposed_criteria: list[str],
    app_dir: Path | None = None,
) -> str:
    skill_text = load_skill("CriteriaDedupe", app_dir)
    task = " ".join(skill_text.split()) if skill_text else _DEDUPE_FALLBACK_TASK
    payload = {
        "task": task,
        "existing_criteria": existing_criteria,
        "proposed_criteria": proposed_criteria,
        "output_schema": {
            "schema": CRITERIA_DEDUPE_SCHEMA,
            "unique_suggested_criteria": ["string"],
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def dedupe_criteria(
    settings: Settings,
    *,
    run_id: str,
    cwd: Path,
    existing_criteria: list[str],
    proposed_criteria: list[str],
    runner: Runner | None = None,
) -> list[str]:
    normalized_existing = {_normalize_criterion(item) for item in existing_criteria}
    normalized_seen = set(normalized_existing)
    llm_candidates: list[str] = []
    fallback_unique: list[str] = []
    for criterion in proposed_criteria:
        normalized = _normalize_criterion(criterion)
        if not normalized or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        llm_candidates.append(criterion)
        fallback_unique.append(criterion)

    if not llm_candidates:
        return []

    active_runner = runner or resolve_runner(settings, "criteria_dedupe", "claude")
    fallback_runner = usage_limit_fallback_runner(
        settings,
        "criteria_dedupe",
        active_runner,
    )
    task = RunnerTask(
        prompt=build_dedupe_prompt(existing_criteria, llm_candidates, app_dir=settings.app_dir),
        cwd=cwd.resolve(),
        run_id=run_id,
        stage_name="criteria_dedupe",
    )

    try:
        result = await run_task_with_usage_limit_fallback(
            settings=settings,
            run_id=run_id,
            stage_name="criteria_dedupe",
            active_runner=active_runner,
            fallback_runner=fallback_runner,
            task=task,
            log_path=_criteria_dedupe_log_path(settings, run_id),
            write_attempt_log=_write_grill_attempt_log,
            logger=LOGGER,
        )
        return _normalize_dedupe_payload(result.artifact, fallback_unique)
    except Exception as error:
        LOGGER.warning(
            "criteria dedupe runner failed; using normalized dedupe: %s",
            error,
        )
        return fallback_unique


def append_suggested_criteria(body: str, suggested_criteria: list[str]) -> tuple[str, bool]:
    if not suggested_criteria:
        return body, False

    heading_range = _find_suggested_criteria_section(body)
    if heading_range is None:
        rendered = "\n".join(f"- [ ] {criterion}" for criterion in suggested_criteria)
        separator = "\n\n" if body.strip() else ""
        return f"{body.rstrip()}{separator}{SUGGESTED_CRITERIA_HEADING}\n\n{rendered}\n", True

    existing = _suggested_criteria_texts(body, heading_range)
    new_criteria = [criterion for criterion in suggested_criteria if criterion not in existing]
    if not new_criteria:
        return body, False

    return _append_to_suggested_criteria_section(body, heading_range, new_criteria), True


def _format_prompt_comments(snapshot: dict[str, Any]) -> str:
    comments = snapshot.get("comments")
    if not isinstance(comments, list) or not comments:
        return "(none)"

    lines: list[str] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        author = str(comment.get("author") or "unknown")
        comment_body = str(comment.get("body") or "").strip()
        if not comment_body:
            continue
        lines.append(f"- {author}: {comment_body}")
    return "\n".join(lines) if lines else "(none)"


def _find_suggested_criteria_section(body: str) -> tuple[int, int] | None:
    lines = body.splitlines(keepends=True)
    heading_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == SUGGESTED_CRITERIA_HEADING:
            heading_index = index
            break

    if heading_index is None:
        return None

    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if HEADING_RE.match(lines[index]):
            section_end = index
            break

    return heading_index, section_end


def _suggested_criteria_texts(body: str, heading_range: tuple[int, int]) -> set[str]:
    lines = body.splitlines(keepends=True)
    _, section_end = heading_range
    criteria: set[str] = set()
    for line in lines[heading_range[0] + 1 : section_end]:
        checkbox_match = UNCHECKED_CHECKBOX_RE.match(line) or CHECKED_CHECKBOX_RE.match(line)
        if checkbox_match:
            criteria.add(_checkbox_text(line))
    return criteria


def _criteria_texts(body: str) -> list[str]:
    criteria: list[str] = []
    for line in body.splitlines():
        if UNCHECKED_CHECKBOX_RE.match(line) or CHECKED_CHECKBOX_RE.match(line):
            criteria.append(_checkbox_text(line))
    return criteria


def _checkbox_text(line: str) -> str:
    return line.split("]", 1)[1].strip() if "]" in line else line.strip()


def _normalize_criterion(criterion: str) -> str:
    return " ".join(str(criterion).casefold().split())


def _normalize_dedupe_payload(
    artifact: dict[str, Any] | None,
    fallback_unique: list[str],
) -> list[str]:
    if artifact is None:
        raise ValueError("criteria dedupe runner did not return JSON")
    value = artifact.get("unique_suggested_criteria")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("criteria dedupe runner returned invalid JSON schema")

    allowed = {criterion: None for criterion in fallback_unique}
    normalized_allowed = {
        _normalize_criterion(criterion) for criterion in fallback_unique
    }
    unique: list[str] = []
    seen: set[str] = set()
    for criterion in value:
        normalized = _normalize_criterion(criterion)
        if (
            criterion not in allowed
            or normalized not in normalized_allowed
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        unique.append(criterion)
    return unique


def _append_to_suggested_criteria_section(
    body: str,
    heading_range: tuple[int, int],
    new_criteria: list[str],
) -> str:
    lines = body.splitlines(keepends=True)
    _, section_end = heading_range
    before = "".join(lines[:section_end]).rstrip()
    after = "".join(lines[section_end:])
    rendered = "\n".join(f"- [ ] {criterion}" for criterion in new_criteria)
    separator = "\n\n" if _section_has_no_criteria(lines, heading_range) else "\n"
    updated = f"{before}{separator}{rendered}\n"
    if after:
        after = after.lstrip("\r\n")
        updated = f"{updated}\n{after}"
    return updated


def _section_has_no_criteria(lines: list[str], heading_range: tuple[int, int]) -> bool:
    _, section_end = heading_range
    return not any(
        UNCHECKED_CHECKBOX_RE.match(line) or CHECKED_CHECKBOX_RE.match(line)
        for line in lines[heading_range[0] + 1 : section_end]
    )


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

    active_runner = runner or resolve_runner(settings, "grill", "claude")
    _log_tool_mismatch(active_runner)
    fallback_runner = usage_limit_fallback_runner(settings, "grill", active_runner)
    result = await run_task_with_usage_limit_fallback(
        settings=settings,
        run_id=run_id,
        stage_name="grill",
        active_runner=active_runner,
        fallback_runner=fallback_runner,
        task=RunnerTask(
            prompt=build_grill_prompt(snapshot, app_dir=settings.app_dir),
            cwd=local_repo_path.resolve(),
            run_id=run_id,
            stage_name="grill",
        ),
        log_path=log_path,
        write_attempt_log=_write_grill_attempt_log,
        logger=LOGGER,
    )
    return normalize_grill_payload(result.artifact)


def _log_tool_mismatch(runner: Runner) -> None:
    warning = runner_tool_mismatch_warning(
        runner,
        stage_name="grill",
        required_tools=REQUIRED_TOOLS,
    )
    if warning is not None:
        LOGGER.warning(warning)


async def _publish_report(
    client: GitHubIssueClient,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    run_id: str | None = None,
    repo_path: Path | None = None,
    dedupe_runner: Runner | None = None,
) -> GrillReport:
    owner = str(snapshot.get("owner") or "")
    repo = str(snapshot.get("repo") or "")
    number = int(snapshot.get("number") or 0)
    criteria = [str(item) for item in payload["suggested_criteria"]]
    questions = [str(item) for item in payload["unanswerable_questions"]]

    body = str(snapshot.get("body") or "")
    existing_criteria = _criteria_texts(body)
    if settings is not None and run_id is not None and repo_path is not None:
        criteria = await dedupe_criteria(
            settings,
            run_id=run_id,
            cwd=repo_path,
            existing_criteria=existing_criteria,
            proposed_criteria=criteria,
            runner=dedupe_runner,
        )

    updated_body, body_updated = append_suggested_criteria(body, criteria)
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


def _criteria_dedupe_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "criteria_dedupe.log"


def _write_grill_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )


def _write_grill_attempt_log(
    log_path: Path,
    runner_id: str,
    result: RunnerResult,
    *,
    append: bool,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    chunk = (
        f"[{runner_id} stdout]\n{result.stdout}\n"
        f"[{runner_id} stderr]\n{result.stderr}\n"
    )
    if append:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(chunk)
        return
    log_path.write_text(chunk, encoding="utf-8")
