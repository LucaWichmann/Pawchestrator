import asyncio
import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from pawchestrator.config import Settings
from pawchestrator.db import create_review_run, get_run_state, init_db
from pawchestrator.review import (
    ReviewContext,
    build_review_prompt,
    parse_review_artifact,
    run_review,
)
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


def test_build_review_prompt_requires_structured_artifact() -> None:
    prompt = build_review_prompt(
        owner="owner",
        repo="repo",
        pr_number=42,
        description="PR body",
        diff="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 existing
+added
""",
    )

    assert (
        '"inline_comments": [{"file": "path/to/file", "line": 123, '
        '"body": "comment"}]'
    ) in prompt
    assert '"summary": "short review summary"' in prompt
    assert '"verdict": "REQUEST_CHANGES|APPROVE|COMMENT"' in prompt
    assert (
        '"suggested_issues": [{"hint": "optional follow-up issue hint", '
        '"file": "path/to/file", "line": 123}]'
    ) in prompt
    assert "REQUEST_CHANGES" in prompt
    assert "APPROVE" in prompt
    assert "COMMENT" in prompt
    assert "PR body" in prompt
    assert "diff --git a/app.py b/app.py" in prompt
    assert "Commentable added lines:" in prompt
    assert "app.py:2 | added" in prompt
    assert "Do not use diff positions" in prompt


def test_build_review_prompt_fallback_keeps_line_anchor_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pawchestrator.review.load_skill", lambda *_args: None)

    prompt = build_review_prompt(
        owner="owner",
        repo="repo",
        pr_number=42,
        description="PR body",
        diff="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 existing
+added
""",
    )

    assert '"inline_comments": [{"file": "path/to/file", "line": 123' in prompt
    assert "Commentable added lines:" in prompt
    assert "app.py:2 | added" in prompt
    assert "Do not use diff positions" in prompt


def test_parse_review_artifact_validates_and_normalizes_report() -> None:
    report = parse_review_artifact(
        {
            "inline_comments": [{"file": "app.py", "line": 12, "body": "Fix this."}],
            "summary": "One issue.",
            "verdict": "REQUEST_CHANGES",
            "suggested_issues": [
                {"hint": "Add regression test", "file": "app.py", "line": 12}
            ],
        }
    )

    assert report == {
        "schema": "pawchestrator.review_report.v1",
        "inline_comments": [{"file": "app.py", "line": 12, "body": "Fix this."}],
        "summary": "One issue.",
        "verdict": "REQUEST_CHANGES",
        "suggested_issues": [
            {"hint": "Add regression test", "file": "app.py", "line": 12}
        ],
    }


def test_parse_review_artifact_rejects_suggested_issue_without_inline_comment() -> None:
    with pytest.raises(ValueError, match="must match an inline comment"):
        parse_review_artifact(
            {
                "inline_comments": [
                    {"file": "app.py", "line": 12, "body": "Fix this."}
                ],
                "summary": "One issue.",
                "verdict": "REQUEST_CHANGES",
                "suggested_issues": [
                    {"hint": "Add regression test", "file": "app.py", "line": 13}
                ],
            }
        )


@pytest.mark.parametrize(
    "issue",
    [
        {"file": "app.py", "line": 12},
        {"hint": "Add regression test", "line": 12},
        {"hint": "Add regression test", "file": "app.py"},
    ],
)
def test_parse_review_artifact_rejects_suggested_issue_missing_required_field(
    issue: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="review suggested issue"):
        parse_review_artifact(
            {
                "inline_comments": [
                    {"file": "app.py", "line": 12, "body": "Fix this."}
                ],
                "summary": "One issue.",
                "verdict": "REQUEST_CHANGES",
                "suggested_issues": [issue],
            }
        )


@pytest.mark.parametrize("verdict", ["REQUEST_CHANGES", "APPROVE", "COMMENT"])
def test_parse_review_artifact_accepts_allowed_verdicts(verdict: str) -> None:
    report = parse_review_artifact(
        {
            "inline_comments": [],
            "summary": "Looks fine.",
            "verdict": verdict,
            "suggested_issues": [],
        }
    )

    assert report["verdict"] == verdict


def test_parse_review_artifact_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError, match="verdict must be one of"):
        parse_review_artifact(
            {
                "inline_comments": [],
                "summary": "Looks fine.",
                "verdict": "CHANGES_REQUESTED",
                "suggested_issues": [],
            }
        )


def test_run_review_fetches_context_before_invoking_runner_and_writes_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(
        create_review_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            pr_number=42,
        )
    )
    events: list[str] = []
    runner = FakeRunner(events)

    async def fake_fetch_review_context(
        *,
        owner: str,
        repo: str,
        pr_number: int,
        cwd: Path,
    ) -> ReviewContext:
        events.append("fetch")
        assert (owner, repo, pr_number) == ("owner", "repo", 42)
        return ReviewContext(description="Description", diff="Diff")

    monkeypatch.setattr(
        "pawchestrator.review.fetch_review_context",
        fake_fetch_review_context,
    )
    post_calls: list[str] = []

    async def fake_run_review_post(post_run_id: str, settings: Settings) -> object:
        post_calls.append(post_run_id)
        return object()

    monkeypatch.setattr(
        "pawchestrator.review_post.run_review_post",
        fake_run_review_post,
    )

    result = asyncio.run(run_review(run_id, settings, runner=runner))

    assert events == ["fetch", "run"]
    assert post_calls == [run_id]
    assert result.artifact_path == tmp_path / "runs" / run_id / "review_report.json"
    assert json.loads(result.artifact_path.read_text(encoding="utf-8")) == {
        "schema": "pawchestrator.review_report.v1",
        "inline_comments": [{"file": "app.py", "line": 4, "body": "Use pathlib."}],
        "summary": "One issue.",
        "verdict": "COMMENT",
        "suggested_issues": [],
    }
    assert runner.tasks[0].stage_name == "review"
    assert "Description" in runner.tasks[0].prompt
    assert "Diff" in runner.tasks[0].prompt

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            """
            SELECT workflow_type, pr_number, status, current_stage
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'review'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path
            FROM artifacts
            WHERE run_id = ? AND artifact_type = 'review_report'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("review", 42, "review_complete", "review")
    assert stage == ("complete", None)
    assert artifact == ("review_report", str(result.artifact_path))


def test_get_run_state_returns_review_stage_status(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_review_stage(settings, run_id))

    state = asyncio.run(get_run_state(settings, run_id))

    assert state is not None
    assert state["workflow_type"] == "review"
    assert state["pr_number"] == 42
    assert state["status"] == "review_running"
    assert state["current_stage"] == "review"
    assert state["stages"][0]["stage_name"] == "review"
    assert state["stages"][0]["status"] == "running"


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.tasks: list[RunnerTask] = []

    async def check_health(self) -> tuple[bool, str]:
        return True, "ok"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.events.append("run")
        self.tasks.append(task)
        return RunnerResult(
            exit_code=0,
            stdout="",
            stderr="",
            artifact={
                "inline_comments": [
                    {"file": "app.py", "line": 4, "body": "Use pathlib."}
                ],
                "summary": "One issue.",
                "verdict": "COMMENT",
                "suggested_issues": [],
            },
        )


async def _insert_review_stage(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, pr_number, workflow_type, status,
              current_stage, created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', NULL, 42, 'review', 'review_running',
              'review', '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at
            )
            VALUES (
              'stage-review', ?, 'review', 'running', '2026-05-23T00:00:00Z'
            )
            """,
            (run_id,),
        )
        await db.commit()
