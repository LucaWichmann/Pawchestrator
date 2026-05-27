import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings
from pawchestrator.db import create_review_run, get_run_state, get_run_warnings
from pawchestrator.github import PAWCHESTRATOR_LABELS
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
                {"path": "app.py", "line": 2, "side": "RIGHT", "body": "Fix this."}
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


def test_run_review_post_uses_source_line_not_diff_position_for_new_file(
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
                    {
                        "file": "pawchestrator/lifecycle.py",
                        "line": 38,
                        "body": "Duplicate time helper.",
                    }
                ],
                "summary": "One non-blocking issue.",
                "verdict": "COMMENT",
                "suggested_issues": [],
            }
        ),
        encoding="utf-8",
    )
    client = FakeReviewClient()
    added_lines = [
        '"""Lifecycle transitions for workflow stages."""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import UTC, datetime",
        "from pathlib import Path",
        "from typing import TYPE_CHECKING",
        "from uuid import uuid4",
        "",
        "import aiosqlite",
        "",
        "from pawchestrator.run_lifecycle import PIPELINE_STAGES, REPAIR_STAGES, REVIEW_STAGES",
        "",
        "if TYPE_CHECKING:",
        "    from pawchestrator.config import Settings",
        "",
        "",
        "TERMINAL_RUN_STATUSES = (",
        '    "completed",',
        '    "failed",',
        '    "grill_complete",',
        '    "grill_failed",',
        '    "epic_complete",',
        '    "epic_failed",',
        '    "post_complete",',
        '    "post_failed",',
        '    "issues_complete",',
        '    "issues_failed",',
        '    "issues_skipped",',
        '    "review_failed",',
        '    "repair_complete",',
        '    "repair_failed",',
        '    "push_complete",',
        '    "push_failed",',
        ")",
        'STALE_RUN_ERROR = "Run aborted: Pawchestrator stopped before this run finished."',
        "",
        "def _utc_now_iso() -> str:",
        '    return datetime.now(UTC).isoformat().replace("+00:00", "Z")',
    ]
    diff = "\n".join(
        [
            "diff --git a/pawchestrator/lifecycle.py b/pawchestrator/lifecycle.py",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/pawchestrator/lifecycle.py",
            "@@ -0,0 +1,39 @@",
            *(f"+{line}" for line in added_lines),
            "",
        ]
    )

    result = asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text=diff,
        )
    )

    assert result.submitted_comments == 1
    assert result.skipped_comments == 0
    assert client.payloads[0]["comments"] == [
        {
            "path": "pawchestrator/lifecycle.py",
            "line": 38,
            "side": "RIGHT",
            "body": "Duplicate time helper.",
        }
    ]


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


def test_run_review_post_downgrades_self_request_changes_to_comment(
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
    _write_review_report(
        settings,
        run_id=run_id,
        inline_comments=[{"file": "app.py", "line": 2, "body": "Fix this."}],
        summary="One blocking issue.",
        verdict="REQUEST_CHANGES",
    )
    client = FakeReviewClient(author="alice", authenticated_user="alice")

    result = asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text=_one_line_diff(),
        )
    )

    assert result.submitted_comments == 1
    assert client.payloads[0]["event"] == "COMMENT"
    assert client.payloads[0]["body"].startswith(
        "Pawchestrator verdict: REQUEST_CHANGES\n\n"
    )
    assert client.payloads[0]["comments"] == [
        {"path": "app.py", "line": 2, "side": "RIGHT", "body": "Fix this."}
    ]
    assert client.labels_added == [
        ("owner", "repo", 42, PAWCHESTRATOR_LABELS["review-changes-requested"][0])
    ]
    assert client.labels_removed == [
        ("owner", "repo", 42, PAWCHESTRATOR_LABELS["review-approved"][0])
    ]
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == [
        "review_verdict_downgraded_for_self_pr"
    ]


def test_run_review_post_downgrades_self_approval_to_comment(
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
    _write_review_report(
        settings,
        run_id=run_id,
        inline_comments=[],
        summary="Clean change.",
        verdict="APPROVE",
    )
    client = FakeReviewClient(author="alice", authenticated_user="alice")

    result = asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text="",
        )
    )

    assert result.submitted_comments == 0
    assert client.payloads[0]["event"] == "COMMENT"
    assert client.payloads[0]["body"].startswith("Pawchestrator verdict: APPROVE\n\n")
    assert client.labels_added == [
        ("owner", "repo", 42, PAWCHESTRATOR_LABELS["review-approved"][0])
    ]
    assert client.labels_removed == [
        ("owner", "repo", 42, PAWCHESTRATOR_LABELS["review-changes-requested"][0])
    ]


def test_run_review_post_keeps_non_self_request_changes_event(
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
    _write_review_report(
        settings,
        run_id=run_id,
        inline_comments=[],
        summary="One blocking issue.",
        verdict="REQUEST_CHANGES",
    )
    client = FakeReviewClient(author="alice", authenticated_user="bob")

    asyncio.run(
        run_review_post(
            run_id,
            settings,
            client=client,  # type: ignore[arg-type]
            diff_text="",
        )
    )

    assert client.payloads[0]["event"] == "REQUEST_CHANGES"
    assert client.payloads[0]["body"] == "One blocking issue."
    assert client.labels_added == []
    assert client.labels_removed == []


class FakeReviewClient:
    def __init__(
        self,
        *,
        author: str = "alice",
        authenticated_user: str = "bob",
    ) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.author = author
        self.authenticated_user = authenticated_user
        self.labels_added: list[tuple[str, str, int, str]] = []
        self.labels_removed: list[tuple[str, str, int, str]] = []

    async def fetch_pr_author_login(self, owner: str, repo: str, number: int) -> str:
        return self.author

    async def fetch_authenticated_user_login(self) -> str:
        return self.authenticated_user

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

    async def add_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        self.labels_added.append((owner, repo, issue_number, name))

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        self.labels_removed.append((owner, repo, issue_number, name))


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


def _write_review_report(
    settings: Settings,
    *,
    run_id: str,
    inline_comments: list[dict[str, Any]],
    summary: str,
    verdict: str,
) -> None:
    report_path = review_report_path(settings, run_id)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.review_report.v1",
                "inline_comments": inline_comments,
                "summary": summary,
                "verdict": verdict,
                "suggested_issues": [],
            }
        ),
        encoding="utf-8",
    )


def _one_line_diff() -> str:
    return """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 one
+two
 three
"""
