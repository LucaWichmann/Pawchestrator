"""Scout stage orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings, StageSettings
from pawchestrator.db import (
    complete_scout_run,
    fail_scout_run,
    get_run_state,
    insert_run_warning,
    start_scout_run,
)
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerResult,
    RunnerTask,
    claude_usage_limit_exhausted,
    resolve_runner,
    runner_tool_mismatch_warning,
)

SCOUT_REPORT_SCHEMA = "pawchestrator.scout_report.v1"
REQUIRED_TOOLS: list[str] = ["Read", "Glob", "Grep"]
MAX_PROMPT_COMMENTS = 10
MAX_PROMPT_COMMENT_BODY_CHARS = 400
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoutResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    report: dict[str, Any]


async def run_scout(
    run_id: str,
    settings: Settings,
    *,
    repo_path: Path | None = None,
    runner: Runner | None = None,
) -> ScoutResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    snapshot_path = _snapshot_artifact_path(settings, run_id)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"issue snapshot not found: {snapshot_path}")

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    local_repo_path = (repo_path or Path.cwd()).resolve()
    stage_id = await start_scout_run(settings, run_id=run_id)
    active_runner = runner or resolve_runner(settings, "scout", "claude")
    _log_tool_mismatch(active_runner)
    fallback_runner = _usage_limit_fallback_runner(settings, active_runner)
    log_path = _scout_log_path(settings, run_id)
    artifact_path = _scout_artifact_path(settings, run_id)
    prompt = build_scout_prompt(snapshot)

    try:
        task = RunnerTask(
            prompt=prompt,
            cwd=local_repo_path,
            run_id=run_id,
            stage_name="scout",
        )
        result = await _run_checked_runner(active_runner, task)
        _write_scout_attempt_log(log_path, active_runner.id, result, append=False)

        if result.exit_code != 0:
            detail = _runner_failure_detail(result, active_runner.id)
            if fallback_runner is None or not claude_usage_limit_exhausted(result):
                raise RuntimeError(detail)

            warning_message = "Claude usage limit exhausted; using Codex for scout."
            await insert_run_warning(
                settings,
                run_id=run_id,
                stage_name="scout",
                code="scout_usage_limit_fallback",
                message=warning_message,
            )
            LOGGER.warning(warning_message)
            try:
                fallback_result = await _run_checked_runner(fallback_runner, task)
                _write_scout_attempt_log(
                    log_path,
                    fallback_runner.id,
                    fallback_result,
                    append=True,
                )
                if fallback_result.exit_code != 0:
                    fallback_detail = _runner_failure_detail(
                        fallback_result,
                        fallback_runner.id,
                    )
                    raise RuntimeError(
                        f"{fallback_detail}\n"
                        f"Original Claude usage-limit failure: {detail}"
                    )
                result = fallback_result
            except Exception as fallback_error:
                if "Original Claude usage-limit failure" in str(fallback_error):
                    raise
                raise RuntimeError(
                    f"{fallback_error}\n"
                    f"Original Claude usage-limit failure: {detail}"
                ) from fallback_error

        report = normalize_scout_report(result.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await complete_scout_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        if not log_path.exists():
            _write_scout_log(log_path, "", str(error))
        await fail_scout_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return ScoutResult(
        run_id=run_id,
        artifact_path=artifact_path,
        log_path=log_path,
        report=report,
    )


async def _run_checked_runner(runner: Runner, task: RunnerTask) -> RunnerResult:
    healthy, message = await runner.check_health()
    if not healthy:
        raise RuntimeError(message)
    return await runner.run_task(task)


def _usage_limit_fallback_runner(
    settings: Settings,
    primary_runner: Runner,
) -> Runner | None:
    if not isinstance(primary_runner, ClaudeRunner):
        return None

    stage_settings = settings.stages.get("scout")
    fallback = (
        stage_settings.usage_limit_fallback_runner
        if stage_settings is not None
        else None
    )
    if fallback == "none":
        return None
    if fallback not in {None, "codex"}:
        return None

    stage_overrides = dict(settings.stages)
    scout_settings = stage_settings or StageSettings()
    fallback_codex = scout_settings.codex.model_copy(
        update={"sandbox": "read-only", "bypass_sandbox": False}
    )
    stage_overrides["scout"] = scout_settings.model_copy(
        update={"runner": "codex", "codex": fallback_codex}
    )
    return CodexRunner(
        settings.runners.codex,
        debug=settings.debug,
        stage_overrides=stage_overrides,
    )


def _runner_failure_detail(result: RunnerResult, runner_id: str) -> str:
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or f"{runner_id.capitalize()} runner failed"
    )


def _log_tool_mismatch(runner: Runner) -> None:
    warning = runner_tool_mismatch_warning(
        runner,
        stage_name="scout",
        required_tools=REQUIRED_TOOLS,
    )
    if warning is not None:
        LOGGER.warning(warning)


def build_scout_prompt(snapshot: dict[str, Any]) -> str:
    comments = _prompt_comments(snapshot.get("comments"))
    rendered_comments = "\n\n".join(_render_comment(comment) for comment in comments)
    if not rendered_comments:
        rendered_comments = "(none)"

    return f"""You are scouting a GitHub issue for implementation readiness.

Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}

Issue body:
{snapshot.get("body", "")}

Comments:
{rendered_comments}

Analyze this issue and return a JSON object matching this schema exactly:
{{
  "schema": "pawchestrator.scout_report.v1",
  "status": "success" | "error",
  "readiness": "ready" | "needs_info" | "blocked",
  "risk": "low" | "medium" | "high",
  "findings": [{{"kind": "string", "text": "string"}}],
  "risks": [{{"level": "string", "text": "string"}}],
  "next_recommended_stage": "grill" | "plan" | "implement"
}}

Use your Read, Glob, Grep tools to explore the repository as needed.
Be terse. Return minimal valid JSON. No prose outside JSON fields.
"""


def normalize_scout_report(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("Claude did not return a JSON artifact")
    findings = _list_value(artifact.get("findings"))
    if not findings:
        raise ValueError("Scout report missing required findings")

    return {
        "schema": str(artifact.get("schema") or SCOUT_REPORT_SCHEMA),
        "status": str(artifact.get("status") or "success"),
        "readiness": str(artifact.get("readiness") or "needs_info"),
        "risk": str(artifact.get("risk") or "medium"),
        "findings": findings,
        "risks": _list_value(artifact.get("risks")),
        "next_recommended_stage": str(
            artifact.get("next_recommended_stage") or "grill"
        ),
    }


def _render_comment(comment: object) -> str:
    if not isinstance(comment, dict):
        return str(comment)
    author = comment.get("author") or "unknown"
    created_at = comment.get("created_at") or "unknown time"
    body = comment.get("body") or ""
    return f"{author} at {created_at}:\n{body}"


def _prompt_comments(value: object) -> list[object]:
    comments = _list_value(value)[:MAX_PROMPT_COMMENTS]
    return [_truncate_comment_body(comment) for comment in comments]


def _truncate_comment_body(comment: object) -> object:
    if not isinstance(comment, dict):
        return comment
    compressed = dict(comment)
    body = str(compressed.get("body") or "")
    if len(body) > MAX_PROMPT_COMMENT_BODY_CHARS:
        compressed["body"] = "[truncated]" + body[:MAX_PROMPT_COMMENT_BODY_CHARS]
    return compressed


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _scout_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "scout_report.json"


def _scout_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "scout.log"


def _write_scout_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )


def _write_scout_attempt_log(
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
