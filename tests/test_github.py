import asyncio
import json

import httpx
import pytest

from pawchestrator.github import (
    PAWCHESTRATOR_LABELS,
    GitHubIssueClient,
    ensure_pawchestrator_labels,
    format_run_comment,
    parse_issue_url,
)


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


def test_github_issue_client_patches_issue_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PATCH"
        assert request.url.path == "/repos/owner/repo/issues/42"
        assert request.read() == b'{"body":"Updated issue body"}'
        return httpx.Response(200, json={"number": 42})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.patch_issue_body("owner", "repo", 42, "Updated issue body"))

    assert len(requests) == 1


def test_github_issue_client_ensure_label_noops_when_label_exists() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/labels/pawchestrator:running"
        return httpx.Response(200, json={"name": "pawchestrator:running"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.ensure_label("owner", "repo", "pawchestrator:running", "6f42c1"))

    assert len(requests) == 1


def test_github_issue_client_ensure_label_creates_missing_label() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/repos/owner/repo/labels/pawchestrator:running"
            return httpx.Response(404, json={"message": "not found"})
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/labels"
        assert json.loads(request.read()) == {
            "name": "pawchestrator:running",
            "color": "6f42c1",
        }
        return httpx.Response(201, json={"name": "pawchestrator:running"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.ensure_label("owner", "repo", "pawchestrator:running", "6f42c1"))

    assert [request.method for request in requests] == ["GET", "POST"]


def test_github_issue_client_ensure_label_treats_create_conflict_as_noop() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(422, json={"message": "already_exists"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.ensure_label("owner", "repo", "pawchestrator:running", "6f42c1"))

    assert [request.method for request in requests] == ["GET", "POST"]


def test_github_issue_client_adds_label() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/issues/42/labels"
        assert json.loads(request.read()) == {"labels": ["pawchestrator:running"]}
        return httpx.Response(200, json=[{"name": "pawchestrator:running"}])

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.add_label("owner", "repo", 42, "pawchestrator:running"))

    assert len(requests) == 1


def test_github_issue_client_removes_label() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "DELETE"
        assert request.url.path == "/repos/owner/repo/issues/42/labels/pawchestrator:running"
        return httpx.Response(200, json=[{"name": "enhancement"}])

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.remove_label("owner", "repo", 42, "pawchestrator:running"))

    assert len(requests) == 1


def test_github_issue_client_remove_label_treats_missing_label_as_noop() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"message": "not found"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.remove_label("owner", "repo", 42, "pawchestrator:running"))

    assert len(requests) == 1


def test_ensure_pawchestrator_labels_creates_all_labels() -> None:
    calls: list[tuple[str, str, str, str]] = []

    class FakeClient:
        async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
            calls.append((owner, repo, name, color))

    asyncio.run(ensure_pawchestrator_labels(FakeClient(), "owner", "repo"))  # type: ignore[arg-type]

    assert calls == [
        ("owner", "repo", name, color)
        for name, color in PAWCHESTRATOR_LABELS.values()
    ]


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
