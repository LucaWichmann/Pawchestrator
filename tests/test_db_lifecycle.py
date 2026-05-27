import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_epic_run,
    complete_repair_push_run,
    complete_repair_run,
    create_epic_run,
    create_pipeline_run,
    create_repair_run,
    fail_epic_run,
    fail_repair_push_run,
    fail_repair_run,
    get_run_state,
    skip_pr_stage,
    start_epic_run,
    start_repair_push_run,
    start_repair_run,
)
from pawchestrator.stage_lifecycle import StageSkipped, run_stage_lifecycle


def test_snapshot_run_lifecycle_start_complete_and_fail(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    complete_artifact = tmp_path / "snapshot.json"

    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="snapshot-complete",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id="snapshot-fail",
            owner="owner",
            repo="repo",
            issue_number=43,
        )
    )

    async def complete_body(log_path: Path):
        return {"number": 42}, complete_artifact

    async def fail_body(log_path: Path):
        raise RuntimeError("snapshot failed")

    with pytest.raises(RuntimeError, match="snapshot failed"):
        asyncio.run(run_stage_lifecycle(settings, "snapshot-fail", "snapshot", fail_body))
    asyncio.run(
        run_stage_lifecycle(settings, "snapshot-complete", "snapshot", complete_body)
    )

    assert _run_status(tmp_path, "snapshot-complete") == (
        "snapshot_complete",
        "snapshot",
        "pipeline",
        42,
        None,
    )

    assert _run_status(tmp_path, "snapshot-complete")[:2] == (
        "snapshot_complete",
        "snapshot",
    )
    assert _stage_by_name(tmp_path, "snapshot-complete", "snapshot") == (
        "complete",
        None,
    )
    assert _artifacts(tmp_path, "snapshot-complete") == [
        ("issue_snapshot", str(complete_artifact))
    ]
    assert _run_status(tmp_path, "snapshot-fail")[:2] == (
        "snapshot_failed",
        "snapshot",
    )
    assert _stage_by_name(tmp_path, "snapshot-fail", "snapshot") == (
        "failed",
        "Stage failed. See local run logs.",
    )


def test_pipeline_run_stage_lifecycle_complete_fail_and_skip(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    for spec in _pipeline_stage_specs(tmp_path):
        _assert_stage_complete(settings, tmp_path, spec)
        _assert_stage_failed(settings, tmp_path, spec)

    _assert_stage_skipped(
        settings,
        tmp_path,
        StageSpec(
            stage_name="verify",
            run_status="verify_skipped",
            artifact_type="verification_report",
            artifact_path=tmp_path / "verify-skipped.json",
            create_run=lambda run_id: create_pipeline_run(
                settings,
                run_id=run_id,
                owner="owner",
                repo="repo",
                issue_number=99,
            ),
            complete_report={"status": "passed"},
            fail_report={"status": "failed", "error": "verify failed"},
            skip_report={"status": "skipped"},
            skip_error="verification skipped",
        ),
    )

    run_id = "pr-pending-skip"
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=100,
        )
    )
    asyncio.run(skip_pr_stage(settings, run_id=run_id, reason="no PR needed"))

    assert _run_status(tmp_path, run_id)[:2] == ("pending", None)
    assert _stage_by_name(tmp_path, run_id, "pr") == ("skipped", "no PR needed")


