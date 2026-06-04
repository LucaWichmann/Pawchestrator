import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawchestrator.config import PipelineSettings, Settings, SmartRoutingSettings
from pawchestrator.approval_gate import (
    has_approval_event,
    signal_approval,
    signal_approval_decision,
)
from pawchestrator.db import (
    get_run_warnings,
    set_run_pr_url,
    upsert_worktree_record,
)
from pawchestrator.github import PAWCHESTRATOR_LABELS
from pawchestrator.pipeline import VerificationFailedError, run_pipeline
from pawchestrator.stage_lifecycle import run_stage_lifecycle


@pytest.fixture(autouse=True)
def _no_network_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pawchestrator.pipeline.get_gh_token", lambda: "test-token")
    monkeypatch.setattr("pawchestrator.pipeline.GitHubIssueClient", _NoNetworkGitHubClient)


def test_run_pipeline_runs_all_stages_and_marks_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert "[scout] done - readiness: ready" in progress
    assert "[verify] done - status: passed" in progress
    assert "[pr] done - https://github.com/owner/repo/pull/99" in progress
    assert progress[-1] == "https://github.com/owner/repo/pull/99"

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY started_at
            """,
            (result.run_id,),
        ).fetchall()

    assert run == ("completed", "pr", "https://github.com/owner/repo/pull/99")
    assert stages == [
        ("snapshot", "complete"),
        ("scout", "complete"),
        ("plan", "complete"),
        ("implement", "complete"),
        ("verify", "complete"),
        ("pr", "complete"),
    ]


def test_should_skip_plan_evaluates_all_smart_routing_conditions() -> None:
    settings = Settings(
        pipeline=PipelineSettings(
            smart_routing=SmartRoutingSettings(
                enabled=True,
                skip_plan_when=["implement"],
                require_readiness=["ready"],
                require_max_risk="medium",
            )
        )
    )

    from pawchestrator.pipeline import _should_skip_plan

    assert _should_skip_plan(
        settings,
        {
            "next_recommended_stage": "implement",
            "readiness": "ready",
            "risk": "medium",
        },
    ) is True
    assert _should_skip_plan(
        settings,
        {
            "next_recommended_stage": "plan",
            "readiness": "ready",
            "risk": "medium",
        },
    ) is False
    assert _should_skip_plan(
        settings,
        {
            "next_recommended_stage": "implement",
            "readiness": "blocked",
            "risk": "medium",
        },
    ) is False
    assert _should_skip_plan(
        settings,
        {
            "next_recommended_stage": "implement",
            "readiness": "ready",
            "risk": "high",
        },
    ) is False


def test_run_pipeline_uses_micro_plan_when_smart_routing_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(
            plan_approval=False,
            smart_routing=SmartRoutingSettings(enabled=True),
        ),
    )
    calls: list[str] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        scout_report={
            "readiness": "ready",
            "next_recommended_stage": "implement",
            "risk": "low",
        },
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )
    warnings = asyncio.run(get_run_warnings(settings, result.run_id))

    assert calls == ["snapshot", "scout", "micro_plan", "implement", "verify", "pr"]
    assert [warning["code"] for warning in warnings] == [
        "smart_routing_plan_skipped"
    ]
    assert warnings[0]["stage_name"] == "plan"


def test_run_pipeline_uses_full_plan_when_smart_routing_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(
            plan_approval=False,
            smart_routing=SmartRoutingSettings(enabled=False),
        ),
    )
    calls: list[str] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        scout_report={
            "readiness": "ready",
            "next_recommended_stage": "implement",
            "risk": "low",
        },
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]


def test_run_pipeline_uses_full_plan_when_smart_routing_conditions_do_not_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(
            plan_approval=False,
            smart_routing=SmartRoutingSettings(enabled=True),
        ),
    )
    calls: list[str] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        scout_report={
            "readiness": "ready",
            "next_recommended_stage": "implement",
            "risk": "high",
        },
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]


def test_run_pipeline_confirm_skip_pauses_for_micro_plan_approval_then_full_plan_on_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = Settings(
            app_dir=tmp_path,
            pipeline=PipelineSettings(
                plan_approval=False,
                smart_routing=SmartRoutingSettings(enabled=True, confirm_skip=True),
            ),
        )
        calls: list[str] = []
        replan_feedback: list[list[dict[str, object]] | None] = []
        _patch_successful_stages(
            monkeypatch,
            calls,
            replan_feedback=replan_feedback,
            scout_report={
                "readiness": "ready",
                "next_recommended_stage": "implement",
                "risk": "low",
            },
        )

        task = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings)
        run_id = next(iter(_run_ids(settings)))
        assert calls == ["snapshot", "scout", "micro_plan"]
        _write_rejections(
            settings,
            run_id,
            [{"attempt": 1, "feedback": "Use the full planner."}],
        )
        assert signal_approval_decision(run_id, "reject") is True

        await _wait_for_plan_call_count(calls, 1)
        await _wait_for_approval_gate(settings)
        assert signal_approval(run_id, approved=True) is True

        result = await task
        assert result.pr_url == "https://github.com/owner/repo/pull/99"
        assert calls == [
            "snapshot",
            "scout",
            "micro_plan",
            "plan",
            "implement",
            "verify",
            "pr",
        ]
        assert replan_feedback == [
            [{"attempt": 1, "feedback": "Use the full planner."}]
        ]

    asyncio.run(scenario())


def test_run_pipeline_pauses_for_plan_approval_then_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = Settings(app_dir=tmp_path)
        calls: list[str] = []
        _patch_successful_stages(monkeypatch, calls)

        task = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings)

        with sqlite3.connect(settings.database_path) as db:
            status = db.execute("SELECT status FROM workflow_runs").fetchone()[0]

        assert status == "awaiting_plan_approval"
        assert calls == ["snapshot", "scout", "plan"]
        assert signal_approval(next(iter(_run_ids(settings))), approved=True) is True

        result = await task
        assert result.pr_url == "https://github.com/owner/repo/pull/99"
        assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
        assert has_approval_event(result.run_id) is False

    asyncio.run(scenario())


def test_run_pipeline_abort_plan_approval_marks_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = Settings(app_dir=tmp_path)
        calls: list[str] = []
        _patch_successful_stages(monkeypatch, calls)

        task = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings)
        run_id = next(iter(_run_ids(settings)))
        assert signal_approval(run_id, approved=False) is True

        with pytest.raises(RuntimeError, match="plan approval aborted"):
            await task

        with sqlite3.connect(settings.database_path) as db:
            run = db.execute(
                "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        assert run == ("failed", "plan")
        assert calls == ["snapshot", "scout", "plan"]
        assert has_approval_event(run_id) is False

    asyncio.run(scenario())


def test_run_pipeline_rejects_plan_then_replans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = Settings(app_dir=tmp_path)
        calls: list[str] = []
        replan_feedback: list[list[dict[str, object]] | None] = []
        _patch_successful_stages(monkeypatch, calls, replan_feedback=replan_feedback)

        task = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings)
        run_id = next(iter(_run_ids(settings)))
        _write_rejections(
            settings,
            run_id,
            [{"attempt": 1, "feedback": "Use axios instead of fetch."}],
        )
        assert signal_approval_decision(run_id, "reject") is True

        await _wait_for_plan_call_count(calls, 2)
        await _wait_for_approval_gate(settings)
        assert signal_approval(run_id, approved=True) is True

        result = await task
        assert result.pr_url == "https://github.com/owner/repo/pull/99"
        assert calls == ["snapshot", "scout", "plan", "plan", "implement", "verify", "pr"]
        assert replan_feedback == [
            None,
            [{"attempt": 1, "feedback": "Use axios instead of fetch."}],
        ]

    asyncio.run(scenario())


def test_run_pipeline_reject_at_max_attempts_marks_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = Settings(
            app_dir=tmp_path,
            pipeline=PipelineSettings(plan_approval_max_attempts=1),
        )
        calls: list[str] = []
        _patch_successful_stages(monkeypatch, calls)

        task = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings)
        run_id = next(iter(_run_ids(settings)))
        _write_rejections(
            settings,
            run_id,
            [{"attempt": 1, "feedback": "Use axios instead of fetch."}],
        )
        assert signal_approval_decision(run_id, "reject") is True

        with pytest.raises(RuntimeError, match=r"plan approval max attempts \(1\) reached"):
            await task

        with sqlite3.connect(settings.database_path) as db:
            run = db.execute(
                "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            plan_stage = db.execute(
                "SELECT error FROM workflow_stages WHERE run_id = ? AND stage_name = 'plan'",
                (run_id,),
            ).fetchone()
        assert run == ("failed", "plan")
        assert plan_stage == ("plan approval max attempts (1) reached",)
        assert calls == ["snapshot", "scout", "plan"]

    asyncio.run(scenario())


def test_run_pipeline_plan_approval_timeout_marks_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(plan_approval_timeout_hours=0),
    )
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    with pytest.raises(RuntimeError, match="plan approval timed out"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT id, status, current_stage FROM workflow_runs"
        ).fetchone()
        plan_stage = db.execute(
            "SELECT error FROM workflow_stages WHERE run_id = ? AND stage_name = 'plan'",
            (run[0],),
        ).fetchone()
    assert run[1:] == ("failed", "plan")
    assert plan_stage == ("plan approval timed out",)


def test_concurrent_plan_approval_gates_are_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings_a = Settings(app_dir=tmp_path / "a")
        settings_b = Settings(app_dir=tmp_path / "b")
        calls: list[str] = []
        _patch_successful_stages(monkeypatch, calls)

        task_a = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings_a,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings_a)
        run_a = next(iter(_run_ids(settings_a)))

        task_b = asyncio.create_task(
            run_pipeline(
                "https://github.com/owner/repo/issues/43",
                settings_b,
                repo_path=tmp_path,
            )
        )
        await _wait_for_approval_gate(settings_b)
        run_b = next(iter(_run_ids(settings_b)))

        assert signal_approval(run_a, approved=True) is True
        result_a = await asyncio.wait_for(task_a, timeout=1)
        assert result_a.run_id == run_a
        assert has_approval_event(run_b) is True
        assert task_b.done() is False

        assert signal_approval(run_b, approved=True) is True
        result_b = await asyncio.wait_for(task_b, timeout=1)
        assert result_b.run_id == run_b

    asyncio.run(scenario())


def test_run_pipeline_skips_verify_for_non_code_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_implement_with_worktree(monkeypatch, calls, tmp_path / "worktree")
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: True,
    )
    monkeypatch.setattr(
        "pawchestrator.pipeline._changed_files",
        lambda worktree_path, base_branch: ["docs/usage.md"],
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            progress=progress.append,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert "[verify] skipped - no code files changed" in progress
    artifact_path = settings.app_dir / "runs" / result.run_id / "verification_report.json"
    report = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert report == {
        "schema": "pawchestrator.verification_report.v1",
        "status": "skipped",
        "skip_reason": (
            "Verification skipped - only non-code files changed: docs/usage.md"
        ),
        "commands": [],
    }
    with sqlite3.connect(settings.database_path) as db:
        verify_stage = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (result.run_id,),
        ).fetchone()

    assert verify_stage == (
        "skipped",
        "Verification skipped - only non-code files changed: docs/usage.md",
    )


def test_run_pipeline_defers_verification_without_running_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            defer_verification=True,
            progress=progress.append,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "pr"]
    assert "[verify] skipped - verification deferred to epic level" in progress
    artifact_path = settings.app_dir / "runs" / result.run_id / "verification_report.json"
    report = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert report == {
        "schema": "pawchestrator.verification_report.v1",
        "status": "skipped",
        "skip_reason": "verification deferred to epic level",
        "commands": [],
    }
    with sqlite3.connect(settings.database_path) as db:
        verify_stage = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (result.run_id,),
        ).fetchone()

    assert verify_stage == ("skipped", "verification deferred to epic level")


def test_run_pipeline_verify_non_code_changes_forces_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_non_code_changes=True, plan_approval=False),
    )
    calls: list[str] = []
    diff_checks: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: diff_checks.append("checked") or True,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert diff_checks == []


def test_run_pipeline_git_diff_error_falls_back_to_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_implement_with_worktree(monkeypatch, calls, tmp_path / "worktree")
    monkeypatch.setattr(
        "pawchestrator.pipeline.all_files_match_non_code",
        lambda worktree_path, base_branch, patterns: False,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]


def test_run_pipeline_stops_on_failure_and_marks_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    async def fake_plan(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        rejections: list[dict[str, object]] | None = None,
    ):
        calls.append("plan")
        async def body(log_path: Path):
            raise RuntimeError("plan exploded")

        await run_stage_lifecycle(settings, run_id, "plan", body)
        raise RuntimeError("plan exploded")

    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)

    with pytest.raises(RuntimeError, match="plan exploded"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=progress.append,
            )
        )

    assert calls == ["snapshot", "scout", "plan"]
    assert "[plan] FAILED: plan exploded" in progress
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT id, status, current_stage FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            WHERE run_id = ?
            ORDER BY stage_name
            """,
            (run[0],),
        ).fetchall()

    assert run[1:] == ("failed", "plan")
    assert ("plan", "failed", "Stage failed. See local run logs.") in stages
    assert ("implement", "pending", None) in stages


