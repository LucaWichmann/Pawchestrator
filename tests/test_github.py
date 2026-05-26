import asyncio
import json

import httpx
import pytest

from pawchestrator.github import (
    PAWCHESTRATOR_LABELS,
    GitHubError,
    GitHubIssueClient,
    ensure_pawchestrator_labels,
    format_run_comment,
    parse_diff_positions,
    parse_checkboxes,
    parse_issue_shorthand,
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


def test_parse_issue_shorthand_accepts_owner_repo_number() -> None:
    reference = parse_issue_shorthand("LucaWichmann/Pawchestrator/42")

    assert reference.owner == "LucaWichmann"
    assert reference.repo == "Pawchestrator"
    assert reference.number == 42
    assert (
        reference.source_url
        == "https://github.com/LucaWichmann/Pawchestrator/issues/42"
    )


@pytest.mark.parametrize(
    "issue_ref",
    [
        "LucaWichmann/Pawchestrator",
        "LucaWichmann/Pawchestrator/nope",
        "LucaWichmann/Pawchestrator/0",
    ],
)
def test_parse_issue_shorthand_rejects_invalid_refs(issue_ref: str) -> None:
    with pytest.raises(ValueError):
        parse_issue_shorthand(issue_ref)


def test_github_issue_client_fetches_snapshot_and_paginated_comments() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/repos/owner/repo/issues/42":
            return httpx.Response(
                200,
                json={
                    "title": "Add memoization",
                    "body": "## Acceptance Criteria\n\n- [ ] First\n- [x] Done",
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
                        "in_reply_to_id": 101,
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
                        "id": 101,
                        "user": {"login": "alice"},
                        "body": "First",
                        "created_at": "2026-05-23T00:00:00Z",
                        "in_reply_to_id": None,
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
    assert snapshot["body"] == "## Acceptance Criteria\n\n- [ ] First\n- [x] Done"
    assert snapshot["checkboxes"] == [{"index": 0, "text": "First"}]
    assert snapshot["comments"] == [
        {
            "author": "alice",
            "body": "First",
            "created_at": "2026-05-23T00:00:00Z",
            "in_reply_to_id": None,
        },
        {
            "author": "bob",
            "body": "Second",
            "created_at": "2026-05-23T00:01:00Z",
            "in_reply_to_id": 101,
        },
    ]
    assert len(requests) == 3


def test_parse_checkboxes_returns_empty_without_matching_heading() -> None:
    body = "- [ ] Outside\n\n## Notes\n\n- [ ] Not criteria"

    assert parse_checkboxes(body) == []


def test_parse_checkboxes_includes_unchecked_under_matching_heading() -> None:
    body = "## Acceptance Criteria\n\n- [ ] First\n- [ ] Second"

    assert parse_checkboxes(body) == [
        {"index": 0, "text": "First"},
        {"index": 1, "text": "Second"},
    ]


def test_parse_checkboxes_ignores_wrong_heading() -> None:
    body = "## Notes\n\n- [ ] Ignore\n\n## Tasks\n\n- [ ] Include"

    assert parse_checkboxes(body) == [{"index": 0, "text": "Include"}]


def test_parse_checkboxes_matches_heading_case_insensitively() -> None:
    body = "## acceptance criteria\n\n- [ ] Lowercase heading"

    assert parse_checkboxes(body) == [{"index": 0, "text": "Lowercase heading"}]


def test_parse_checkboxes_uses_custom_headings() -> None:
    body = "## Acceptance Criteria\n\n- [ ] Ignore\n\n## Done When\n\n- [ ] Include"

    assert parse_checkboxes(body, ["Done When"]) == [
        {"index": 0, "text": "Include"}
    ]


def test_parse_checkboxes_excludes_checked_boxes_and_scopes_indexes() -> None:
    body = """## Acceptance Criteria

- [ ] First
- [x] Done
- [X] Also done

## Notes

- [ ] Ignore

## Tasks

- [ ] Second
"""

    assert parse_checkboxes(body) == [
        {"index": 0, "text": "First"},
        {"index": 1, "text": "Second"},
    ]


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


def test_github_issue_client_posts_pull_request_review_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/pulls/42/reviews"
        assert json.loads(request.read()) == {
            "body": "Summary",
            "event": "REQUEST_CHANGES",
            "comments": [
                {"path": "app.py", "position": 3, "body": "Fix this."}
            ],
        }
        return httpx.Response(200, json={"id": 123})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    review_id = asyncio.run(
        client.post_pr_review(
            "owner",
            "repo",
            42,
            body="Summary",
            event="REQUEST_CHANGES",
            comments=[{"path": "app.py", "position": 3, "body": "Fix this."}],
        )
    )

    assert review_id == 123
    assert len(requests) == 1


def test_github_issue_client_omits_empty_pull_request_review_comments() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/pulls/42/reviews"
        assert json.loads(request.read()) == {
            "body": "Clean change.",
            "event": "APPROVE",
        }
        return httpx.Response(200, json={"id": 124})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    review_id = asyncio.run(
        client.post_pr_review(
            "owner",
            "repo",
            42,
            body="Clean change.",
            event="APPROVE",
            comments=[],
        )
    )

    assert review_id == 124
    assert len(requests) == 1


def test_github_issue_client_creates_issue_and_returns_html_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/issues"
        assert json.loads(request.read()) == {"title": "Follow up"}
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/issues/99"},
        )

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    issue_url = asyncio.run(
        client.create_issue("owner", "repo", title="Follow up")
    )

    assert issue_url == "https://github.com/owner/repo/issues/99"
    assert len(requests) == 1


def test_parse_diff_positions_maps_added_lines_to_github_positions() -> None:
    diff = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 one
+two
 three
+four
diff --git a/pkg/util.py b/pkg/util.py
--- a/pkg/util.py
+++ b/pkg/util.py
@@ -10,2 +10,2 @@
-old
+new
 context
"""

    assert parse_diff_positions(diff) == {
        ("app.py", 2): 2,
        ("app.py", 4): 4,
        ("pkg/util.py", 10): 2,
    }


def test_github_issue_client_fetches_paginated_admin_collaborators() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.query == b"permission=admin&page=2":
            return httpx.Response(200, json=[{"login": "bob"}])
        assert request.url.path == "/repos/owner/repo/collaborators"
        assert request.url.query == b"permission=admin"
        return httpx.Response(
            200,
            headers={
                "Link": '<https://api.github.test/repos/owner/repo/collaborators?permission=admin&page=2>; rel="next"'
            },
            json=[{"login": "alice"}, {"name": "missing login"}],
        )

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    collaborators = asyncio.run(client.fetch_admin_collaborators("owner", "repo"))

    assert collaborators == ["alice", "bob"]
    assert [request.method for request in requests] == ["GET", "GET"]


def test_github_issue_client_fetch_admin_collaborators_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/collaborators"
        assert request.url.query == b"permission=admin"
        return httpx.Response(403, json={"message": "forbidden"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="GitHub API error 403: forbidden"):
        asyncio.run(client.fetch_admin_collaborators("owner", "repo"))


def test_github_issue_client_includes_validation_details_in_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/pulls/42/reviews"
        return httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequestReview",
                        "field": "comments",
                        "code": "invalid",
                    },
                    {"message": "Comment is not on a diff line"},
                ],
            },
        )

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(
        GitHubError,
        match=(
            r"GitHub API error 422: Validation Failed: "
            r"PullRequestReview comments invalid; Comment is not on a diff line"
        ),
    ):
        asyncio.run(
            client.post_pr_review(
                "owner",
                "repo",
                42,
                body="Summary",
                event="COMMENT",
                comments=[],
            )
        )


def test_github_issue_client_fetches_issue_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/issues/42"
        return httpx.Response(200, json={"body": "Issue body"})

    reference = parse_issue_shorthand("owner/repo/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    body = asyncio.run(client.fetch_issue_body(reference))

    assert body == "Issue body"
    assert len(requests) == 1


def test_github_issue_client_fetch_issue_body_rejects_non_object_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/issues/42"
        return httpx.Response(200, json=[])

    reference = parse_issue_shorthand("owner/repo/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GitHubError, match="GitHub issue response was not an object"):
        asyncio.run(client.fetch_issue_body(reference))


def test_github_issue_client_fetches_sub_issues() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/issues/42/sub_issues"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 43,
                    "title": "First child",
                    "html_url": "https://github.com/owner/repo/issues/43",
                },
                {
                    "number": 44,
                    "title": "Second child",
                    "html_url": "https://github.com/owner/repo/issues/44",
                },
            ],
        )

    reference = parse_issue_shorthand("owner/repo/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    sub_issues = asyncio.run(client.fetch_sub_issues(reference))

    assert sub_issues == [
        {
            "number": 43,
            "title": "First child",
            "url": "https://github.com/owner/repo/issues/43",
        },
        {
            "number": 44,
            "title": "Second child",
            "url": "https://github.com/owner/repo/issues/44",
        },
    ]
    assert len(requests) == 1


def test_github_issue_client_fetches_pr_head_branch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/pulls/42"
        return httpx.Response(200, json={"head": {"label": "owner:feature-branch"}})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(client.fetch_pr_head_branch("owner", "repo", 42)) == "owner:feature-branch"
    assert len(requests) == 1


def test_github_issue_client_fetches_review_comments_and_pr_diff() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/owner/repo/pulls/42/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "reviewer"},
                        "body": "Fix inline",
                        "path": "app.py",
                        "line": 12,
                        "created_at": "2026-05-26T00:00:00Z",
                    }
                ],
            )
        if request.url.path == "/repos/owner/repo/issues/42/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "reviewer"},
                        "body": "Fix summary",
                        "created_at": "2026-05-26T00:01:00Z",
                    }
                ],
            )
        if request.url.path == "/repos/owner/repo/pulls/42":
            assert request.headers["Accept"] == "application/vnd.github.v3.diff"
            return httpx.Response(200, text="diff --git a/app.py b/app.py\n")
        return httpx.Response(404, json={"message": "not found"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    comments = asyncio.run(client.fetch_review_comments("owner", "repo", 42))
    diff = asyncio.run(client.fetch_pr_diff("owner", "repo", 42))

    assert comments["inline_comments"][0]["body"] == "Fix inline"
    assert comments["top_level_comments"][0]["body"] == "Fix summary"
    assert diff == "diff --git a/app.py b/app.py\n"


def test_github_issue_client_fetches_changes_requested_reviewers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/pulls/42/reviews"
        return httpx.Response(
            200,
            json=[
                {"state": "COMMENTED", "user": {"login": "ignored"}},
                {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
                {"state": "CHANGES_REQUESTED", "user": {"login": "alice"}},
                {"state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
                {"state": "APPROVED", "user": {"login": "carol"}},
            ],
        )

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    reviewers = asyncio.run(
        client.fetch_changes_requested_reviewers("owner", "repo", 42)
    )

    assert reviewers == ["alice", "bob"]
    assert len(requests) == 1


def test_github_issue_client_requests_reviewers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repo/pulls/42/requested_reviewers"
        assert json.loads(request.read()) == {"reviewers": ["alice", "bob"]}
        return httpx.Response(201, json={})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.request_review("owner", "repo", 42, ["alice", "bob"]))

    assert len(requests) == 1


def test_github_issue_client_fetch_sub_issues_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/issues/42/sub_issues"
        return httpx.Response(200, json=[])

    reference = parse_issue_shorthand("owner/repo/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(client.fetch_sub_issues(reference)) == []


def test_github_issue_client_fetch_sub_issues_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/issues/42/sub_issues"
        return httpx.Response(403, json={"message": "forbidden"})

    reference = parse_issue_shorthand("owner/repo/42")
    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GitHubError, match="GitHub API error 403: forbidden"):
        asyncio.run(client.fetch_sub_issues(reference))


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


def test_format_run_comment_uses_sanitized_stage_error() -> None:
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
                {
                    "stage_name": "plan",
                    "status": "failed",
                    "error": "Runner exited with code 1",
                },
            ],
        }
    )

    assert "- Error: `Runner exited with code 1`" in body
    assert "SECRET_TOKEN_FROM_STDOUT" not in body


def test_format_run_comment_does_not_treat_repaired_failed_stage_as_terminal() -> None:
    body = format_run_comment(
        {
            "id": "run-123",
            "owner": "owner",
            "repo": "repo",
            "issue_number": 42,
            "status": "completed",
            "current_stage": "pr",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:05:00Z",
            "stages": [
                {"stage_name": "verify", "status": "failed", "error": "test failed"},
                {"stage_name": "verify", "status": "complete"},
                {"stage_name": "pr", "status": "complete"},
            ],
        }
    )

    assert "- Failed stage:" not in body
    assert "- Error:" not in body
    assert "| Verify | `failed` |" in body
    assert "| Verify | `complete` |" in body


def test_format_run_comment_includes_warnings_when_present() -> None:
    body = format_run_comment(
        {
            "id": "run-123",
            "owner": "owner",
            "repo": "repo",
            "issue_number": 42,
            "status": "running",
            "current_stage": "pr",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:10:00Z",
            "stages": [],
        },
        [
            {
                "code": "assignment_lookup_failed",
                "message": "Could not resolve repo admin collaborators - PR created unassigned.",
            }
        ],
    )

    assert "## Warnings" in body
    assert (
        "- assignment_lookup_failed: Could not resolve repo admin collaborators - PR created unassigned."
        in body
    )


def test_format_run_comment_omits_warnings_when_empty() -> None:
    body = format_run_comment(
        {
            "id": "run-123",
            "owner": "owner",
            "repo": "repo",
            "issue_number": 42,
            "status": "running",
            "current_stage": "pr",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:10:00Z",
            "stages": [],
        },
        [],
    )

    assert "## Warnings" not in body
