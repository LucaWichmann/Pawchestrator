"""Implementation plan stage orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.skill_loader import load_skill
from pawchestrator.db import get_run_state
from pawchestrator.runners import (
    Runner,
    RunnerTask,
    resolve_runner,
    runner_tool_mismatch_warning,
)
from pawchestrator.stage_fallback import (
    run_task_with_usage_limit_fallback,
    usage_limit_fallback_runner,
)
from pawchestrator.stage_lifecycle import StageResult, run_stage_lifecycle

IMPLEMENTATION_PLAN_SCHEMA = "pawchestrator.implementation_plan.v1"
REQUIRED_TOOLS: list[str] = ["Read", "Glob", "Grep"]
VALID_RISKS = {"low", "medium", "high"}
VALID_FILE_OPERATION_TYPES = {"create", "modify", "delete"}
MAX_FILE_OPERATION_DESCRIPTION_LENGTH = 100
MAX_PROMPT_FINDINGS = 5
MAX_PROMPT_RISKS = 5
LOGGER = logging.getLogger(__name__)


async def run_plan(
    run_id: str,
    settings: Settings,
    *,
    repo_path: Path | None = None,
    runner: Runner | None = None,
    rejections: list[dict[str, Any]] | None = None,
) -> StageResult:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    local_repo_path = (repo_path or Path.cwd()).resolve()
    artifact_path = _plan_artifact_path(settings, run_id)

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        snapshot_path = _snapshot_artifact_path(settings, run_id)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"issue snapshot not found: {snapshot_path}")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        scout_path = _scout_artifact_path(settings, run_id)
        if not scout_path.exists():
            raise FileNotFoundError(f"scout report not found: {scout_path}")

        scout_report = json.loads(scout_path.read_text(encoding="utf-8"))
        active_runner = runner or resolve_runner(settings, "plan", "claude")
        _log_tool_mismatch(active_runner)
        task = RunnerTask(
            prompt=build_plan_prompt(
                snapshot,
                scout_report,
                app_dir=settings.app_dir,
                rejections=rejections,
            ),
            cwd=local_repo_path,
            run_id=run_id,
            stage_name="plan",
        )
        fallback_runner = usage_limit_fallback_runner(settings, "plan", active_runner)
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
        return plan, artifact_path

    return await run_stage_lifecycle(settings, run_id, "plan", body)


def _log_tool_mismatch(runner: Runner) -> None:
    warning = runner_tool_mismatch_warning(
        runner,
        stage_name="plan",
        required_tools=REQUIRED_TOOLS,
    )
    if warning is not None:
        LOGGER.warning(warning)


_PLAN_FALLBACK = 'Create an implementation plan for this issue and return an ImplementationPlan JSON artifact with approach_summary, steps, file_operations, files_to_modify, and estimated_risk. file_operations must be [{path, type, description}], type must be "create", "modify", or "delete", and description must be one line <=100 chars.'


def build_plan_prompt(
    snapshot: dict[str, Any],
    scout_report: dict[str, Any],
    app_dir: Path | None = None,
    rejections: list[dict[str, Any]] | None = None,
) -> str:
    prompt_scout_report = _prompt_scout_report(scout_report)
    instructions = load_skill("ImplementationPlan", app_dir) or _PLAN_FALLBACK
    data_section = f"""Issue: #{snapshot.get("number")} - {snapshot.get("title", "")}
Repository: {snapshot.get("owner", "")}/{snapshot.get("repo", "")}

IssueSnapshot JSON:
{_prompt_json(_prompt_plan_snapshot(snapshot))}

ScoutReport JSON:
{_prompt_json(prompt_scout_report)}"""

    rejection_section = _prompt_rejections(rejections or [])
    if rejection_section:
        data_section = f"{data_section}\n\n{rejection_section}"

    return f"{instructions}\n\n{data_section}"


def plan_rejections_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "plan_rejections.json"


def load_plan_rejections(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    path = plan_rejections_path(settings, run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def append_plan_rejection(
    settings: Settings,
    run_id: str,
    feedback: str,
) -> dict[str, Any]:
    rejections = load_plan_rejections(settings, run_id)
    entry = {"attempt": len(rejections) + 1, "feedback": feedback}
    rejections.append(entry)
    path = plan_rejections_path(settings, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(rejections, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return entry


def _prompt_rejections(rejections: list[dict[str, Any]]) -> str:
    if not rejections:
        return ""

    lines = ["## Previous plan rejections"]
    for index, rejection in enumerate(rejections, start=1):
        attempt = rejection.get("attempt", index)
        feedback = str(rejection.get("feedback") or "")
        lines.append(f"Attempt {attempt} feedback: {json.dumps(feedback)}")
    lines.append("Please address all of the above feedback in this revised plan.")
    return "\n".join(lines)


def _prompt_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _prompt_plan_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    compressed = dict(snapshot)
    compressed.pop("comments", None)
    return compressed


def normalize_implementation_plan(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("Claude did not return a JSON artifact")

    steps = [_normalize_step(step, index) for index, step in enumerate(_list_value(artifact.get("steps")), start=1)]
    file_operations = _normalize_file_operations(artifact.get("file_operations"))
    if file_operations:
        files_to_modify = _dedupe_strings(
            operation["path"] for operation in file_operations
        )
    else:
        files_to_modify = _dedupe_strings(artifact.get("files_to_modify"))

    if not files_to_modify:
        files_to_modify = _dedupe_strings(
            file_path
            for step in steps
            for file_path in step["files_to_modify"]
        )
    if not file_operations:
        file_operations = [
            {"path": file_path, "type": "modify", "description": ""}
            for file_path in files_to_modify
        ]

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
        "file_operations": file_operations,
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


def _normalize_file_operations(value: object) -> list[dict[str, str]]:
    operations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _list_value(value):
        if not isinstance(item, dict):
            continue

        path = str(item.get("path") or "").strip()
        operation_type = str(item.get("type") or "").strip()
        if not path or operation_type not in VALID_FILE_OPERATION_TYPES:
            continue
        if path in seen:
            continue

        seen.add(path)
        description = str(item.get("description") or "").splitlines()[0].strip()
        operations.append(
            {
                "path": path,
                "type": operation_type,
                "description": description[:MAX_FILE_OPERATION_DESCRIPTION_LENGTH],
            }
        )
    return operations


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
