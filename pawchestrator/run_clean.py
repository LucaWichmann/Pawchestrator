"""Run artifact and worktree cleanup."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import aiosqlite

from pawchestrator.config import Settings
from pawchestrator.db import init_db

DEFAULT_CLEAN_STATUSES = ("failed", "complete")
AUTO_CLEAN_STATUSES = ("failed", "complete")
ACTIVE_STATUS_MARKERS = ("pending", "running", "waiting", "awaiting")
_DURATION_RE = re.compile(r"^(?P<count>\d+)(?P<unit>[dhw])$")


@dataclass(frozen=True)
class CleanTarget:
    run_id: str
    status: str
    artifacts_path: Path
    worktree_path: Path | None


@dataclass(frozen=True)
class CleanResult:
    target: CleanTarget
    artifacts_deleted: bool
    worktree_removed: bool
    dry_run: bool


def parse_duration(value: str) -> timedelta:
    """Parse compact durations like 30d, 12h, or 2w."""

    match = _DURATION_RE.match(value.strip())
    if match is None:
        raise ValueError("duration must use '<number>d', '<number>h', or '<number>w'")

    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "h":
        return timedelta(hours=count)
    if unit == "w":
        return timedelta(weeks=count)
    return timedelta(days=count)


async def clean_runs(
    settings: Settings,
    *,
    older_than: str,
    statuses: Iterable[str] = DEFAULT_CLEAN_STATUSES,
    dry_run: bool = False,
) -> list[CleanResult]:
    await init_db(settings)
    duration = parse_duration(older_than)
    cutoff = datetime.now(UTC) - duration
    if older_than.strip().endswith(("d", "w")):
        cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    targets = await _find_clean_targets(settings, cutoff=cutoff, statuses=statuses)
    results: list[CleanResult] = []
    for target in targets:
        artifacts_deleted = False
        worktree_removed = False
        if not dry_run:
            if target.artifacts_path.exists():
                shutil.rmtree(target.artifacts_path)
                artifacts_deleted = True
            if target.worktree_path is not None and target.worktree_path.exists():
                _remove_worktree(target.worktree_path)
                worktree_removed = True
        results.append(
            CleanResult(
                target=target,
                artifacts_deleted=artifacts_deleted,
                worktree_removed=worktree_removed,
                dry_run=dry_run,
            )
        )
    return results


async def auto_clean_runs(settings: Settings) -> list[CleanResult]:
    if settings.pipeline.auto_clean is False:
        return []
    return await clean_runs(
        settings,
        older_than=str(settings.pipeline.auto_clean),
        statuses=AUTO_CLEAN_STATUSES,
    )


async def _find_clean_targets(
    settings: Settings,
    *,
    cutoff: datetime,
    statuses: Iterable[str],
) -> list[CleanTarget]:
    status_filters = _status_filters(statuses)
    if not status_filters:
        return []

    where = " OR ".join(filter_sql for filter_sql, _ in status_filters)
    params: list[object] = [param for _, param in status_filters]
    params.append(cutoff.isoformat().replace("+00:00", "Z"))

    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT workflow_runs.id, workflow_runs.status, worktrees.path AS worktree_path
            FROM workflow_runs
            LEFT JOIN worktrees ON worktrees.run_id = workflow_runs.id
            WHERE ({where})
              AND workflow_runs.updated_at < ?
            ORDER BY workflow_runs.updated_at, workflow_runs.id
            """,
            params,
        )
        rows = await cursor.fetchall()

    targets = []
    for row in rows:
        status = str(row["status"])
        if _is_active_status(status):
            continue
        worktree_path = row["worktree_path"]
        targets.append(
            CleanTarget(
                run_id=str(row["id"]),
                status=status,
                artifacts_path=settings.app_dir / "runs" / str(row["id"]),
                worktree_path=None if worktree_path is None else Path(str(worktree_path)),
            )
        )
    return targets


def _status_filters(statuses: Iterable[str]) -> list[tuple[str, object]]:
    filters: list[tuple[str, object]] = []
    for status in statuses:
        if status == "failed":
            filters.append(("workflow_runs.status LIKE ?", "%failed"))
        elif status == "complete":
            filters.append(
                (
                    "(workflow_runs.status = 'completed' OR workflow_runs.status LIKE ?)",
                    "%complete",
                )
            )
        else:
            filters.append(("workflow_runs.status = ?", status))
    return filters


def _is_active_status(status: str) -> bool:
    return any(marker in status for marker in ACTIVE_STATUS_MARKERS)


def _remove_worktree(path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
