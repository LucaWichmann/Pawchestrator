"""Implementation plan stage orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_plan_run,
    fail_plan_run,
    get_run_state,
    start_plan_run,
)
from pawchestrator.runners import (
    Runner,
    RunnerTask,
    resolve_runner,
    runner_tool_mismatch_warning,
)
from pawchestrator.stage_fallback import (
    runner_failure_detail,
    run_checked_runner,
    run_task_with_usage_limit_fallback,
    usage_limit_fallback_runner,
)

IMPLEMENTATION_PLAN_SCHEMA = "pawchestrator.implementation_plan.v1"
REQUIRED_TOOLS: list[str] = ["Read", "Glob", "Grep"]
VALID_RISKS = {"low", "medium", "high"}
MAX_PROMPT_FINDINGS = 5
MAX_PROMPT_RISKS = 5
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImplementationPlanResult:
    run_id: str
    artifact_path: Path
    log_path: Path
    plan: dict[str, Any]


async def run_plan(
    run_id: str,
    settings: Settings,
    *,
    repo_path: Path | None = None,
    runner: Runner | None = None,
) -> ImplementationPlanResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    snapshot_path = _snapshot_artifact_path(settings, run_id)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"issue snapshot not found: {snapshot_path}")

    scout_path = _scout_artifact_path(settings, run_id)
    if not scout_path.exists():
        raise FileNotFoundError(f"scout report not found: {scout_path}")

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    scout_report = json.loads(scout_path.read_text(encoding="utf-8"))
    local_repo_path = (repo_path or Path.cwd()).resolve()
    stage_id = await start_plan_run(settings, run_id=run_id)
    active_runner = runner or resolve_runner(settings, "plan", "claude")
    _log_tool_mismatch(active_runner)
    fallback_runner = usage_limit_fallback_runner(settings, "plan", active_runner)
    log_path = _plan_log_path(settings, run_id)
    artifact_path = _plan_artifact_path(settings, run_id)
    task = RunnerTask(
        prompt=build_plan_prompt(snapshot, scout_report),
        cwd=local_repo_path,
        run_id=run_id,
        stage_name="plan",
    )

    try:
        if fallback_runner is None:
            result = await run_checked_runner(active_runner, task)
            _write_plan_attempt_log(
                log_path,
                active_runner.id,
                result,
                append=False,
            )
            if result.exit_code != 0:
                raise RuntimeError(runner_failure_detail(result, active_runner.id))
        else:
            result = await run_task_with_usage_limit_fallback(
                settings=settings,
                run_id=run_id,
                stage_name="plan",
                active_runner=active_runner,
                fallback_runner=fallback_runner,
                task=task,
                log_path=log_path,
                write_attempt_log=_write_plan_attempt_log,
                logger=LOGGER,
            )

        plan = normalize_implementation_plan(result.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await complete_plan_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        if not log_path.exists():
            _write_plan_log(log_path, "", str(error))
        await fail_plan_run(
            settings,
            run_id=run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return ImplementationPlanResult(
        run_id=run_id,
        artifact_path=artifact_path,
        log_path=log_path,
        plan=plan,
    )


def _log_tool_mismatch(runner: Runner) -> None:
    warning = runner_tool_mismatch_warning(
        runner,
        stage_name="plan",
        required_tools=REQUIRED_TOOLS,
    )
    if warning is not None:
        LOGGER.warning(warning)


def build_plan_prompt(snapshot: dict[str, Any], scout_report: dict[str, Any]) -> str:
    prompt_scout_report = _prompt_scout_report(scout_report)

    return f"""You are creating an implementation plan for a GitHub issue.

Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}

IssueSnapshot JSON:
{_prompt_json(snapshot)}

ScoutReport JSON:
{_prompt_json(prompt_scout_report)}

Return a JSON object matching this schema exactly:
{{
  "schema": "pawchestrator.implementation_plan.v1",
  "approach_summary": "string - 2-3 sentence overview",
  "steps": [
    {{
      "order": 1,
      "description": "string",
      "files_to_modify": ["path/to/file.py"],
      "notes": "string"
    }}
  ],
  "files_to_modify": ["deduplicated list of all files"],
  "estimated_risk": "low" | "medium" | "high"
}}

Use your Read, Glob, Grep tools to explore the codebase before planning.
Be terse. Return minimal valid JSON. Keep descriptions under 20 words per step.
"""


def _prompt_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def normalize_implementation_plan(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("Claude did not return a JSON artifact")

    steps = [_normalize_step(step, index) for index, step in enumerate(_list_value(artifact.get("steps")), start=1)]
    files_to_modify = _dedupe_strings(artifact.get("files_to_modify"))
    if not files_to_modify:
        files_to_modify = _dedupe_strings(
            file_path
            for step in steps
            for file_path in step["files_to_modify"]
        )

    estimated_risk = str(artifact.get("estimated_risk") or "medium")
    if estimated_risk not in VALID_RISKS:
        estimated_risk = "medium"

    approach_summary = str(artifact.get("approach_summary") or "").strip()
    if not approach_summary:
        raise ValueError("Implementation plan missing required approach_summary")
    if not steps:
        raise ValueError("Implementation plan missing required steps")
    if not files_to_modify:
        raise ValueError("Implementation plan missing required files_to_modify")

    return {
        "schema": str(artifact.get("schema") or IMPLEMENTATION_PLAN_SCHEMA),
        "approach_summary": approach_summary,
        "steps": steps,
        "files_to_modify": files_to_modify,
        "estimated_risk": estimated_risk,
    }


def _normalize_step(step: object, fallback_order: int) -> dict[str, object]:
    if not isinstance(step, dict):
        return {
            "order": fallback_order,
            "description": str(step),
            "files_to_modify": [],
            "notes": "",
        }

    try:
        order = int(step.get("order") or fallback_order)
    except (TypeError, ValueError):
        order = fallback_order

    return {
        "order": order,
        "description": str(step.get("description") or ""),
        "files_to_modify": _dedupe_strings(step.get("files_to_modify")),
        "notes": str(step.get("notes") or ""),
    }


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _prompt_scout_report(scout_report: dict[str, Any]) -> dict[str, Any]:
    compressed = dict(scout_report)
    compressed["findings"] = _list_value(compressed.get("findings"))[:MAX_PROMPT_FINDINGS]
    compressed["risks"] = _list_value(compressed.get("risks"))[:MAX_PROMPT_RISKS]
    return compressed


def _dedupe_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        value = list(value) if value is not None and not isinstance(value, str) else []

    deduped: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item)
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _scout_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "scout_report.json"


def _plan_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "implementation_plan.json"


def _plan_log_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "stdout" / "plan.log"


def _write_plan_log(log_path: Path, stdout: str, stderr: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        encoding="utf-8",
    )


def _write_plan_attempt_log(
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
