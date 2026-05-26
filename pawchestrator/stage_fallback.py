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
    RunnerFailedError,
    RunnerResult,
    RunnerTask,
    claude_usage_limit_exhausted,
)

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

    if fallback_runner is None or not claude_usage_limit_exhausted(result):
        raise RunnerFailedError(
            public_message=f"Runner exited with code {result.exit_code}",
            exit_code=result.exit_code,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    warning_message = f"Claude usage limit exhausted; using Codex for {stage_name}."
    await insert_run_warning(
        settings,
        run_id=run_id,
        stage_name=stage_name,
        code=f"{stage_name}_usage_limit_fallback",
        message=warning_message,
    )
    logger.warning(warning_message)

    fallback_result = await run_checked_runner(fallback_runner, task)
    write_attempt_log(log_path, fallback_runner.id, fallback_result, append=True)
    if fallback_result.exit_code != 0:
        raise RunnerFailedError(
            public_message=(
                f"Claude exited with code {result.exit_code}; "
                f"Codex fallback exited with code {fallback_result.exit_code}"
            ),
            exit_code=fallback_result.exit_code,
            stderr=(
                f"[claude stderr]\n{result.stderr}\n"
                f"[codex stderr]\n{fallback_result.stderr}"
            ),
            stdout=(
                f"[claude stdout]\n{result.stdout}\n"
                f"[codex stdout]\n{fallback_result.stdout}"
            ),
        )
    return fallback_result


async def run_checked_runner(runner: Runner, task: RunnerTask) -> RunnerResult:
    healthy, message = await runner.check_health()
    if not healthy:
        raise RuntimeError(message)
    return await runner.run_task(task)
