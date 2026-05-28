"""Epic architect scout stage orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import get_run_state, insert_run_warning, lookup_repo_path
from pawchestrator.issues import snapshot_issue
from pawchestrator.runners import (
    Runner,
    RunnerResult,
    RunnerTask,
    resolve_runner,
)
from pawchestrator.stage_fallback import (
    run_task_with_usage_limit_fallback,
    usage_limit_fallback_runner,
)
from pawchestrator.stage_lifecycle import StageResult, run_stage_lifecycle

EPIC_SCOUT_REPORT_SCHEMA = "pawchestrator.epic_scout_report.v1"
MAX_RELEVANT_FILES = 5
MAX_FILE_TREE_ENTRIES = 120
MAX_CONTEXT_CHARS = 4000
LOGGER = logging.getLogger(__name__)


async def run_epic_scout(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str,
    repo_path: Path | None = None,
    runner: Runner | None = None,
) -> StageResult:
    if not _snapshot_artifact_path(settings, run_id).exists():
        await snapshot_issue(issue_url, settings, run_id=run_id)

    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    snapshot = _read_snapshot(settings, run_id)
    artifact_path = epic_scout_report_path(settings, run_id)

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        local_repo_path = await _resolve_repo_path(settings, snapshot, repo_path)
        if local_repo_path is None:
            await insert_run_warning(
                settings,
                run_id=run_id,
                stage_name="epic_scout",
                code="repo_not_registered",
                message="Repository is not registered; epic scout skipped file lookup.",
            )
            return _empty_report(), artifact_path

        active_runner = runner or resolve_runner(settings, "epic_scout", "claude")
        task = RunnerTask(
            prompt=build_epic_scout_prompt(snapshot, local_repo_path),
            cwd=local_repo_path.resolve(),
            run_id=run_id,
            stage_name="epic_scout",
        )
        result = await run_task_with_usage_limit_fallback(
            settings=settings,
            run_id=run_id,
            stage_name="epic_scout",
            active_runner=active_runner,
            fallback_runner=usage_limit_fallback_runner(
                settings,
                "epic_scout",
                active_runner,
            ),
            task=task,
            log_path=log_path,
            write_attempt_log=_write_epic_scout_attempt_log,
            logger=LOGGER,
        )
        return normalize_epic_scout_report(result.artifact), artifact_path

    return await run_stage_lifecycle(settings, run_id, "epic_scout", body)


def build_epic_scout_prompt(snapshot: dict[str, Any], repo_path: Path) -> str:
    payload = {
        "task": (
            "Return only EpicScoutReport JSON. Issue body is the primary signal. "
            "Use repo context only for existing behavior explicitly referenced by "
            "the issue. Be terse."
        ),
        "output_schema": {
            "relevant_files": [
                {"path": "relative/path.py", "reason": "short", "snippet": "short"}
            ],
            "tech_context": "one line",
        },
        "rules": [
            "Cap relevant_files at 5.",
            "Use only files shown in repo_context.",
            "No prose, markdown, or tool calls.",
        ],
        "issue": snapshot,
        "repo_context": _repo_context(repo_path),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def normalize_epic_scout_report(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("epic scout did not return a JSON artifact")

    files = artifact.get("relevant_files")
    if not isinstance(files, list):
        raise ValueError("EpicScoutReport relevant_files must be a list")

    relevant_files: list[dict[str, str]] = []
    for item in files[:MAX_RELEVANT_FILES]:
        if not isinstance(item, dict):
            raise ValueError("EpicScoutReport relevant_files entries must be objects")
        path = item.get("path")
        reason = item.get("reason")
        snippet = item.get("snippet")
        if not all(isinstance(value, str) for value in (path, reason, snippet)):
            raise ValueError("EpicScoutReport relevant_files entries are invalid")
        relevant_files.append(
            {
                "path": path,
                "reason": reason,
                "snippet": snippet,
            }
        )

    tech_context = artifact.get("tech_context")
    if not isinstance(tech_context, str) or not tech_context.strip():
        raise ValueError("EpicScoutReport tech_context must be a non-empty string")

    return {
        "schema": str(artifact.get("schema") or EPIC_SCOUT_REPORT_SCHEMA),
        "relevant_files": relevant_files,
        "tech_context": " ".join(tech_context.splitlines()).strip(),
    }


def epic_scout_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "epic_scout_report.json"


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


def _repo_context(repo_path: Path) -> dict[str, Any]:
    return {
        "file_tree": _file_tree(repo_path),
        "context_md": _read_context_md(repo_path),
    }


def _file_tree(repo_path: Path) -> list[str]:
    ignored = {".git", ".codegraph", ".venv", "__pycache__", "node_modules"}
    paths: list[str] = []
    for path in sorted(repo_path.rglob("*")):
        if len(paths) >= MAX_FILE_TREE_ENTRIES:
            break
        if any(part in ignored for part in path.relative_to(repo_path).parts):
            continue
        if path.is_file():
            paths.append(path.relative_to(repo_path).as_posix())
    return paths


def _read_context_md(repo_path: Path) -> str:
    path = repo_path / "CONTEXT.md"
    try:
        return path.read_text(encoding="utf-8")[:MAX_CONTEXT_CHARS]
    except OSError:
        return ""


def _read_snapshot(settings: Settings, run_id: str) -> dict[str, Any]:
    return json.loads(_snapshot_artifact_path(settings, run_id).read_text(encoding="utf-8"))


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _empty_report() -> dict[str, Any]:
    return {
        "schema": EPIC_SCOUT_REPORT_SCHEMA,
        "relevant_files": [],
        "tech_context": "Repository not registered; codebase context unavailable.",
    }


def _write_epic_scout_attempt_log(
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
