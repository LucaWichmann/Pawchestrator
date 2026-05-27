import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_epic_run,
    complete_implement_run,
    complete_pr_run,
    complete_repair_push_run,
    complete_repair_run,
    complete_review_issues_run,
    complete_review_post_run,
    complete_review_run,
    complete_snapshot_run,
    complete_verify_run,
    create_epic_run,
    create_pipeline_run,
    create_repair_run,
    create_review_run,
    create_snapshot_run,
    fail_epic_run,
    fail_implement_run,
    fail_pr_run,
    fail_repair_push_run,
    fail_repair_run,
    fail_review_issues_run,
    fail_review_post_run,
    fail_review_run,
    fail_snapshot_run,
    fail_verify_run,
    get_run_state,
    skip_pr_stage,
    skip_review_issues_run,
    skip_verify_run,
    start_epic_run,
    start_implement_run,
    start_pr_run,
    start_repair_push_run,
    start_repair_run,
    start_review_issues_run,
    start_review_post_run,
    start_review_run,
    start_verify_run,
)


def test_snapshot_run_lifecycle_start_complete_and_fail(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    complete_stage_id = asyncio.run(
        create_snapshot_run(
            settings,
            run_id="snapshot-complete",
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )
    fail_stage_id = asyncio.run(
        create_snapshot_run(
            settings,
            run_id="snapshot-fail",
            owner="owner",
            repo="repo",
            issue_number=43,
        )
    )

    assert _run_status(tmp_path, "snapshot-complete") == (
        "snapshot_running",
        "snapshot",
        "pipeline",
        42,
        None,
    )
    assert _stage(tmp_path, complete_stage_id) == ("snapshot", "running", None)

    asyncio.run(
        complete_snapshot_run(
            settings,
            run_id="snapshot-complete",
            stage_id=complete_stage_id,
            artifact_path=tmp_path / "snapshot.json",
        )
    )
    asyncio.run(
        fail_snapshot_run(
            settings,
            run_id="snapshot-fail",
            stage_id=fail_stage_id,
            error="snapshot failed",
        )
    )

    assert _run_status(tmp_path, "snapshot-complete")[:2] == (
        "snapshot_complete",
        "snapshot",
    )
    assert _stage(tmp_path, complete_stage_id) == ("snapshot", "complete", None)
    assert _artifacts(tmp_path, "snapshot-complete") == [
        ("issue_snapshot", str(tmp_path / "snapshot.json"))
    ]
    assert _run_status(tmp_path, "snapshot-fail")[:2] == (
        "snapshot_failed",
        "snapshot",
    )
    assert _stage(tmp_path, fail_stage_id) == (
        "snapshot",
        "failed",
        "snapshot failed",
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
            start=lambda run_id: start_verify_run(settings, run_id=run_id),
            complete=lambda _run_id, _stage_id: _noop(),
            fail=lambda _run_id, _stage_id: _noop(),
            skip=lambda run_id, stage_id: skip_verify_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "verify-skipped.json",
                reason="verification skipped",
            ),
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


def test_review_run_lifecycle_complete_fail_and_skip(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    for spec in _review_stage_specs(tmp_path):
        _assert_stage_complete(settings, tmp_path, spec)
        _assert_stage_failed(settings, tmp_path, spec)

    _assert_stage_skipped(
        settings,
        tmp_path,
        StageSpec(
            stage_name="issues",
            run_status="issues_skipped",
            artifact_type="created_issues_report",
            artifact_path=tmp_path / "issues-skipped.json",
            create_run=lambda run_id: create_review_run(
                settings,
                run_id=run_id,
                owner="owner",
                repo="repo",
                pr_number=17,
            ),
            start=lambda run_id: start_review_issues_run(settings, run_id=run_id),
            complete=lambda _run_id, _stage_id: _noop(),
            fail=lambda _run_id, _stage_id: _noop(),
            skip=lambda run_id, stage_id: skip_review_issues_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "issues-skipped.json",
                reason="issues skipped",
            ),
            skip_error="issues skipped",
        ),
    )


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
        start: Callable[[str], Awaitable[str]],
        complete: Callable[[str, str], Awaitable[None]],
        fail: Callable[[str, str], Awaitable[None]],
        artifact_type: str | None = None,
        artifact_path: Path | None = None,
        skip: Callable[[str, str], Awaitable[None]] | None = None,
        skip_error: str | None = None,
    ) -> None:
        self.stage_name = stage_name
        self.run_status = run_status
        self.create_run = create_run
        self.start = start
        self.complete = complete
        self.fail = fail
        self.artifact_type = artifact_type
        self.artifact_path = artifact_path
        self.skip = skip
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
            start=lambda run_id: start_implement_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_implement_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "implement.json",
            ),
            fail=lambda run_id, stage_id: fail_implement_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="implement failed",
            ),
        ),
        StageSpec(
            stage_name="verify",
            run_status="verify_complete",
            artifact_type="verification_report",
            artifact_path=tmp_path / "verify.json",
            create_run=create,
            start=lambda run_id: start_verify_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_verify_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "verify.json",
                passed=True,
            ),
            fail=lambda run_id, stage_id: fail_verify_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="verify failed",
            ),
        ),
        StageSpec(
            stage_name="pr",
            run_status="pr_complete",
            artifact_type="pr_draft",
            artifact_path=tmp_path / "pr.json",
            create_run=create,
            start=lambda run_id: start_pr_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_pr_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "pr.json",
                pr_url="https://github.com/owner/repo/pull/2",
            ),
            fail=lambda run_id, stage_id: fail_pr_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="pr failed",
            ),
        ),
    ]