def test_run_pipeline_passes_allow_empty_commit_to_pr_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    allow_empty_commit_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        allow_empty_commit_values=allow_empty_commit_values,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            allow_empty_commit=True,
        )
    )

    assert allow_empty_commit_values == [True]


def test_run_pipeline_passes_allow_dirty_to_initial_implement_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    allow_dirty_existing_worktree_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        allow_dirty_existing_worktree_values=allow_dirty_existing_worktree_values,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
            allow_dirty_existing_worktree=True,
        )
    )

    assert allow_dirty_existing_worktree_values == [True]


def test_run_pipeline_blocks_pr_when_verify_fails_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_repair_attempts=0, plan_approval=False),
    )
    calls: list[str] = []
    progress: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed"])

    with pytest.raises(VerificationFailedError, match="verification failed"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
                progress=progress.append,
            )
        )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify"]
    assert "[verify] done - status: failed" in progress
    assert not any(call == "pr" for call in calls)
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status, error
            FROM workflow_stages
            ORDER BY stage_name, started_at
            """
        ).fetchall()

    assert run == ("failed", "verify", None)
    assert ("verify", "failed", "test exited 1: assertion failed") in stages
    assert ("pr", "pending", None) in stages


def test_run_pipeline_repairs_failed_verify_then_creates_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    repair_contexts: list[tuple[dict[str, object] | None, int | None]] = []
    allow_dirty_existing_worktree_values: list[bool] = []
    _patch_successful_stages(
        monkeypatch,
        calls,
        repair_contexts=repair_contexts,
        allow_dirty_existing_worktree_values=allow_dirty_existing_worktree_values,
    )
    _patch_verify_results(monkeypatch, calls, ["failed", "passed"])

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "verify",
        "implement",
        "verify",
        "pr",
    ]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert allow_dirty_existing_worktree_values == [False, True]
    assert repair_contexts == [
        (None, None),
        (
            {
                "status": "failed",
                "commands": [
                    {
                        "command": "pytest",
                        "exit_code": 1,
                        "stdout_summary": "",
                        "stderr_summary": "assertion failed",
                    }
                ],
                "skip_reason": None,
                "verify_log_tail": (
                    "[command] test: pytest\n"
                    "[exit_code] 1\n"
                    "[stdout]\n\n"
                    "[stderr]\n"
                    "assertion failed\n"
                ),
            },
            1,
        ),
    ]
    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage, pr_url FROM workflow_runs"
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status
            FROM workflow_stages
            WHERE stage_name IN ('implement', 'verify', 'pr')
            ORDER BY started_at
            """
        ).fetchall()

    assert run == ("completed", "pr", "https://github.com/owner/repo/pull/99")
    assert stages == [
        ("implement", "complete"),
        ("verify", "failed"),
        ("implement", "complete"),
        ("verify", "complete"),
        ("pr", "complete"),
    ]


