"""Issue workflow entry points."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_snapshot_run,
    create_snapshot_run,
    fail_snapshot_run,
)
from pawchestrator.github import GitHubIssueClient, get_gh_token, parse_issue_url


@dataclass(frozen=True)
class SnapshotResult:
    run_id: str
    artifact_path: Path
    issue_number: int
    title: str


async def snapshot_issue(
    issue_url: str,
    settings: Settings,
    *,
    run_id: str | None = None,
) -> SnapshotResult:
    reference = parse_issue_url(issue_url)
    active_run_id = run_id or str(uuid4())
    stage_id = await create_snapshot_run(
        settings,
        run_id=active_run_id,
        owner=reference.owner,
        repo=reference.repo,
        issue_number=reference.number,
    )

    try:
        token = get_gh_token()
        snapshot = await GitHubIssueClient(token).fetch_snapshot(reference)
        artifact_path = _snapshot_artifact_path(settings, active_run_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await complete_snapshot_run(
            settings,
            run_id=active_run_id,
            stage_id=stage_id,
            artifact_path=artifact_path,
        )
    except Exception as error:
        await fail_snapshot_run(
            settings,
            run_id=active_run_id,
            stage_id=stage_id,
            error=str(error),
        )
        raise

    return SnapshotResult(
        run_id=active_run_id,
        artifact_path=artifact_path,
        issue_number=reference.number,
        title=str(snapshot["title"]),
    )


def _snapshot_artifact_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "issue.snapshot.json"
