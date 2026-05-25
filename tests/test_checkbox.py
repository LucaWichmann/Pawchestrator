import asyncio
import json

import httpx
import pytest

from pawchestrator.checkbox import CheckboxError, check_checkbox, check_checkbox_in_body
from pawchestrator.github import GitHubIssueClient, parse_issue_shorthand


def test_check_checkbox_in_body_checks_valid_index() -> None:
    body = "## Acceptance Criteria\n\n- [ ] First\n- [ ] Second\n"

    updated = check_checkbox_in_body(body, 0)

    assert updated == "## Acceptance Criteria\n\n- [x] First\n- [ ] Second\n"


def test_check_checkbox_in_body_noops_when_already_checked() -> None:
    body = "## Acceptance Criteria\n\n- [x] First\n- [ ] Second\n"

    updated = check_checkbox_in_body(body, 0)

    assert updated == body


def test_check_checkbox_in_body_rejects_invalid_index() -> None:
    body = "## Acceptance Criteria\n\n- [ ] First\n"

    with pytest.raises(CheckboxError, match="checkbox index 1 out of range"):
        check_checkbox_in_body(body, 1)


def test_check_checkbox_in_body_rejects_body_without_in_scope_headings() -> None:
    body = "## Notes\n\n- [ ] First\n"

    with pytest.raises(CheckboxError, match="no in-scope headings"):
        check_checkbox_in_body(body, 0)


def test_check_checkbox_retries_once_on_etag_conflict() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"ETag": f'"v{len(requests)}"'},
                json={"body": "## Acceptance Criteria\n\n- [ ] First\n"},
            )

        assert request.method == "PATCH"
        assert request.url.path == "/repos/owner/repo/issues/42"
        assert json.loads(request.read()) == {
            "body": "## Acceptance Criteria\n\n- [x] First\n"
        }
        if len([item for item in requests if item.method == "PATCH"]) == 1:
            assert request.headers["If-Match"] == '"v1"'
            return httpx.Response(412, json={"message": "precondition failed"})

        assert request.headers["If-Match"] == '"v3"'
        return httpx.Response(200, json={"number": 42})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    changed = asyncio.run(
        check_checkbox(client, parse_issue_shorthand("owner/repo/42"), 0)
    )

    assert changed is True
    assert [request.method for request in requests] == ["GET", "PATCH", "GET", "PATCH"]


def test_check_checkbox_noops_without_patch_when_already_checked() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(
            200,
            headers={"ETag": '"v1"'},
            json={"body": "## Acceptance Criteria\n\n- [x] First\n"},
        )

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    changed = asyncio.run(
        check_checkbox(client, parse_issue_shorthand("owner/repo/42"), 0)
    )

    assert changed is False
    assert [request.method for request in requests] == ["GET"]


def test_check_checkbox_fails_when_second_etag_patch_conflicts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"ETag": '"v1"'},
                json={"body": "## Acceptance Criteria\n\n- [ ] First\n"},
            )
        return httpx.Response(412, json={"message": "precondition failed"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="GitHub API error 412"):
        asyncio.run(check_checkbox(client, parse_issue_shorthand("owner/repo/42"), 0))