def test_run_pipeline_reconciles_checkbox_marks_after_implement_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed", "passed"])

    async def fake_reconcile(
        settings: Settings,
        run_id: str,
        client: object,
    ) -> tuple[bool, list[dict[str, object]]]:
        calls.append("reconcile")
        return False, []

    monkeypatch.setattr(
        "pawchestrator.pipeline.reconcile_checkbox_marks",
        fake_reconcile,
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "reconcile",
        "verify",
        "implement",
        "reconcile",
        "verify",
        "reconcile",
        "pr",
    ]


def test_run_pipeline_records_checkbox_reconciliation_warning_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)

    async def fake_reconcile(
        settings: Settings,
        run_id: str,
        client: object,
    ) -> tuple[bool, list[dict[str, object]]]:
        raise RuntimeError("lost patch state")

    monkeypatch.setattr(
        "pawchestrator.pipeline.reconcile_checkbox_marks",
        fake_reconcile,
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )
    warnings = asyncio.run(get_run_warnings(settings, result.run_id))

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert [warning["code"] for warning in warnings] == [
        "checkbox_reconciliation_failed",
        "checkbox_reconciliation_failed",
    ]
    assert warnings[0]["stage_name"] == "implement"
    assert warnings[1]["stage_name"] == "verify"
    assert "lost patch state" in warnings[0]["message"]


