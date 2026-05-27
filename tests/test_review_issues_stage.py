import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from pawchestrator.config import Settings
from pawchestrator.db import (
    complete_review_post_run,
    create_review_run,
    get_run_state,
    start_review_post_run,
)
from pawchestrator.github import GENERATED_BY_FOOTER
from pawchestrator.review import review_report_path
from pawchestrator.server import create_app
from pawchestrator.sessions import save_sessions


def test_review_issues_stage_skips_empty_suggestions(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _prepare_post_complete_review_run(settings, suggested_issues=[])

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/create-issues",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "issues_skipped"
    assert payload["current_stage"] == "issues"
    assert payload["created_issue_urls"] == []
    assert _stage(payload, "issues")["status"] == "skipped"


def test_review_issues_stage_creates_suggested_issues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _prepare_post_complete_review_run(
        settings,
        suggested_issues=["First follow-up", "Second follow-up"],
    )
    fake_client = FakeCreateIssueClient()
    _patch_github_client(monkeypatch, fake_client)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/create-issues",
            headers=_token_headers(),
        )
        status_response = client.get(
            "/runs/run-123/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert fake_client.created == [
        ("owner", "repo", "First follow-up", GENERATED_BY_FOOTER),
        ("owner", "repo", "Second follow-up", GENERATED_BY_FOOTER),
    ]
    payload = status_response.json()
    assert payload["status"] == "issues_complete"
    assert payload["created_issue_urls"] == [
        "https://github.com/owner/repo/issues/1",
        "https://github.com/owner/repo/issues/2",
    ]
    assert _stage(payload, "issues")["status"] == "complete"


def test_review_issues_stage_marks_failed_after_partial_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _prepare_post_complete_review_run(
        settings,
        suggested_issues=["First follow-up", "Second follow-up"],
    )
    fake_client = FakeCreateIssueClient(fail_after=1)
    _patch_github_client(monkeypatch, fake_client)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/create-issues",
            headers=_token_headers(),
        )

    assert response.status_code == 502
    state = asyncio.run(get_run_state(settings, "run-123"))
    assert state is not None
    assert state["status"] == "issues_failed"
    assert state["created_issue_urls"] == ["https://github.com/owner/repo/issues/1"]
    assert _stage(state, "issues")["status"] == "failed"


def test_review_issues_endpoint_requires_complete_post_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    asyncio.run(
        create_review_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            pr_number=42,
        )
    )
    _write_review_report(settings, suggested_issues=["Follow-up"])

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/create-issues",
            headers=_token_headers(),
        )

    assert response.status_code == 409


class FakeCreateIssueClient:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.fail_after = fail_after
        self.created: list[tuple[str, str, str, str | None]] = []

    async def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str | None = None,
    ) -> str:
        if self.fail_after is not None and len(self.created) >= self.fail_after:
            raise RuntimeError("github failed")
        self.created.append((owner, repo, title, body))
        return f"https://github.com/{owner}/{repo}/issues/{len(self.created)}"


def _prepare_post_complete_review_run(
    settings: Settings,
    *,
    suggested_issues: list[str],
) -> None:
    asyncio.run(
        create_review_run(
            settings,
            run_id="run-123",
            owner="owner",
            repo="repo",
            pr_number=42,
        )
    )
    stage_id = asyncio.run(start_review_post_run(settings, run_id="run-123"))
    asyncio.run(
        complete_review_post_run(
            settings,
            run_id="run-123",
            stage_id=stage_id,
        )
    )
    _write_review_report(settings, suggested_issues=suggested_issues)


def _write_review_report(settings: Settings, *, suggested_issues: list[str]) -> None:
    report_path = review_report_path(settings, "run-123")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.review_report.v1",
                "inline_comments": [],
                "summary": "Summary.",
                "verdict": "COMMENT",
                "suggested_issues": suggested_issues,
            }
        ),
        encoding="utf-8",
    )


def _stage(payload: dict[str, object], stage_name: str) -> dict[str, object]:
    stages = payload["stages"]
    assert isinstance(stages, list)
    stage = next(
        stage
        for stage in stages
        if isinstance(stage, dict) and stage["stage_name"] == stage_name
    )
    return stage


def _seed_token(settings: Settings, token: str = "known-token") -> None:
    save_sessions(settings, {"tokens": [token]})


def _token_headers() -> dict[str, str]:
    return {"X-Pawchestrator-Token": "known-token"}


def _patch_github_client(monkeypatch, client: FakeCreateIssueClient) -> None:
    monkeypatch.setattr("pawchestrator.server.get_gh_token", lambda: "token")
    monkeypatch.setattr("pawchestrator.server.GitHubIssueClient", lambda _token: client)
