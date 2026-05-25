import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings
from pawchestrator.github import IssueReference


class FakeGitHubIssueClient:
    checkbox_headings: list[str] | None = None

    def __init__(self, token: str) -> None:
        assert token == "fake-token"

    async def fetch_snapshot(
        self,
        reference: IssueReference,
        checkbox_headings: list[str],
    ) -> dict[str, object]:
        FakeGitHubIssueClient.checkbox_headings = checkbox_headings
        return {
            "schema": "pawchestrator.issue_snapshot.v1",
            "owner": reference.owner,
            "repo": reference.repo,
            "number": reference.number,
            "title": "Add handler memoization",
            "body": "Issue body",
            "checkboxes": [],
            "labels": ["enhancement"],
            "assignees": ["octo"],
            "comments": [],
            "source_url": reference.source_url,
            "fetched_at": "2026-05-23T00:00:00Z",
        }


def test_issue_snapshot_command_writes_artifact_and_records_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))
    monkeypatch.setattr("pawchestrator.issues.get_gh_token", lambda: "fake-token")
    monkeypatch.setattr(
        "pawchestrator.issues.GitHubIssueClient",
        FakeGitHubIssueClient,
    )

    result = CliRunner().invoke(
        cli.app,
        ["issue", "snapshot", "https://github.com/owner/repo/issues/42"],
    )

    assert result.exit_code == 0
    assert "Run ID:" in result.output
    assert "Snapshot:" in result.output
    assert "Issue: #42 - Add handler memoization" in result.output

    run_id = _output_value(result.output, "Run ID")
    artifact_path = tmp_path / "runs" / run_id / "issue.snapshot.json"
    snapshot = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert snapshot["schema"] == "pawchestrator.issue_snapshot.v1"
    assert snapshot["owner"] == "owner"
    assert snapshot["repo"] == "repo"
    assert snapshot["number"] == 42
    assert snapshot["checkboxes"] == []
    assert FakeGitHubIssueClient.checkbox_headings == [
        "Acceptance Criteria",
        "AC",
        "Definition of Gone",
        "DoD",
        "Checklist",
        "Requirements",
        "Tasks",
    ]

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            "SELECT status, error FROM workflow_stages WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            "SELECT artifact_type, file_path FROM artifacts WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    assert run == ("snapshot_complete", "snapshot")
    assert stage == ("complete", None)
    assert artifact == ("issue_snapshot", str(artifact_path))


def test_issue_snapshot_command_marks_run_failed_after_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    def fail_token() -> str:
        raise RuntimeError("no token")

    monkeypatch.setattr("pawchestrator.issues.get_gh_token", fail_token)

    result = CliRunner().invoke(
        cli.app,
        ["issue", "snapshot", "https://github.com/owner/repo/issues/42"],
    )

    assert result.exit_code == 1
    assert "Snapshot failed: no token" in result.output

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs"
        ).fetchone()
        stage = db.execute(
            "SELECT status, error FROM workflow_stages"
        ).fetchone()

    assert run == ("snapshot_failed", "snapshot")
    assert stage == ("failed", "no token")


def _output_value(output: str, label: str) -> str:
    prefix = f"{label}: "
    return next(line.removeprefix(prefix) for line in output.splitlines() if line.startswith(prefix))