def test_run_pipeline_exhausts_verify_repairs_and_leaves_pr_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(verify_repair_attempts=2, plan_approval=False),
    )
    calls: list[str] = []
    _patch_successful_stages(monkeypatch, calls)
    _patch_verify_results(monkeypatch, calls, ["failed", "failed", "failed"])

    with pytest.raises(VerificationFailedError):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )

    assert calls == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "verify",
        "implement",
        "verify",
        "implement",
        "verify",
    ]
    with sqlite3.connect(settings.database_path) as db:
        pr_stage = db.execute(
            """
            SELECT status
            FROM workflow_stages
            WHERE stage_name = 'pr'
            """
        ).fetchone()

    assert pr_stage == ("pending",)


def test_run_pipeline_updates_stage_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    client = _RecordingLabelClient()
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert client.ensure_calls == [
        ("owner", "repo", name, color)
        for name, color in PAWCHESTRATOR_LABELS.values()
    ]
    assert client.added_labels == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:implementing",
        "pawchestrator:verifying",
        "pawchestrator:pr-ready",
    ]
    assert "pawchestrator:failed" not in client.added_labels


def test_run_pipeline_sets_failed_label_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    client = _RecordingLabelClient()
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    async def fake_plan(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        rejections: list[dict[str, object]] | None = None,
    ):
        calls.append("plan")
        async def body(log_path: Path):
            raise RuntimeError("plan exploded")

        await run_stage_lifecycle(settings, run_id, "plan", body)
        raise RuntimeError("plan exploded")

    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)

    with pytest.raises(RuntimeError, match="plan exploded"):
        asyncio.run(
            run_pipeline(
                "https://github.com/owner/repo/issues/42",
                settings,
                repo_path=tmp_path,
            )
        )

    assert client.added_labels == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:failed",
    ]


