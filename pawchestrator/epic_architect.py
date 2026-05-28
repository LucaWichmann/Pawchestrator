"""Epic architect stage orchestration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pawchestrator.config import Settings
from pawchestrator.db import get_run_state, insert_run_warning, lookup_repo_path
from pawchestrator.epic_scout import (
    epic_scout_report_path,
    run_epic_scout,
)
from pawchestrator.github import GitHubError, GitHubIssueClient, get_gh_token
from pawchestrator.issues import snapshot_issue
from pawchestrator.models import EpicArchitectPlan
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
from pawchestrator.stage_lifecycle import (
    StageFailedWithArtifact,
    StageResult,
    run_stage_lifecycle,
)

EPIC_ARCHITECT_PLAN_SCHEMA = "pawchestrator.epic_architect_plan.v1"
LOGGER = logging.getLogger(__name__)


async def run_epic_architect(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str,
    repo_path: Path | None = None,
    runner: Runner | None = None,
    github_client: GitHubIssueClient | None = None,
) -> StageResult:
    snapshot_path = _snapshot_artifact_path(settings, run_id)
    if not snapshot_path.exists():
        await snapshot_issue(issue_url, settings, run_id=run_id)
    scout_path = epic_scout_report_path(settings, run_id)
    if not scout_path.exists():
        await run_epic_scout(
            issue_url,
            settings,
            run_id=run_id,
            repo_path=repo_path,
        )

    state = await get_run_state(settings, run_id)
    if state is None:
        raise ValueError(f"run not found: {run_id}")

    snapshot = _read_json(snapshot_path)
    scout_report = _read_json(scout_path)
    artifact_path = epic_architect_plan_path(settings, run_id)

    async def body(log_path: Path) -> tuple[dict[str, Any], Path]:
        cwd = await _resolve_repo_path(settings, snapshot, repo_path) or Path.cwd()
        active_runner = runner or resolve_runner(settings, "epic_architect", "claude")
        task = RunnerTask(
            prompt=build_epic_architect_prompt(snapshot, scout_report),
            cwd=cwd.resolve(),
            run_id=run_id,
            stage_name="epic_architect",
        )
        result = await run_task_with_usage_limit_fallback(
            settings=settings,
            run_id=run_id,
            stage_name="epic_architect",
            active_runner=active_runner,
            fallback_runner=usage_limit_fallback_runner(
                settings,
                "epic_architect",
                active_runner,
            ),
            task=task,
            log_path=log_path,
            write_attempt_log=_write_epic_architect_attempt_log,
            logger=LOGGER,
        )
        plan = normalize_epic_architect_plan(result.artifact)
        validated_plan = await validate_epic_architect_dependencies(
            settings,
            run_id=run_id,
            plan=plan,
        )
        client = github_client or GitHubIssueClient(get_gh_token())
        try:
            created_plan = await create_epic_architect_sub_issues(
                validated_plan,
                client=client,
                owner=str(snapshot["owner"]),
                repo=str(snapshot["repo"]),
                parent_number=int(snapshot["number"]),
            )
        except EpicArchitectSubIssueCreationError as error:
            raise StageFailedWithArtifact(str(error), error.plan, artifact_path) from error
        return created_plan, artifact_path

    return await run_stage_lifecycle(settings, run_id, "epic_architect", body)


def build_epic_architect_prompt(
    snapshot: dict[str, Any],
    scout_report: dict[str, Any],
) -> str:
    payload = {
        "role": "Staff Engineer",
        "task": (
            "Return only EpicArchitectPlan JSON. Decompose the issue into atomic, "
            "non-overlapping sub-issues. Separate frontend, backend, infra, and "
            "test work where applicable. Keep epic_analysis to one terse sentence."
        ),
        "output_schema": {
            "epic_analysis": "One terse sentence describing the decomposition approach.",
            "sub_issues": [
                {
                    "title": "Backend: Add X endpoint",
                    "description": (
                        "Full issue body. Append acceptance criteria here.\n\n"
                        "**Acceptance Criteria:**\n- [ ] ..."
                    ),
                    "depends_on_indexes": [],
                }
            ],
        },
        "rules": [
            "Use zero-based depends_on_indexes into sub_issues only.",
            "Do not include labels, estimates, prose, markdown fences, or tool calls.",
            "Pawchestrator validates dependency indexes and cycles after this stage.",
        ],
        "issue": snapshot,
        "epic_scout_report": scout_report,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def normalize_epic_architect_plan(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        raise ValueError("epic architect did not return a JSON artifact")

    try:
        model = EpicArchitectPlan.model_validate(artifact)
    except ValidationError as error:
        raise ValueError(f"EpicArchitectPlan is invalid: {error}") from error

    plan = model.model_dump(mode="json")
    plan["schema"] = str(artifact.get("schema") or EPIC_ARCHITECT_PLAN_SCHEMA)
    plan["epic_analysis"] = " ".join(plan["epic_analysis"].splitlines()).strip()
    return plan


async def create_epic_architect_sub_issues(
    plan: dict[str, Any],
    *,
    client: GitHubIssueClient,
    owner: str,
    repo: str,
    parent_number: int,
) -> dict[str, Any]:
    created_by_index: dict[int, dict[str, Any]] = {}
    created_sub_issues: list[dict[str, Any]] = []

    try:
        for index in topological_sub_issue_order(plan["sub_issues"]):
            sub_issue = plan["sub_issues"][index]
            body = _description_with_dependency_references(
                str(sub_issue["description"]),
                sub_issue["depends_on_indexes"],
                created_by_index,
            )
            created = await client.create_issue_details(
                owner,
                repo,
                title=str(sub_issue["title"]),
                body=body,
            )
            created_record = {
                "number": int(created["number"]),
                "title": str(created["title"]),
                "url": str(created["url"]),
            }
            created_by_index[index] = created_record
            created_sub_issues.append(created_record)
            await client.link_sub_issue(
                owner,
                repo,
                parent_number,
                sub_issue_id=str(created["node_id"]),
            )
    except GitHubError as error:
        plan["created_sub_issues"] = created_sub_issues
        raise EpicArchitectSubIssueCreationError(str(error), plan) from error

    plan["created_sub_issues"] = created_sub_issues
    return plan


class EpicArchitectSubIssueCreationError(RuntimeError):
    def __init__(self, message: str, plan: dict[str, Any]) -> None:
        super().__init__(message)
        self.plan = plan


def topological_sub_issue_order(sub_issues: list[dict[str, Any]]) -> list[int]:
    dependents_by_dependency: dict[int, list[int]] = {
        index: [] for index in range(len(sub_issues))
    }
    indegrees = [0 for _ in sub_issues]
    for index, sub_issue in enumerate(sub_issues):
        for dependency in sub_issue["depends_on_indexes"]:
            dependents_by_dependency[dependency].append(index)
            indegrees[index] += 1

    ready = [index for index, indegree in enumerate(indegrees) if indegree == 0]
    order: list[int] = []
    while ready:
        dependency = ready.pop(0)
        order.append(dependency)
        for dependent in dependents_by_dependency[dependency]:
            indegrees[dependent] -= 1
            if indegrees[dependent] == 0:
                ready.append(dependent)
    return order


def _description_with_dependency_references(
    description: str,
    depends_on_indexes: list[int],
    created_by_index: dict[int, dict[str, Any]],
) -> str:
    dependency_numbers = [
        int(created_by_index[index]["number"])
        for index in depends_on_indexes
        if index in created_by_index
    ]
    if not dependency_numbers:
        return description
    references = ", ".join(f"#{number}" for number in dependency_numbers)
    return f"{description}\n\nDepends on: {references}"


async def validate_epic_architect_dependencies(
    settings: Settings,
    *,
    run_id: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    sub_issues = plan["sub_issues"]
    issue_count = len(sub_issues)
    for index, sub_issue in enumerate(sub_issues):
        valid_dependencies: list[int] = []
        for dependency in sub_issue["depends_on_indexes"]:
            if dependency < 0 or dependency >= issue_count or dependency == index:
                await _warn_invalid_dependency(
                    settings,
                    run_id=run_id,
                    index=index,
                    dependency=dependency,
                    reason="out of range or self-reference",
                )
                continue
            valid_dependencies.append(dependency)
        sub_issue["depends_on_indexes"] = valid_dependencies

    cyclic_nodes = _cyclic_dependency_nodes(sub_issues)
    for index in sorted(cyclic_nodes):
        stripped = list(sub_issues[index]["depends_on_indexes"])
        sub_issues[index]["depends_on_indexes"] = []
        for dependency in stripped:
            await _warn_invalid_dependency(
                settings,
                run_id=run_id,
                index=index,
                dependency=dependency,
                reason="cycle detected",
            )
    return plan


def _cyclic_dependency_nodes(sub_issues: list[dict[str, Any]]) -> set[int]:
    visiting: set[int] = set()
    visited: set[int] = set()
    stack: list[int] = []
    stack_indexes: dict[int, int] = {}
    cyclic: set[int] = set()

    def visit(index: int) -> None:
        if index in visited:
            return
        if index in visiting:
            cyclic.update(stack[stack_indexes[index] :])
            return

        visiting.add(index)
        stack_indexes[index] = len(stack)
        stack.append(index)
        for dependency in sub_issues[index]["depends_on_indexes"]:
            visit(dependency)
        stack.pop()
        stack_indexes.pop(index)
        visiting.remove(index)
        visited.add(index)

    for index in range(len(sub_issues)):
        visit(index)
    return cyclic


def epic_architect_plan_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "epic_architect_plan.json"


async def _warn_invalid_dependency(
    settings: Settings,
    *,
    run_id: str,
    index: int,
    dependency: int,
    reason: str,
) -> None:
    await insert_run_warning(
        settings,
        run_id=run_id,
        stage_name="epic_architect",
        code="invalid_dependency",
        message=(
            f"Stripped depends_on_indexes entry {dependency} from sub_issues[{index}]: "
            f"{reason}."
        ),
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"


def _write_epic_architect_attempt_log(
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
