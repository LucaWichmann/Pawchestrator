import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import create_review_run, get_run_state, get_run_warnings
from pawchestrator.review import review_report_path
from pawchestrator.review_post import run_review_post


def test_run_review_post_submits_review_and_warns_for_unmapped_lines(
    tmp_path: Path,
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
    report_path = review_report_path(settings, run_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.review_report.v1",
                "inline_comments": [
                    {"file": "app.py", "line": 2, "body": "Fix this."},
                    {"file": "app.py", "line": 3, "body": "Context only."},
                ],
                "summary": "One blocking issue.",
                "verdict": "REQUEST_CHANGES",
                "suggested_issues": [],
            }
        ),
        encoding="utf-8",
    )
    client = FakeReviewClient()
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 one
+two
 three
"""

    result = asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text=diff,
        )
    )

    assert result.submitted_comments == 1
    assert result.skipped_comments == 1
    assert result.review_id == 99
    assert client.payloads == [
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "body": "One blocking issue.",
            "event": "REQUEST_CHANGES",
            "comments": [
                {"path": "app.py", "position": 2, "body": "Fix this."}
            ],
        }
    ]
    state = asyncio.run(get_run_state(settings, run_id))
    assert state is not None
    assert state["status"] == "post_complete"
    assert state["current_stage"] == "post"
    assert [stage["stage_name"] for stage in state["stages"]] == [
        "review",
        "post",
        "issues",
    ]
    assert state["stages"][1]["status"] == "complete"
    assert state["stages"][2]["status"] == "pending"
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert len(warnings) == 1
    assert warnings[0]["stage_name"] == "post"
    assert warnings[0]["code"] == "review_comment_line_not_in_diff"
    assert "app.py:3" in warnings[0]["message"]


def test_review_run_status_includes_pending_post_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    asyncio.run(
        create_review_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            pr_number=42,
        )
    )

    state = asyncio.run(get_run_state(settings, "run-123"))

    assert state is not None
    assert [stage["stage_name"] for stage in state["stages"]] == [
        "review",
        "post",
        "issues",
    ]


def test_run_review_post_submits_approve_review_without_inline_comments(
    tmp_path: Path,
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
    report_path = review_report_path(settings, run_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.review_report.v1",
                "inline_comments": [],
                "summary": "Clean change.",
                "verdict": "APPROVE",
                "suggested_issues": [],
            }
        ),
        encoding="utf-8",
    )
    client = FakeReviewClient()

    result = asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text="",
        )
    )

    assert result.submitted_comments == 0
    assert result.skipped_comments == 0
    assert result.review_id == 99
    assert client.payloads == [
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "body": "Clean change.",
            "event": "APPROVE",
            "comments": [],
        }
    ]


def test_run_review_post_marks_stage_failed_on_submission_error(tmp_path: Path) -> None:
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
    report_path = review_report_path(settings, run_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.review_report.v1",
                "inline_comments": [],
                "summary": "Summary.",
                "verdict": "COMMENT",
                "suggested_issues": [],
            }
        ),
        encoding="utf-8",
    )

    try:
        asyncio.run(
            run_review_post(
                run_id,
                settings,
                client=FailingReviewClient(),  # type: ignore[arg-type]
                diff_text="",
            )
        )
    except RuntimeError as error:
        assert str(error) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    with sqlite3.connect(settings.database_path) as db:
        row = db.execute(
            """
            SELECT status, current_stage
            FROM workflow_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error
            FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'post'
            """,
            (run_id,),
        ).fetchone()

    assert row == ("post_failed", "post")
    assert stage == ("failed", "Stage failed. See local run logs.")


class FakeReviewClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
        event: str,
        comments: list[dict[str, Any]],
    ) -> int:
        self.payloads.append(
            {
                "owner": owner,
                "repo": repo,
                "number": number,
                "body": body,
                "event": event,
                "comments": comments,
            }
        )
        return 99


class FailingReviewClient:
    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
        event: str,
        comments: list[dict[str, Any]],
    ) -> int:
        raise RuntimeError("boom")