def test_run_pipeline_label_errors_do_not_abort_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path, pipeline=PipelineSettings(plan_approval=False))
    calls: list[str] = []
    client = _RecordingLabelClient(fail_add=True)
    _patch_successful_stages(monkeypatch, calls)
    monkeypatch.setattr(
        "pawchestrator.pipeline._post_initial_run_comment",
        _fake_post_initial_run_comment(client),
    )

    result = asyncio.run(
        run_pipeline(
            "https://github.com/owner/repo/issues/42",
            settings,
            repo_path=tmp_path,
        )
    )

    assert calls == ["snapshot", "scout", "plan", "implement", "verify", "pr"]
    assert result.pr_url == "https://github.com/owner/repo/pull/99"
    assert client.add_attempts == [
        "pawchestrator:running",
        "pawchestrator:scouting",
        "pawchestrator:planning",
        "pawchestrator:implementing",
        "pawchestrator:verifying",
        "pawchestrator:pr-ready",
    ]


class _NoNetworkGitHubClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.comment_id = 123

    async def post_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> int:
        return self.comment_id

    async def edit_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        body: str,
    ) -> None:
        return None

    async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
        return None

    async def add_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        return None

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        return None

    async def fetch_issue_body(self, reference: object) -> str:
        return ""

    async def patch_issue_body(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> None:
        return None


class _RecordingLabelClient:
    def __init__(self, *, fail_add: bool = False) -> None:
        self.fail_add = fail_add
        self.ensure_calls: list[tuple[str, str, str, str]] = []
        self.added_labels: list[str] = []
        self.add_attempts: list[str] = []
        self.removed_labels: list[str] = []

    async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
        self.ensure_calls.append((owner, repo, name, color))

    async def add_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.add_attempts.append(name)
        if self.fail_add:
            raise RuntimeError("label add failed")
        self.added_labels.append(name)

    async def remove_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.removed_labels.append(name)


def _fake_post_initial_run_comment(client: _RecordingLabelClient):
    async def fake_post_initial_run_comment(settings: Settings, run_id: str):
        return client

    return fake_post_initial_run_comment


async def _wait_for_approval_gate(settings: Settings) -> None:
    for _ in range(100):
        run_ids = _run_ids(settings)
        if run_ids and any(has_approval_event(run_id) for run_id in run_ids):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("approval gate did not open")


async def _wait_for_plan_call_count(calls: list[str], count: int) -> None:
    for _ in range(100):
        if calls.count("plan") >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"plan stage was not called {count} times")