def test_repair_run_lifecycle_complete_and_fail(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    for spec in _repair_stage_specs(tmp_path):
        _assert_stage_complete(settings, tmp_path, spec)
        _assert_stage_failed(settings, tmp_path, spec)


def test_epic_run_lifecycle_start_complete_and_fail(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    asyncio.run(
        create_epic_run(
            settings,
            run_id="epic-complete",
            owner="owner",
            repo="repo",
            issue_number=42,
            group_id="epic-group",
        )
    )
    assert _run_status(tmp_path, "epic-complete")[:3] == ("pending", None, "epic")

    asyncio.run(start_epic_run(settings, run_id="epic-complete"))
    assert _run_status(tmp_path, "epic-complete")[:2] == ("epic_running", "epic")
    asyncio.run(
        complete_epic_run(
            settings,
            run_id="epic-complete",
            pr_url="https://github.com/owner/repo/pull/1",
        )
    )
    assert _run_pr_url(tmp_path, "epic-complete") == "https://github.com/owner/repo/pull/1"
    assert _run_status(tmp_path, "epic-complete")[:2] == ("epic_complete", "epic")
    assert _stage_count(tmp_path, "epic-complete") == 0

    asyncio.run(
        create_epic_run(
            settings,
            run_id="epic-fail",
            owner="owner",
            repo="repo",
            issue_number=43,
            group_id="epic-group",
        )
    )
    asyncio.run(start_epic_run(settings, run_id="epic-fail"))
    asyncio.run(fail_epic_run(settings, run_id="epic-fail"))

    assert _run_status(tmp_path, "epic-fail")[:2] == ("epic_failed", "epic")
    assert _stage_count(tmp_path, "epic-fail") == 0


class StageSpec:
    def __init__(
        self,
        *,
        stage_name: str,
        run_status: str,
        create_run: Callable[[str], Awaitable[None]],
        start: Callable[[str], Awaitable[str]] | None = None,
        complete: Callable[[str, str], Awaitable[None]] | None = None,
        fail: Callable[[str, str], Awaitable[None]] | None = None,
        complete_report: dict[str, object] | None = None,
        fail_report: dict[str, object] | None = None,
        artifact_type: str | None = None,
        artifact_path: Path | None = None,
        skip_report: dict[str, object] | None = None,
        skip_error: str | None = None,
    ) -> None:
        self.stage_name = stage_name
        self.run_status = run_status
        self.create_run = create_run
        self.start = start
        self.complete = complete
        self.fail = fail
        self.complete_report = complete_report or {}
        self.fail_report = fail_report or {}
        self.artifact_type = artifact_type
        self.artifact_path = artifact_path
        self.skip_report = skip_report or {}
        self.skip_error = skip_error


async def _noop() -> None:
    return None


def _pipeline_stage_specs(tmp_path: Path) -> list[StageSpec]:
    settings = Settings(app_dir=tmp_path)

    def create(run_id: str) -> Awaitable[None]:
        return create_pipeline_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=42,
        )

    return [
        StageSpec(
            stage_name="implement",
            run_status="implement_complete",
            artifact_type="implementation_report",
            artifact_path=tmp_path / "implement.json",
            create_run=create,
            fail_report={"error": "implement failed"},
        ),
        StageSpec(
            stage_name="verify",
            run_status="verify_complete",
            artifact_type="verification_report",
            artifact_path=tmp_path / "verify.json",
            create_run=create,
            complete_report={"status": "passed"},
            fail_report={"status": "failed", "error": "verify failed"},
        ),
        StageSpec(
            stage_name="pr",
            run_status="pr_complete",
            artifact_type="pr_draft",
            artifact_path=tmp_path / "pr.json",
            create_run=create,
            complete_report={"pr_url": "https://github.com/owner/repo/pull/2"},
            fail_report={"error": "pr failed"},
        ),
    ]


def _repair_stage_specs(tmp_path: Path) -> list[StageSpec]:
    settings = Settings(app_dir=tmp_path)

    def create(run_id: str) -> Awaitable[None]:
        return create_repair_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            pr_number=8,
        )

    return [
        StageSpec(
            stage_name="repair",
            run_status="repair_complete",
            artifact_type="repair_report",
            artifact_path=tmp_path / "repair.json",
            create_run=create,
            start=lambda run_id: start_repair_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_repair_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "repair.json",
            ),
            fail=lambda run_id, stage_id: fail_repair_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="repair failed",
            ),
        ),
        StageSpec(
            stage_name="push",
            run_status="push_complete",
            create_run=create,
            start=lambda run_id: start_repair_push_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_repair_push_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
            ),
            fail=lambda run_id, stage_id: fail_repair_push_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="push failed",
            ),
        ),
    ]


def _assert_stage_complete(
    settings: Settings,
    tmp_path: Path,
    spec: StageSpec,
) -> None:
    run_id = f"{spec.stage_name}-complete"
    asyncio.run(spec.create_run(run_id))
    if spec.start is not None and spec.complete is not None:
        stage_id = asyncio.run(spec.start(run_id))
        assert _run_status(tmp_path, run_id)[:2] == (
            f"{spec.stage_name}_running",
            spec.stage_name,
        )
        assert _stage(tmp_path, stage_id) == (spec.stage_name, "running", None)
        asyncio.run(spec.complete(run_id, stage_id))
        assert _run_status(tmp_path, run_id)[:2] == (spec.run_status, spec.stage_name)
        assert _stage(tmp_path, stage_id) == (spec.stage_name, "complete", None)
        if spec.artifact_type is not None and spec.artifact_path is not None:
            assert _artifacts(tmp_path, run_id) == [
                (spec.artifact_type, str(spec.artifact_path))
            ]
        assert asyncio.run(get_run_state(settings, run_id)) is not None
        return

    async def body(log_path: Path):
        return spec.complete_report, spec.artifact_path

    asyncio.run(run_stage_lifecycle(settings, run_id, spec.stage_name, body))

    assert _run_status(tmp_path, run_id)[:2] == (spec.run_status, spec.stage_name)
    assert _stage_by_name(tmp_path, run_id, spec.stage_name) == ("complete", None)
    if spec.artifact_type is not None and spec.artifact_path is not None:
        assert _artifacts(tmp_path, run_id) == [
            (spec.artifact_type, str(spec.artifact_path))
        ]
    assert asyncio.run(get_run_state(settings, run_id)) is not None


