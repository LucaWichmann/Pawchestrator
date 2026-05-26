"""Shared stage fallback orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from pawchestrator.config import Settings, StageSettings
from pawchestrator.db import insert_run_warning
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerResult,
    RunnerTask,
    claude_usage_limit_exhausted,
)

ORIGINAL_CLAUDE_FAILURE = "Original Claude usage-limit failure"


def usage_limit_fallback_runner(
    settings: Settings,
    stage_name: str,
    primary_runner: Runner,
) -> Runner | None:
    if not isinstance(primary_runner, ClaudeRunner):
        return None

    stage_settings = settings.stages.get(stage_name)
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
    fallback_settings = stage_settings or StageSettings()
    fallback_codex = fallback_settings.codex.model_copy(
        update={"sandbox": "read-only", "bypass_sandbox": False}
    )
    stage_overrides[stage_name] = fallback_settings.model_copy(
        update={"runner": "codex", "codex": fallback_codex}
    )
    return CodexRunner(
        settings.runners.codex,
        debug=settings.debug,
        stage_overrides=stage_overrides,
    )


async def run_task_with_usage_limit_fallback(
    *,
    settings: Settings,
    run_id: str,
    stage_name: str,
    active_runner: Runner,
    fallback_runner: Runner | None,
    task: RunnerTask,
    log_path: Path,
    write_attempt_log: Callable[[Path, str, RunnerResult, bool], None],
    logger: logging.Logger,
) -> RunnerResult:
    result = await run_checked_runner(active_runner, task)
    write_attempt_log(log_path, active_runner.id, result, append=False)

    if result.exit_code == 0:
        return result

    detail = runner_failure_detail(result, active_runner.id)
    if fallback_runner is None or not claude_usage_limit_exhausted(result):
        raise RuntimeError(detail)

    warning_message = f"Claude usage limit exhausted; using Codex for {stage_name}."
    await insert_run_warning(
        settings,
        run_id=run_id,
        stage_name=stage_name,
        code=f"{stage_name}_usage_limit_fallback",
        message=warning_message,
    )
    logger.warning(warning_message)

    try:
        fallback_result = await run_checked_runner(fallback_runner, task)
        write_attempt_log(log_path, fallback_runner.id, fallback_result, append=True)
        if fallback_result.exit_code != 0:
            fallback_detail = runner_failure_detail(
                fallback_result,
                fallback_runner.id,
            )
            raise RuntimeError(f"{fallback_detail}\n{ORIGINAL_CLAUDE_FAILURE}: {detail}")
        return fallback_result
    except Exception as fallback_error:
        if ORIGINAL_CLAUDE_FAILURE in str(fallback_error):
            raise
        raise RuntimeError(
            f"{fallback_error}\n{ORIGINAL_CLAUDE_FAILURE}: {detail}"
        ) from fallback_error


async def run_checked_runner(runner: Runner, task: RunnerTask) -> RunnerResult:
    healthy, message = await runner.check_health()
    if not healthy:
        raise RuntimeError(message)
    return await runner.run_task(task)


def runner_failure_detail(result: RunnerResult, runner_id: str) -> str:
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or f"{runner_id.capitalize()} runner failed"
    )
