"""Sequential issue-to-PR pipeline orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from pawchestrator.checkbox import reconcile_checkbox_marks
from pawchestrator.config import Settings
from pawchestrator.db import (
    create_pipeline_run,
    get_github_comment_id,
    get_run_state,
    get_run_warnings,
    get_worktree_record,
    insert_run_warning,
    lookup_repo_path,
    mark_run_completed,
    mark_run_failed,
    skip_pr_stage,
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
from pawchestrator.issues import snapshot_issue
from pawchestrator.plan import run_plan
from pawchestrator.pr import run_pr
from pawchestrator.scout import run_scout
from pawchestrator.stage_lifecycle import StageResult
from pawchestrator.verify import run_verify

ProgressFn = Callable[[str], None]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    pr_url: str


class VerificationFailedError(RuntimeError):
    """Raised when verification fails after all repair attempts."""


async def run_pipeline(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str | None = None,
    group_id: str | None = None,
    repo_path: Path | None = None,
    allow_empty_commit: bool = False,
    create_pr: bool = True,
    worktree_branch: str | None = None,
    worktree_path: Path | None = None,
    base_branch: str = "main",
    pr_base_branch: str = "main",
    allow_dirty_existing_worktree: bool = False,
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
            group_id=group_id,
        )

    comment_client = await _post_initial_run_comment(settings, active_run_id)
    await _ensure_pawchestrator_labels(settings, active_run_id, comment_client)
    await _swap_stage_label(settings, active_run_id, comment_client, "running")

    async def snapshot_stage() -> StageResult:
        return await snapshot_issue(issue_url, settings, run_id=active_run_id)

    async def scout_stage() -> StageResult:
        return await run_scout(active_run_id, settings, repo_path=resolved_repo_path)

    async def plan_stage() -> StageResult:
        return await run_plan(active_run_id, settings, repo_path=resolved_repo_path)

    async def implement_stage(
        repair_context: dict[str, Any] | None = None,
        repair_attempt: int | None = None,
        dirty_worktree_allowed: bool = allow_dirty_existing_worktree,
    ):
        implement_kwargs: dict[str, Any] = {
            "repo_path": resolved_repo_path,
            "repair_context": repair_context,
            "repair_attempt": repair_attempt,
            "allow_dirty_existing_worktree": dirty_worktree_allowed,
        }
        if worktree_branch is not None:
            implement_kwargs["worktree_branch"] = worktree_branch
        if worktree_path is not None:
            implement_kwargs["worktree_path"] = worktree_path
        if base_branch != "main":
            implement_kwargs["base_branch"] = base_branch
        return await run_implement(active_run_id, settings, **implement_kwargs)

    async def verify_stage() -> StageResult:
        return await run_verify(active_run_id, settings, base_branch=base_branch)

    async def pr_stage() -> StageResult:
        pr_kwargs: dict[str, Any] = {"allow_empty_commit": allow_empty_commit}
        if pr_base_branch != "main":
            pr_kwargs["base_branch"] = pr_base_branch
        return await run_pr(active_run_id, settings, **pr_kwargs)

    async def reconcile_marks(stage_name: str) -> None:
        await _reconcile_checkbox_marks(
            settings,
            active_run_id,
            comment_client,
            stage_name=stage_name,
            progress=progress,
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
        await reconcile_marks("implement")
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "verifying")
        verification = await _run_stage("verify", verify_stage, progress)
        _print_done(
            progress,
            "verify",
            f"status: {verification.report.get('status', 'unknown')}",
        )
        await _edit_run_comment(settings, active_run_id, comment_client)
        repair_attempt = 0
        max_repair_attempts = settings.pipeline.verify_repair_attempts
        while _verification_status(verification) == "failed" and repair_attempt < max_repair_attempts:
            repair_attempt += 1
            progress(
                f"[verify] failed - repair attempt {repair_attempt}/{max_repair_attempts}"
            )
            await _swap_stage_label(settings, active_run_id, comment_client, "implementing")
            await _run_stage(
                "implement",
                lambda: implement_stage(
                    _verification_repair_context(verification),
                    repair_attempt,
                    dirty_worktree_allowed=True,
                ),
                progress,
            )
            await reconcile_marks("implement")
            await _edit_run_comment(settings, active_run_id, comment_client)
            await _swap_stage_label(settings, active_run_id, comment_client, "verifying")
            verification = await _run_stage("verify", verify_stage, progress)
            _print_done(
                progress,
                "verify",
                f"status: {verification.report.get('status', 'unknown')}",
            )
            await _edit_run_comment(settings, active_run_id, comment_client)
        if _verification_status(verification) == "failed":
            raise VerificationFailedError(_verification_failure_message(verification))
        if _verification_status(verification) not in {"passed", "skipped"}:
            raise VerificationFailedError(
                f"verification did not pass: {_verification_status(verification)}"
            )
        await reconcile_marks("verify")
        if create_pr:
            pr = await _run_stage("pr", pr_stage, progress)
            pr_url = str(pr.report["pr_url"])
            _print_done(progress, "pr", pr_url)
            await _edit_run_comment(settings, active_run_id, comment_client)
    except Exception:
        await mark_run_failed(settings, run_id=active_run_id)
        await _edit_run_comment(settings, active_run_id, comment_client)
        await _swap_stage_label(settings, active_run_id, comment_client, "failed")
        raise

    if not create_pr:
        await skip_pr_stage(
            settings,
            run_id=active_run_id,
            reason="PR creation handled by epic workflow.",
        )
    await mark_run_completed(
        settings,
        run_id=active_run_id,
        current_stage="pr" if create_pr else "verify",
    )
    await _edit_run_comment(settings, active_run_id, comment_client)
    await _swap_stage_label(settings, active_run_id, comment_client, "pr-ready")
    if pr_url:
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


async def _reconcile_checkbox_marks(
    settings: Settings,
    run_id: str,
    client: GitHubIssueClient | None,
    *,
    stage_name: str,
    progress: ProgressFn,
) -> None:
    try:
        active_client: Any = client or _LazyGitHubIssueClient()
        _changed, warnings = await reconcile_checkbox_marks(
            settings,
            run_id,
            active_client,
        )
        for warning in warnings:
            issue = (
                f"{warning['owner']}/{warning['repo']}/"
                f"{warning['issue_number']}"
            )
            message = (
                f"checkbox reconciliation after {stage_name} skipped stale mark "
                f"{issue} index {warning['checkbox_index']}: stored text no "
                "longer matches current checkbox text"
            )
            LOGGER.warning(message)
            await insert_run_warning(
                settings,
                run_id=run_id,
                stage_name=stage_name,
                code="checkbox_reconciliation_stale_mark",
                message=message,
            )
            progress(f"[{stage_name}] warning - {message}")
    except Exception as error:
        message = f"checkbox reconciliation after {stage_name} failed: {error}"
        LOGGER.warning(message)
        await insert_run_warning(
            settings,
            run_id=run_id,
            stage_name=stage_name,
            code="checkbox_reconciliation_failed",
            message=message,
        )
        progress(f"[{stage_name}] warning - {message}")


class _LazyGitHubIssueClient:
    def __init__(self) -> None:
        self._client: GitHubIssueClient | None = None

    def _active_client(self) -> GitHubIssueClient:
        if self._client is None:
            self._client = GitHubIssueClient(get_gh_token())
        return self._client

    async def fetch_issue_body(self, reference: Any) -> str:
        return await self._active_client().fetch_issue_body(reference)

    async def patch_issue_body(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> None:
        await self._active_client().patch_issue_body(
            owner,
            repo,
            issue_number,
            body,
        )


def _verification_status(verification: StageResult) -> str:
    return str(verification.report.get("status") or "unknown")


def _verification_failure_message(verification: StageResult) -> str:
    commands = verification.report.get("commands")
    if isinstance(commands, list):
        for command in commands:
            if not isinstance(command, dict) or command.get("exit_code") == 0:
                continue
            detail = str(command.get("stderr_summary") or command.get("stdout_summary") or "")
            message = f"verification failed: {command.get('command')} exited {command.get('exit_code')}"
            if detail:
                return f"{message}: {detail}"
            return message
    return "verification failed"


def _verification_repair_context(verification: StageResult) -> dict[str, Any]:
    return {
        "status": _verification_status(verification),
        "commands": verification.report.get("commands") or [],
        "skip_reason": verification.report.get("skip_reason"),
        "verify_log_tail": _tail_text(verification.log_path),
    }


def _tail_text(path: Path, *, max_chars: int = 8000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


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
