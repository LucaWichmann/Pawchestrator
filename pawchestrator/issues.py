"""Issue workflow entry points."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pawchestrator.config import Settings
from pawchestrator.db import create_pipeline_run, get_run_state
from pawchestrator.github import GitHubIssueClient, get_gh_token, parse_issue_url
from pawchestrator.stage_lifecycle import StageResult, run_stage_lifecycle


async def snapshot_issue(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str | None = None,
) -> StageResult:
    reference = parse_issue_url(issue_url)
    active_run_id = run_id or str(uuid4())
    if await get_run_state(settings, active_run_id) is None:
        await create_pipeline_run(
            settings,
            run_id=active_run_id,
            owner=reference.owner,
            repo=reference.repo,
            issue_number=reference.number,
        )

    async def body(_log_path: Path) -> tuple[dict[str, object], Path]:
        token = get_gh_token()
        snapshot = await GitHubIssueClient(token).fetch_snapshot(
            reference,
            settings.checkboxes.headings,
        )
        return snapshot, _snapshot_artifact_path(settings, active_run_id)

    return await run_stage_lifecycle(settings, active_run_id, "snapshot", body)


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"
