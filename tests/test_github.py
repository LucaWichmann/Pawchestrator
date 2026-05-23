import asyncio

import httpx
import pytest

from pawchestrator.github import GitHubIssueClient, format_run_comment, parse_issue_url


def test_parse_issue_url_accepts_github_issue_url() -> None:
    reference = parse_issue_url("https://github.com/LucaWichmann/Pawchestrator/issues/2")

    assert reference.owner == "LucaWichmann"
    assert reference.repo == "Pawchestrator"
    assert reference.number == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/LucaWichmann/Pawchestrator/pull/2",
        "https://example.com/LucaWichmann/Pawchestrator/issues/2",
        "https://github.com/LucaWichmann/Pawchestrator/issues/nope",
    ],
)
def test_parse_issue_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        parse_issue_url(url)


def test_github_issue_client_fetches_snapshot_and_paginated_comments() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/repos/owner/repo/issues/42":
            return httpx.Response(
                200,
                json={
                    "title": "Add memoization",
                    "body": "Body",
                    "labels": [{"name": "enhancement"}],
                    "assignees": [{"login": "octo"}],
                },
            )
        if request.url.query == b"page=2":
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "bob"},
                        "body": "Second",
                        "created_at": "2026-05-23T00:01:00Z",
                    }
                ],
            )
        if request.url.path == "/repos/owner/repo/issues/42/comments":
            return httpx.Response(
                200,
                headers={
                    "Link": '<https://api.github.test/repos/owner/repo/issues/42/comments?page=2>; rel="next"'
                },
                json=[
                    {
                        "user": {"login": "alice"},
                        "body": "First",
                        "created_at": "2026-05-23T00:00:00Z",
                    }
                ],
            )
        return httpx.Response(404, json={"message": "not found"})

    reference = parse_issue_url("https://github.com/owner/repo/issues/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    snapshot = asyncio.run(client.fetch_snapshot(reference))

    assert snapshot["schema"] == "pawchestrator.issue_snapshot.v1"
    assert snapshot["labels"] == ["enhancement"]
    assert snapshot["assignees"] == ["octo"]
    assert snapshot["comments"] == [
        {
            "author": "alice",
            "body": "First",
            "created_at": "2026-05-23T00:00:00Z",
        },
        {
            "author": "bob",
            "body": "Second",
            "created_at": "2026-05-23T00:01:00Z",
        },
    ]
    assert len(requests) == 3


def test_github_issue_client_posts_comment_and_returns_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/issues/42/comments"
        assert request.headers["Authorization"] == "Bearer token"
        assert request.read() == b'{"body":"Body"}'
        return httpx.Response(201, json={"id": 99})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    comment_id = asyncio.run(client.post_comment("owner", "repo", 42, "Body"))

    assert comment_id == 99
    assert len(requests) == 1


def test_github_issue_client_edits_comment() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PATCH"
        assert request.url.path == "/repos/owner/repo/issues/comments/99"
        assert request.read() == b'{"body":"Updated"}'
        return httpx.Response(200, json={"id": 99})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.edit_comment("owner", "repo", 99, "Updated"))

    assert len(requests) == 1


def test_format_run_comment_includes_structured_run_state() -> None:
    body = format_run_comment(
        {
            "id": "run-123",
            "owner": "owner",
            "repo": "repo",
            "issue_number": 42,
            "branch": "paw/issue-42",
            "status": "pr_complete",
            "current_stage": "pr",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:10:00Z",
            "pr_url": "https://github.com/owner/repo/pull/99",
            "stages": [
                {"stage_name": "snapshot", "status": "complete"},
                {"stage_name": "pr", "status": "complete"},
            ],
        }
    )

    assert "- Run ID: `run-123`" in body
    assert "- Branch: `paw/issue-42`" in body
    assert "- Current stage: `pr`" in body
    assert "- PR: https://github.com/owner/repo/pull/99" in body
    assert "| Snapshot | `complete` |" in body


def test_format_run_comment_includes_failure_details() -> None:
    body = format_run_comment(
        {
            "id": "run-123",
            "owner": "owner",
            "repo": "repo",
            "issue_number": 42,
            "status": "failed",
            "current_stage": "plan",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:05:00Z",
            "stages": [
                {"stage_name": "plan", "status": "failed", "error": "plan exploded"},
            ],
        }
    )

    assert "- Failed stage: `plan`" in body
    assert "- Error: `plan exploded`" in body