def _assert_stage_failed(
    settings: Settings,
    tmp_path: Path,
    spec: StageSpec,
) -> None:
    run_id = f"{spec.stage_name}-failed"
    error = f"{spec.stage_name} failed"
    asyncio.run(spec.create_run(run_id))
    if spec.start is not None and spec.fail is not None:
        stage_id = asyncio.run(spec.start(run_id))
        asyncio.run(spec.fail(run_id, stage_id))
        assert _run_status(tmp_path, run_id)[:2] == (
            f"{spec.stage_name}_failed",
            spec.stage_name,
        )
        assert _stage(tmp_path, stage_id) == (spec.stage_name, "failed", error)
        assert asyncio.run(get_run_state(settings, run_id)) is not None
        return

    async def body(log_path: Path):
        if spec.stage_name == "verify":
            return spec.fail_report, spec.artifact_path
        raise RuntimeError(error)

    if spec.stage_name == "verify":
        asyncio.run(run_stage_lifecycle(settings, run_id, spec.stage_name, body))
    else:
        with pytest.raises(RuntimeError, match=error):
            asyncio.run(run_stage_lifecycle(settings, run_id, spec.stage_name, body))

    assert _run_status(tmp_path, run_id)[:2] == (
        f"{spec.stage_name}_failed",
        spec.stage_name,
    )
    expected_error = error if spec.stage_name == "verify" else "Stage failed. See local run logs."
    assert _stage_by_name(tmp_path, run_id, spec.stage_name) == ("failed", expected_error)
    assert asyncio.run(get_run_state(settings, run_id)) is not None


def _assert_stage_skipped(
    settings: Settings,
    tmp_path: Path,
    spec: StageSpec,
) -> None:
    run_id = f"{spec.stage_name}-skipped"
    asyncio.run(spec.create_run(run_id))

    async def body(log_path: Path):
        raise StageSkipped(spec.skip_error or "skipped", spec.skip_report, spec.artifact_path)

    asyncio.run(run_stage_lifecycle(settings, run_id, spec.stage_name, body))

    assert _run_status(tmp_path, run_id)[:2] == (spec.run_status, spec.stage_name)
    assert _stage_by_name(tmp_path, run_id, spec.stage_name) == ("skipped", spec.skip_error)
    if spec.artifact_type is not None and spec.artifact_path is not None:
        assert _artifacts(tmp_path, run_id) == [
            (spec.artifact_type, str(spec.artifact_path))
        ]
    assert asyncio.run(get_run_state(settings, run_id)) is not None


def _run_status(
    tmp_path: Path,
    run_id: str,
) -> tuple[str, str | None, str, int | None, int | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT status, current_stage, workflow_type, issue_number, pr_number
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return row


def _run_pr_url(tmp_path: Path, run_id: str) -> str | None:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            "SELECT pr_url FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return row[0]


def _stage(tmp_path: Path, stage_id: str) -> tuple[str, str, str | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            WHERE id = ?
            """,
            (stage_id,),
        ).fetchone()
    return row


def _stage_by_name(
    tmp_path: Path,
    run_id: str,
    stage_name: str,
) -> tuple[str, str | None]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = ?
            """,
            (run_id, stage_name),
        ).fetchone()
    return row


def _stage_count(tmp_path: Path, run_id: str) -> int:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        row = db.execute(
            "SELECT COUNT(*) FROM workflow_stages WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return row[0]


def _artifacts(tmp_path: Path, run_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        rows = db.execute(
            """
            SELECT artifact_type, file_path
            FROM artifacts
            WHERE run_id = ?
            ORDER BY artifact_type, file_path
            """,
            (run_id,),
        ).fetchall()
    return rows