def _review_stage_specs(tmp_path: Path) -> list[StageSpec]:
    settings = Settings(app_dir=tmp_path)

    def create(run_id: str) -> Awaitable[None]:
        return create_review_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            pr_number=7,
        )

    return [
        StageSpec(
            stage_name="review",
            run_status="review_complete",
            artifact_type="review_report",
            artifact_path=tmp_path / "review.json",
            create_run=create,
            start=lambda run_id: start_review_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_review_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "review.json",
            ),
            fail=lambda run_id, stage_id: fail_review_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="review failed",
            ),
        ),
        StageSpec(
            stage_name="post",
            run_status="post_complete",
            create_run=create,
            start=lambda run_id: start_review_post_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_review_post_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
            ),
            fail=lambda run_id, stage_id: fail_review_post_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                error="post failed",
            ),
        ),
        StageSpec(
            stage_name="issues",
            run_status="issues_complete",
            artifact_type="created_issues_report",
            artifact_path=tmp_path / "issues.json",
            create_run=create,
            start=lambda run_id: start_review_issues_run(settings, run_id=run_id),
            complete=lambda run_id, stage_id: complete_review_issues_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "issues.json",
            ),
            fail=lambda run_id, stage_id: fail_review_issues_run(
                settings,
                run_id=run_id,
                stage_id=stage_id,
                artifact_path=tmp_path / "issues-failed.json",
                error="issues failed",
            ),
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


def _assert_stage_failed(
    settings: Settings,
    tmp_path: Path,
    spec: StageSpec,
) -> None:
    run_id = f"{spec.stage_name}-failed"
    error = f"{spec.stage_name} failed"
    asyncio.run(spec.create_run(run_id))
    stage_id = asyncio.run(spec.start(run_id))
    asyncio.run(spec.fail(run_id, stage_id))

    assert _run_status(tmp_path, run_id)[:2] == (
        f"{spec.stage_name}_failed",
        spec.stage_name,
    )
    assert _stage(tmp_path, stage_id) == (spec.stage_name, "failed", error)
    assert asyncio.run(get_run_state(settings, run_id)) is not None


def _assert_stage_skipped(
    settings: Settings,
    tmp_path: Path,
    spec: StageSpec,
) -> None:
    assert spec.skip is not None
    run_id = f"{spec.stage_name}-skipped"
    asyncio.run(spec.create_run(run_id))
    stage_id = asyncio.run(spec.start(run_id))
    asyncio.run(spec.skip(run_id, stage_id))

    assert _run_status(tmp_path, run_id)[:2] == (spec.run_status, spec.stage_name)
    assert _stage(tmp_path, stage_id) == (spec.stage_name, "skipped", spec.skip_error)
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
