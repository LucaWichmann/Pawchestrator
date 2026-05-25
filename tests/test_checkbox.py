import asyncio
import json
from pathlib import Path

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


def test_check_checkbox_patches_issue_body_without_if_match() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"body": "## Acceptance Criteria\n\n- [ ] First\n"},
            )

        assert request.method == "PATCH"
        assert request.url.path == "/repos/owner/repo/issues/42"
        assert json.loads(request.read()) == {
            "body": "## Acceptance Criteria\n\n- [x] First\n"
        }
        assert "If-Match" not in request.headers
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
    assert [request.method for request in requests] == ["GET", "PATCH"]


def test_check_checkbox_noops_without_patch_when_already_checked() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(
            200,
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


def test_check_checkbox_propagates_patch_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"body": "## Acceptance Criteria\n\n- [ ] First\n"},
            )
        return httpx.Response(403, json={"message": "forbidden"})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="GitHub API error 403: forbidden"):
        asyncio.run(check_checkbox(client, parse_issue_shorthand("owner/repo/42"), 0))


def test_run_scoped_checkbox_retries_when_verification_get_is_stale(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    body = "## Acceptance Criteria\n\n- [ ] First\n- [ ] Second\n"
    patched_body = body
    stale_verification_sent = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patched_body, stale_verification_sent
        requests.append(request)
        if request.method == "GET":
            if patched_body != body and not stale_verification_sent:
                stale_verification_sent = True
                return httpx.Response(200, json={"body": body})
            return httpx.Response(200, json={"body": patched_body})

        assert request.method == "PATCH"
        patched_body = json.loads(request.read())["body"]
        return httpx.Response(200, json={"number": 42})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )

    changed = asyncio.run(
        check_checkbox(
            client,
            parse_issue_shorthand("owner/repo/42"),
            0,
            run_id="run-1",
            db_path=tmp_path / "database.sqlite",
        )
    )

    assert changed is True
    assert patched_body == "## Acceptance Criteria\n\n- [x] First\n- [ ] Second\n"
    assert [request.method for request in requests] == [
        "GET",
        "PATCH",
        "GET",
        "PATCH",
        "GET",
    ]


def test_run_scoped_overlapping_checkbox_checks_converge(
    tmp_path: Path,
) -> None:
    lock = asyncio.Lock()
    body = "## Acceptance Criteria\n\n- [ ] First\n- [ ] Second\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal body
        async with lock:
            if request.method == "GET":
                return httpx.Response(200, json={"body": body})

            assert request.method == "PATCH"
            body = json.loads(request.read())["body"]
            return httpx.Response(200, json={"number": 42})

    client = GitHubIssueClient(
        "token",
        api_base="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    reference = parse_issue_shorthand("owner/repo/42")
    db_path = tmp_path / "database.sqlite"

    async def run_checks() -> None:
        await asyncio.gather(
            check_checkbox(
                client,
                reference,
                0,
                run_id="run-1",
                db_path=db_path,
            ),
            check_checkbox(
                client,
                reference,
                1,
                run_id="run-1",
                db_path=db_path,
            ),
        )

    asyncio.run(run_checks())

    assert body == "## Acceptance Criteria\n\n- [x] First\n- [x] Second\n"
