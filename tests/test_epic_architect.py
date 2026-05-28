import asyncio
import json
import sqlite3
from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.db import (
    create_epic_architect_run,
    get_run_warnings,
    insert_repo_registration,
)
from pawchestrator.epic_architect import (
    normalize_epic_architect_plan,
    run_epic_architect,
    validate_epic_architect_dependencies,
)
from pawchestrator.epic_scout import EPIC_SCOUT_REPORT_SCHEMA
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(self, artifact: dict[str, object] | None = None) -> None:
        self.artifact = artifact or _plan(
            [
                ("Backend: Add API", []),
                ("Frontend: Add UI", [0]),
            ]
        )
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


def test_run_epic_architect_writes_plan_and_records_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
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
        run_epic_architect(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            runner=runner,
        )
    )

    assert runner.task is not None
    assert runner.task.cwd == repo_path.resolve()
    assert runner.task.stage_name == "epic_architect"
    assert "Staff Engineer" in runner.task.prompt
    assert result.artifact_path == tmp_path / "runs" / run_id / "epic_architect_plan.json"
    report = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert report["epic_analysis"] == "Split backend and frontend work."
    assert report["sub_issues"][1]["depends_on_indexes"] == [0]

    with sqlite3.connect(settings.database_path) as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stages = db.execute(
            """
            SELECT stage_name, status, error FROM workflow_stages
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (run_id,),
        ).fetchall()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'epic_architect_plan'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("epic_architect_complete", "epic_architect")
    assert stages[-1] == ("epic_architect", "complete", None)
    assert artifact == ("epic_architect_plan", str(result.artifact_path))


def test_valid_dependency_graph_passes_through_unchanged(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
    plan = normalize_epic_architect_plan(_plan([("A", []), ("B", [0]), ("C", [0, 1])]))

    result = asyncio.run(
        validate_epic_architect_dependencies(settings, run_id=run_id, plan=plan)
    )

    assert [issue["depends_on_indexes"] for issue in result["sub_issues"]] == [
        [],
        [0],
        [0, 1],
    ]
    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_out_of_range_indexes_are_stripped_with_warnings(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
    plan = normalize_epic_architect_plan(_plan([("A", [-1, 3]), ("B", [0])]))

    result = asyncio.run(
        validate_epic_architect_dependencies(settings, run_id=run_id, plan=plan)
    )

    assert [issue["depends_on_indexes"] for issue in result["sub_issues"]] == [
        [],
        [0],
    ]
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == [
        "invalid_dependency",
        "invalid_dependency",
    ]
    assert all(warning["stage_name"] == "epic_architect" for warning in warnings)


def test_self_reference_is_stripped_with_warning(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
    plan = normalize_epic_architect_plan(_plan([("A", [0]), ("B", [0, 1])]))

    result = asyncio.run(
        validate_epic_architect_dependencies(settings, run_id=run_id, plan=plan)
    )

    assert [issue["depends_on_indexes"] for issue in result["sub_issues"]] == [
        [],
        [0],
    ]
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert len(warnings) == 2
    assert {warning["code"] for warning in warnings} == {"invalid_dependency"}


def test_two_node_cycle_is_stripped_with_warnings(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
    plan = normalize_epic_architect_plan(_plan([("A", [1]), ("B", [0])]))

    result = asyncio.run(
        validate_epic_architect_dependencies(settings, run_id=run_id, plan=plan)
    )

    assert [issue["depends_on_indexes"] for issue in result["sub_issues"]] == [[], []]
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert len(warnings) == 2
    assert all("cycle detected" in warning["message"] for warning in warnings)


def test_multi_node_cycle_is_stripped_with_warnings(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run_snapshot_and_scout(settings, run_id))
    plan = normalize_epic_architect_plan(
        _plan([("A", [2]), ("B", [0]), ("C", [1]), ("D", [0])])
    )

    result = asyncio.run(
        validate_epic_architect_dependencies(settings, run_id=run_id, plan=plan)
    )

    assert [issue["depends_on_indexes"] for issue in result["sub_issues"]] == [
        [],
        [],
        [],
        [0],
    ]
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert len(warnings) == 3
    assert all(warning["code"] == "invalid_dependency" for warning in warnings)


def _plan(items: list[tuple[str, list[int]]]) -> dict[str, object]:
    return {
        "epic_analysis": "Split backend and frontend work.",
        "sub_issues": [
            {
                "title": title,
                "description": (
                    f"{title}.\n\n**Acceptance Criteria:**\n- [ ] Complete the work."
                ),
                "depends_on_indexes": depends_on_indexes,
            }
            for title, depends_on_indexes in items
        ],
    }


async def _insert_run_snapshot_and_scout(settings: Settings, run_id: str) -> None:
    await create_epic_architect_run(
        settings,
        run_id=run_id,
        owner="owner",
        repo="repo",
        issue_number=42,
    )
    run_dir = settings.app_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "issue.snapshot.json").write_text(
        json.dumps(
            {
                "schema": "pawchestrator.issue_snapshot.v1",
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "title": "Build feature",
                "body": "Build a full-stack feature.",
                "comments": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "epic_scout_report.json").write_text(
        json.dumps(
            {
                "schema": EPIC_SCOUT_REPORT_SCHEMA,
                "relevant_files": [],
                "tech_context": "FastAPI backend with SQLite workflow state.",
            }
        ),
        encoding="utf-8",
    )
