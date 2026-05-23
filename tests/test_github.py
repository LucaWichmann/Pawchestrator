import asyncio

import httpx
import pytest

from pawchestrator.github import GitHubIssueClient, parse_issue_url


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