def _run_ids(settings: Settings) -> set[str]:
    if not settings.database_path.exists():
        return set()
    with sqlite3.connect(settings.database_path) as db:
        rows = db.execute("SELECT id FROM workflow_runs").fetchall()
    return {str(row[0]) for row in rows}


def _patch_successful_stages(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    allow_empty_commit_values: list[bool] | None = None,
    repair_contexts: list[tuple[dict[str, object] | None, int | None]] | None = None,
    allow_dirty_existing_worktree_values: list[bool] | None = None,
    replan_feedback: list[list[dict[str, object]] | None] | None = None,
    scout_report: dict[str, object] | None = None,
) -> None:
    async def fake_snapshot(issue_url: str, settings: Settings, *, run_id: str):
        calls.append("snapshot")
        artifact_path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"

        async def body(log_path: Path):
            return {"number": 42, "title": "Issue"}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "snapshot", body)

    async def fake_scout(run_id: str, settings: Settings, *, repo_path: Path | None = None):
        calls.append("scout")
        artifact_path = settings.app_dir / "runs" / run_id / "scout_report.json"

        async def body(log_path: Path):
            return scout_report or {"readiness": "ready"}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "scout", body)

    async def fake_plan(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        rejections: list[dict[str, object]] | None = None,
    ):
        calls.append("plan")
        if replan_feedback is not None:
            replan_feedback.append(rejections)
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_plan.json"

        async def body(log_path: Path):
            return {}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "plan", body)

    async def fake_micro_plan(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
    ):
        calls.append("micro_plan")
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_plan.json"

        async def body(log_path: Path):
            return {}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "plan", body)

    async def fake_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        repair_context: dict[str, object] | None = None,
        repair_attempt: int | None = None,
        allow_dirty_existing_worktree: bool = False,
    ):
        calls.append("implement")
        if repair_contexts is not None:
            repair_contexts.append((repair_context, repair_attempt))
        if allow_dirty_existing_worktree_values is not None:
            allow_dirty_existing_worktree_values.append(allow_dirty_existing_worktree)
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_report.json"

        async def body(log_path: Path):
            return {}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "implement", body)

    async def fake_verify(
        run_id: str,
        settings: Settings,
        *,
        base_branch: str = "main",
    ):
        calls.append("verify")
        artifact_path = settings.app_dir / "runs" / run_id / "verification_report.json"

        async def body(log_path: Path):
            return {"status": "passed"}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "verify", body)

    async def fake_pr(
        run_id: str,
        settings: Settings,
        *,
        allow_empty_commit: bool = False,
    ):
        calls.append("pr")
        if allow_empty_commit_values is not None:
            allow_empty_commit_values.append(allow_empty_commit)
        artifact_path = settings.app_dir / "runs" / run_id / "pr_draft.json"

        async def body(log_path: Path):
            return {"pr_url": "https://github.com/owner/repo/pull/99"}, artifact_path

        result = await run_stage_lifecycle(settings, run_id, "pr", body)
        await set_run_pr_url(
            settings,
            run_id=run_id,
            pr_url=str(result.report["pr_url"]),
        )
        return result

    monkeypatch.setattr("pawchestrator.pipeline.snapshot_issue", fake_snapshot)
    monkeypatch.setattr("pawchestrator.pipeline.run_scout", fake_scout)
    monkeypatch.setattr("pawchestrator.pipeline.run_plan", fake_plan)
    monkeypatch.setattr("pawchestrator.pipeline.run_micro_plan", fake_micro_plan)
    monkeypatch.setattr("pawchestrator.pipeline.run_implement", fake_implement)
    monkeypatch.setattr("pawchestrator.pipeline.run_verify", fake_verify)
    monkeypatch.setattr("pawchestrator.pipeline.run_pr", fake_pr)


