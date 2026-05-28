import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_epic_architect_run,
    get_run_warnings,
    insert_repo_registration,
)
from pawchestrator.epic_scout import run_epic_scout
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(self, artifact: dict[str, object] | None = None) -> None:
        self.artifact = artifact or {
            "relevant_files": [
                {
                    "path": "pawchestrator/epic.py",
                    "reason": "Epic workflow entry point.",
                    "snippet": "async def run_epic(...): ...",
                }
            ],
            "tech_context": "FastAPI backend with SQLite workflow state.",
        }
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return True, "ok"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.task = task
        return RunnerResult(
            exit_code=0,
            stdout=json.dumps({"result": self.artifact}),
            stderr="",
            artifact=self.artifact,
        )


def test_run_epic_scout_writes_report_and_records_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "CONTEXT.md").write_text("FastAPI backend", encoding="utf-8")
    (repo_path / "pawchestrator").mkdir()
    (repo_path / "pawchestrator" / "epic.py").write_text("def run_epic(): pass", encoding="utf-8")
    asyncio.run(_insert_run_and_snapshot(settings, run_id))
    asyncio.run(
        insert_repo_registration(
            settings,
            owner="owner",
            repo="repo",
            local_path=repo_path,
        )
    )
    runner = FakeRunner()

    result = asyncio.run(
        run_epic_scout(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            runner=runner,
        )
    )

    assert runner.task is not None
    assert runner.task.cwd == repo_path.resolve()
    assert runner.task.stage_name == "epic_scout"
    assert "Issue body is the primary signal" in runner.task.prompt
    assert "CONTEXT.md" in runner.task.prompt
    assert result.artifact_path == tmp_path / "runs" / run_id / "epic_scout_report.json"
    report = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert report["relevant_files"][0]["path"] == "pawchestrator/epic.py"
    assert report["tech_context"] == "FastAPI backend with SQLite workflow state."

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'epic_scout'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'epic_scout_report'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("epic_scout_complete", "epic_scout")
    assert stage == ("complete", None)
    assert artifact == ("epic_scout_report", str(result.artifact_path))


def test_run_epic_scout_unregistered_repo_warns_and_empty_files(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_and_snapshot(settings, run_id))

    result = asyncio.run(
        run_epic_scout(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            runner=FakeRunner(),
        )
    )

    assert result.report["relevant_files"] == []
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == ["repo_not_registered"]
    assert warnings[0]["stage_name"] == "epic_scout"


def test_run_epic_scout_rejects_malformed_model_output(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    asyncio.run(_insert_run_and_snapshot(settings, run_id))

    with pytest.raises(ValueError, match="tech_context"):
        asyncio.run(
            run_epic_scout(
                "https://github.com/owner/repo/issues/42",
                settings,
                run_id=run_id,
                repo_path=repo_path,
                runner=FakeRunner({"relevant_files": []}),
            )
        )

    with sqlite3.connect(settings.database_path) as db:
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'epic_scout'
            """,
            (run_id,),
        ).fetchone()

    assert stage == ("failed", "Stage failed. See local run logs.")


async def _insert_run_and_snapshot(settings: Settings, run_id: str) -> None:
    await create_epic_architect_run(
        settings,
        run_id=run_id,
        owner="owner",
        repo="repo",
        issue_number=42,
    )
    path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.issue_snapshot.v1",
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "title": "Add epic scout",
                "body": "We currently run epic architect as a scaffold.",
                "comments": [],
            }
        ),
        encoding="utf-8",
    )
