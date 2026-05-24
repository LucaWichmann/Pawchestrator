"""Sequential issue-to-PR pipeline orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_pipeline_run,
    get_github_comment_id,
    get_run_state,
    get_run_warnings,
    get_worktree_record,
    lookup_repo_path,
    mark_run_completed,
    mark_run_failed,
    store_github_comment_id,
)
from pawchestrator.github import (
    GitHubIssueClient,
    PAWCHESTRATOR_LABELS,
    ensure_pawchestrator_labels,
    format_run_comment,
    get_gh_token,
    parse_issue_url,
)
from pawchestrator.implement import run_implement
from pawchestrator.issues import SnapshotResult, snapshot_issue
from pawchestrator.plan import ImplementationPlanResult, run_plan
from pawchestrator.pr import PrDraftResult, run_pr
from pawchestrator.scout import ScoutResult, run_scout
from pawchestrator.verify import VerificationResult, run_verify

ProgressFn = Callable[[str], None]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    pr_url: str


async def run_pipeline(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str | None = None,
    repo_path: Path | None = None,
    allow_empty_commit: bool = False,
    progress: ProgressFn = print,
) -> PipelineResult:
    reference = parse_issue_url(issue_url)
    resolved_repo_path = repo_path.resolve() if repo_path is not None else None
    if resolved_repo_path is None:
        resolved_repo_path = await lookup_repo_path(
            settings,
            owner=reference.owner,
            repo=reference.repo,
        )
        if resolved_repo_path is None:
            raise ValueError("Repo not registered — run `pawchestrator repo add <path>` first")
        resolved_repo_path = resolved_repo_path.resolve()

    active_run_id = run_id or str(uuid4())
    if run_id is None:
        await create_pipeline_run(
            settings,
            run_id=active_run_id,
            owner=reference.owner,
            repo=reference.repo,
            issue_number=reference.number,
        )

    comment_client = await _post_initial_run_comment(settings, active_run_id)
    await _ensure_pawchestrator_labels(settings, active_run_id, comment_client)
    await _swap_stage_label(settings, active_run_id, comment_client, "running")

    async def snapshot_stage() -> SnapshotResult:
        return await snapshot_issue(issue_url, settings, run_id=active_run_id)

    async def scout_stage() -> ScoutResult:
        return await run_scout(active_run_id, settings, repo_path=resolved_repo_path)

    async def plan_stage() -> ImplementationPlanResult:
        return await run_plan(active_run_id, settings, repo_path=resolved_repo_path)

    async def implement_stage():
        return await run_implement(active_run_id, settings, repo_path=resolved_repo_path)

    async def verify_stage() -> VerificationResult:
        return await run_verify(active_run_id, settings)

    async def pr_stage() -> PrDraftResult:
        return await run_pr(
            active_run_id,
            settings,
            allow_empty_commit=allow_empty_commit,
        )

    pr_url = ""
    try:
        await _run_stage("snapshot", snapshot_stage, progress)
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "scouting")
        scout = await _run_stage("scout", scout_stage, progress)
        _print_done(progress, "scout", f"readiness: {scout.report.get('readiness', 'unknown')}")
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "planning")
        await _run_stage("plan", plan_stage, progress)
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "implementing")
        await _run_stage("implement", implement_stage, progress)
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "verifying")
        verification = await _run_stage("verify", verify_stage, progress)
        _print_done(progress, "verify", f"status: {verification.report.get('status', 'unknown')}")
        await _edit_run_comment(settings, active_run_id, comment_client)
        pr = await _run_stage("pr", pr_stage, progress)
        pr_url = pr.pr_url
        _print_done(progress, "pr", pr_url)
        await _edit_run_comment(settings, active_run_id, comment_client)
    except Exception:
        await mark_run_failed(settings, run_id=active_run_id)
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "failed")
        raise

    await mark_run_completed(settings, run_id=active_run_id)
    await _edit_run_comment(settings, active_run_id, comment_client)
    await _swap_stage_label(settings, active_run_id, comment_client, "pr-ready")
    progress(pr_url)
    return PipelineResult(run_id=active_run_id, pr_url=pr_url)


async def _run_stage[T](
    stage_name: str,
    stage_fn: Callable[[], Awaitable[T]],
    progress: ProgressFn,
) -> T:
    progress(f"[{stage_name}] running...")
    try:
        result = await stage_fn()
    except Exception as error:
        progress(f"[{stage_name}] FAILED: {error}")
        raise
    if stage_name not in {"scout", "verify", "pr"}:
        _print_done(progress, stage_name)
    return result


def _print_done(progress: ProgressFn, stage_name: str, suffix: str | None = None) -> None:
    message = f"[{stage_name}] done"
    if suffix:
        message = f"{message} - {suffix}"
    progress(message)


async def _post_initial_run_comment(
    settings: Settings,
    run_id: str,
) -> GitHubIssueClient | None:
    try:
        existing_comment_id = await get_github_comment_id(settings, run_id)
        if existing_comment_id is not None:
            return GitHubIssueClient(get_gh_token())

        run_state = await _comment_run_state(settings, run_id)
        if run_state is None:
            return None
        warnings = await get_run_warnings(settings, run_id)
        client = GitHubIssueClient(get_gh_token())
        comment_id = await client.post_comment(
            str(run_state["owner"]),
            str(run_state["repo"]),
            int(run_state["issue_number"]),
            format_run_comment(run_state, warnings),
        )
        await store_github_comment_id(settings, run_id, comment_id)
        return client
    except Exception as error:
        LOGGER.warning("GitHub run comment post failed for %s: %s", run_id, error)
        return None


async def _edit_run_comment(
    settings: Settings,
    run_id: str,
    client: GitHubIssueClient | None,
) -> None:
    try:
        comment_id = await get_github_comment_id(settings, run_id)
        if comment_id is None:
            return
        run_state = await _comment_run_state(settings, run_id)
        if run_state is None:
            return
        warnings = await get_run_warnings(settings, run_id)
        active_client = client or GitHubIssueClient(get_gh_token())
        await active_client.edit_comment(
            str(run_state["owner"]),
            str(run_state["repo"]),
            comment_id,
            format_run_comment(run_state, warnings),
        )
    except Exception as error:
        LOGGER.warning("GitHub run comment edit failed for %s: %s", run_id, error)


async def _ensure_pawchestrator_labels(
    settings: Settings,
    run_id: str,
    client: GitHubIssueClient | None,
) -> None:
    try:
        run_state = await _comment_run_state(settings, run_id)
        if run_state is None:
            return
        active_client = client or GitHubIssueClient(get_gh_token())
        await ensure_pawchestrator_labels(
            active_client,
            str(run_state["owner"]),
            str(run_state["repo"]),
        )
    except Exception as error:
        LOGGER.warning("GitHub label ensure failed for %s: %s", run_id, error)


async def _swap_stage_label(
    settings: Settings,
    run_id: str,
    client: GitHubIssueClient | None,
    stage_name: str,
) -> None:
    try:
        run_state = await _comment_run_state(settings, run_id)
        if run_state is None:
            return
        active_client = client or GitHubIssueClient(get_gh_token())
        owner = str(run_state["owner"])
        repo = str(run_state["repo"])
        issue_number = int(run_state["issue_number"])
        next_label = PAWCHESTRATOR_LABELS[stage_name][0]
        for label_name, _color in PAWCHESTRATOR_LABELS.values():
            if label_name != next_label:
                await active_client.remove_label(owner, repo, issue_number, label_name)
        await active_client.add_label(owner, repo, issue_number, next_label)
    except Exception as error:
        LOGGER.warning("GitHub stage label update failed for %s: %s", run_id, error)


async def _comment_run_state(settings: Settings, run_id: str) -> dict[str, object] | None:
    run_state = await get_run_state(settings, run_id)
    if run_state is None:
        return None
    worktree = await get_worktree_record(settings, run_id=run_id)
    if worktree is not None:
        run_state["branch"] = worktree["branch"]
    return run_state