def _write_rejections(
    settings: Settings,
    run_id: str,
    rejections: list[dict[str, object]],
) -> None:
    path = settings.app_dir / "runs" / run_id / "plan_rejections.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rejections), encoding="utf-8")


def _patch_implement_with_worktree(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    worktree_path: Path,
) -> None:
    async def fake_implement(
        run_id: str,
        settings: Settings,
        *,
        repo_path: Path | None = None,
        repair_context: dict[str, object] | None = None,
        repair_attempt: int | None = None,
        allow_dirty_existing_worktree: bool = False,
    ):
        calls.append("implement")
        worktree_path.mkdir(parents=True, exist_ok=True)
        await upsert_worktree_record(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=42,
            branch="issue-42",
            path=worktree_path,
        )
        artifact_path = settings.app_dir / "runs" / run_id / "implementation_report.json"

        async def body(log_path: Path):
            return {}, artifact_path

        return await run_stage_lifecycle(settings, run_id, "implement", body)

    monkeypatch.setattr("pawchestrator.pipeline.run_implement", fake_implement)


def _patch_verify_results(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    statuses: list[str],
) -> None:
    remaining = list(statuses)

    async def fake_verify(
        run_id: str,
        settings: Settings,
        *,
        base_branch: str = "main",
    ):
        calls.append("verify")
        if not remaining:
            raise AssertionError("unexpected verify call")
        status = remaining.pop(0)
        passed = status == "passed"
        artifact_path = settings.app_dir / "runs" / run_id / "verification_report.json"
        report = {
            "schema": "pawchestrator.verification_report.v1",
            "status": status,
            "commands": [
                {
                    "command": "pytest",
                    "exit_code": 0 if passed else 1,
                    "stdout_summary": "",
                    "stderr_summary": "" if passed else "assertion failed",
                }
            ],
            "skip_reason": None,
        }

        async def body(log_path: Path):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                (
                    "[command] test: pytest\n"
                    f"[exit_code] {0 if passed else 1}\n"
                    "[stdout]\n\n"
                    "[stderr]\n"
                    f"{'' if passed else 'assertion failed'}\n"
                ),
                encoding="utf-8",
            )
            return report, artifact_path

        return await run_stage_lifecycle(settings, run_id, "verify", body)

    monkeypatch.setattr("pawchestrator.pipeline.run_verify", fake_verify)
