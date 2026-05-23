"""Sequential issue-to-PR pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from pawchestrator.config import Settings
from pawchestrator.db import create_pipeline_run, mark_run_completed, mark_run_failed
from pawchestrator.github import parse_issue_url
from pawchestrator.implement import run_implement
from pawchestrator.issues import SnapshotResult, snapshot_issue
from pawchestrator.plan import ImplementationPlanResult, run_plan
from pawchestrator.pr import PrDraftResult, run_pr
from pawchestrator.scout import ScoutResult, run_scout
from pawchestrator.verify import VerificationResult, run_verify

ProgressFn = Callable[[str], None]


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
    progress: ProgressFn = print,
) -> PipelineResult:
    reference = parse_issue_url(issue_url)
    active_run_id = run_id or str(uuid4())
    if run_id is None:
        await create_pipeline_run(
            settings,
            run_id=active_run_id,
            owner=reference.owner,
            repo=reference.repo,
            issue_number=reference.number,
        )

    async def snapshot_stage() -> SnapshotResult:
        return await snapshot_issue(issue_url, settings, run_id=active_run_id)

    async def scout_stage() -> ScoutResult:
        return await run_scout(active_run_id, settings, repo_path=repo_path)

    async def plan_stage() -> ImplementationPlanResult:
        return await run_plan(active_run_id, settings, repo_path=repo_path)

    async def implement_stage():
        return await run_implement(active_run_id, settings, repo_path=repo_path)

    async def verify_stage() -> VerificationResult:
        return await run_verify(active_run_id, settings)

    async def pr_stage() -> PrDraftResult:
        return await run_pr(active_run_id, settings)

    pr_url = ""
    try:
        await _run_stage("snapshot", snapshot_stage, progress)
        scout = await _run_stage("scout", scout_stage, progress)
        _print_done(progress, "scout", f"readiness: {scout.report.get('readiness', 'unknown')}")
        await _run_stage("plan", plan_stage, progress)
        await _run_stage("implement", implement_stage, progress)
        verification = await _run_stage("verify", verify_stage, progress)
        _print_done(progress, "verify", f"status: {verification.report.get('status', 'unknown')}")
        pr = await _run_stage("pr", pr_stage, progress)
        pr_url = pr.pr_url
        _print_done(progress, "pr", pr_url)
    except Exception:
        await mark_run_failed(settings, run_id=active_run_id)
        raise

    await mark_run_completed(settings, run_id=active_run_id)
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
