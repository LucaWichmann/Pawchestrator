"""GitHub issue snapshot fetching."""

from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from pawchestrator.config import DEFAULT_CHECKBOX_HEADINGS
from pawchestrator.db import utc_now_iso

ISSUE_SNAPSHOT_SCHEMA = "pawchestrator.issue_snapshot.v1"
GITHUB_API_BASE = "https://api.github.com"
PAWCHESTRATOR_LABELS = {
    "running": ("pawchestrator:running", "6f42c1"),
    "scouting": ("pawchestrator:scouting", "0969da"),
    "planning": ("pawchestrator:planning", "1f883d"),
    "implementing": ("pawchestrator:implementing", "bf8700"),
    "verifying": ("pawchestrator:verifying", "8250df"),
    "pr-ready": ("pawchestrator:pr-ready", "0e8a16"),
    "failed": ("pawchestrator:failed", "cf222e"),
    "blocked": ("pawchestrator:blocked", "57606a"),
    "needs-info": ("pawchestrator:needs-info", "d29922"),
}
RUN_STAGE_LABELS = {
    "snapshot": "Snapshot",
    "scout": "Scout",
    "plan": "Plan",
    "implement": "Implement",
    "verify": "Verify",
    "pr": "PR",
    "review": "Review",
    "post": "Post",
    "issues": "Issues",
    "repair": "Repair",
}
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
UNCHECKED_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[\s\]\s+(?P<text>.+?)\s*$")
CHECKED_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s+.+")


class GitHubError(RuntimeError):
    """Raised when GitHub snapshot creation fails."""


@dataclass(frozen=True)
class IssueReference:
    owner: str
    repo: str
    number: int
    source_url: str


def parse_issue_url(issue_url: str) -> IssueReference:
    parsed = urlparse(issue_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise ValueError("expected https://github.com/{owner}/{repo}/issues/{number}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[2] != "issues":
        raise ValueError("expected GitHub issue URL shaped /{owner}/{repo}/issues/{number}")

    try:
        number = int(parts[3])
    except ValueError as error:
        raise ValueError("issue number must be an integer") from error

    if number < 1:
        raise ValueError("issue number must be positive")

    return IssueReference(
        owner=parts[0],
        repo=parts[1],
        number=number,
        source_url=issue_url,
    )


def parse_issue_shorthand(issue_ref: str) -> IssueReference:
    parts = issue_ref.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError("expected issue reference shaped {owner}/{repo}/{number}")

    try:
        number = int(parts[2])
    except ValueError as error:
        raise ValueError("issue number must be an integer") from error

    if number < 1:
        raise ValueError("issue number must be positive")

    owner, repo = parts[0], parts[1]
    return IssueReference(
        owner=owner,
        repo=repo,
        number=number,
        source_url=f"https://github.com/{owner}/{repo}/issues/{number}",
    )


def parse_checkboxes(
    body: str,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
) -> list[dict[str, int | str]]:
    allowed_headings = {heading.casefold() for heading in headings}
    in_scope = False
    checkboxes: list[dict[str, int | str]] = []

    for line in body.splitlines():
        heading_match = HEADING_RE.match(line)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            in_scope = heading_text.casefold() in allowed_headings
            continue

        if not in_scope or CHECKED_CHECKBOX_RE.match(line):
            continue

        checkbox_match = UNCHECKED_CHECKBOX_RE.match(line)
        if checkbox_match:
            checkboxes.append(
                {
                    "index": len(checkboxes),
                    "text": checkbox_match.group("text"),
                }
            )

    return checkboxes


def get_gh_token() -> str:
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as error:
        raise GitHubError("gh binary not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise GitHubError("gh auth token timed out") from error

    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        message = (completed.stderr or completed.stdout).strip()
        raise GitHubError(message or "gh auth token failed")
    return token


class GitHubIssueClient:
    def __init__(
        self,
        token: str,
        *,
        api_base: str = GITHUB_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    async def fetch_snapshot(
        self,
        reference: IssueReference,
        checkbox_headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            issue = await self._get_json(
                client,
                f"/repos/{reference.owner}/{reference.repo}/issues/{reference.number}",
            )
            comments = await self._get_all_pages(
                client,
                f"/repos/{reference.owner}/{reference.repo}/issues/{reference.number}/comments",
            )

        body = issue.get("body") or ""
        return {
            "schema": ISSUE_SNAPSHOT_SCHEMA,
            "owner": reference.owner,
            "repo": reference.repo,
            "number": reference.number,
            "title": issue.get("title") or "",
            "body": body,
            "checkboxes": parse_checkboxes(body, checkbox_headings),
            "labels": [
                label.get("name", "")
                for label in issue.get("labels", [])
                if isinstance(label, dict)
            ],
            "assignees": [
                assignee.get("login", "")
                for assignee in issue.get("assignees", [])
                if isinstance(assignee, dict)
            ],
            "comments": [
                {
                    "author": (comment.get("user") or {}).get("login", ""),
                    "body": comment.get("body") or "",
                    "created_at": comment.get("created_at") or "",
                    "in_reply_to_id": comment.get("in_reply_to_id"),
                }
                for comment in comments
            ],
            "source_url": reference.source_url,
            "fetched_at": utc_now_iso(),
        }

    async def fetch_issue_body(
        self,
        reference: IssueReference,
    ) -> str:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.get(
                f"/repos/{reference.owner}/{reference.repo}/issues/{reference.number}",
            )
            self._raise_for_status(response)
            payload = response.json()

        if not isinstance(payload, dict):
            raise GitHubError("GitHub issue response was not an object")

        return payload.get("body") or ""

    async def fetch_issue_title(self, reference: IssueReference) -> str:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.get(
                f"/repos/{reference.owner}/{reference.repo}/issues/{reference.number}",
            )
            self._raise_for_status(response)
            payload = response.json()

        if not isinstance(payload, dict):
            raise GitHubError("GitHub issue response was not an object")

        return str(payload.get("title") or "")

    async def fetch_sub_issues(self, reference: IssueReference) -> list[dict]:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.get(
                f"/repos/{reference.owner}/{reference.repo}/issues/{reference.number}/sub_issues",
            )
            self._raise_for_status(response)
            payload = response.json()

        if not isinstance(payload, list):
            raise GitHubError("GitHub sub-issues response was not a list")

        return [
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "url": item.get("html_url") or item.get("url") or "",
            }
            for item in payload
            if isinstance(item, dict)
        ]

    async def fetch_pr_review_state(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> str:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            reviews = await self._get_all_pages(
                client,
                f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            )

        states = [str(review.get("state") or "") for review in reviews]
        if "CHANGES_REQUESTED" in states:
            return "changes_requested"
        if states and states[-1] == "APPROVED":
            return "approved"
        return "open"

    async def fetch_pr_head_branch(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> str:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            payload = await self._get_json(
                client,
                f"/repos/{owner}/{repo}/pulls/{number}",
            )

        head = payload.get("head")
        if not isinstance(head, dict):
            raise GitHubError("GitHub pull request response did not include head")
        label = head.get("label")
        ref = head.get("ref")
        branch = label or ref
        if not isinstance(branch, str) or not branch:
            raise GitHubError("GitHub pull request head did not include a branch")
        return branch

    async def fetch_review_comments(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> dict[str, list[dict[str, Any]]]:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            inline_comments, top_level_comments = await asyncio.gather(
                self._get_all_pages(
                    client,
                    f"/repos/{owner}/{repo}/pulls/{number}/comments",
                ),
                self._get_all_pages(
                    client,
                    f"/repos/{owner}/{repo}/issues/{number}/comments",
                ),
            )
        return {
            "inline_comments": [
                {
                    "author": (comment.get("user") or {}).get("login", ""),
                    "body": comment.get("body") or "",
                    "path": comment.get("path") or "",
                    "line": comment.get("line") or comment.get("original_line"),
                    "created_at": comment.get("created_at") or "",
                }
                for comment in inline_comments
            ],
            "top_level_comments": [
                {
                    "author": (comment.get("user") or {}).get("login", ""),
                    "body": comment.get("body") or "",
                    "created_at": comment.get("created_at") or "",
                }
                for comment in top_level_comments
            ],
        }

    async def fetch_pr_diff(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> str:
        headers = {**self._headers(), "Accept": "application/vnd.github.v3.diff"}
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=headers,
            transport=self._transport,
        ) as client:
            response = await client.get(f"/repos/{owner}/{repo}/pulls/{number}")
            self._raise_for_status(response)
            return response.text

    async def post_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> int:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                json={"body": body},
            )
            self._raise_for_status(response)
            payload = response.json()
        if not isinstance(payload, dict) or "id" not in payload:
            raise GitHubError("GitHub comment response did not include an id")
        return int(payload["id"])

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
        event: str,
        comments: Sequence[dict[str, Any]],
    ) -> int | None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"/repos/{owner}/{repo}/pulls/{number}/reviews",
                json={
                    "body": body,
                    "event": event,
                    "comments": list(comments),
                },
            )
            self._raise_for_status(response)
            payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubError("GitHub review response was not an object")
        review_id = payload.get("id")
        return int(review_id) if review_id is not None else None

    async def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str | None = None,
    ) -> str:
        request_body: dict[str, str] = {"title": title}
        if body is not None:
            request_body["body"] = body
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"/repos/{owner}/{repo}/issues",
                json=request_body,
            )
            self._raise_for_status(response)
            payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubError("GitHub issue creation response was not an object")
        issue_url = payload.get("html_url") or payload.get("url")
        if not isinstance(issue_url, str) or not issue_url:
            raise GitHubError("GitHub issue creation response did not include a URL")
        return issue_url

    async def fetch_admin_collaborators(self, owner: str, repo: str) -> list[str]:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            collaborators = await self._get_all_pages(
                client,
                f"/repos/{owner}/{repo}/collaborators?permission=admin",
            )

        return [
            collaborator.get("login", "")
            for collaborator in collaborators
            if isinstance(collaborator.get("login"), str)
        ]

    async def patch_issue_body(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
    ) -> None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.patch(
                f"/repos/{owner}/{repo}/issues/{number}",
                json={"body": body},
            )
            self._raise_for_status(response)

    async def edit_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        body: str,
    ) -> None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.patch(
                f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
                json={"body": body},
            )
            self._raise_for_status(response)

    async def ensure_label(self, owner: str, repo: str, name: str, color: str) -> None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.get(f"/repos/{owner}/{repo}/labels/{name}")
            if response.status_code == 200:
                return
            if response.status_code != 404:
                self._raise_for_status(response)

            response = await client.post(
                f"/repos/{owner}/{repo}/labels",
                json={"name": name, "color": color},
            )
            if response.status_code == 422:
                return
            self._raise_for_status(response)

    async def add_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
                json={"labels": [name]},
            )
            self._raise_for_status(response)

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        name: str,
    ) -> None:
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=self._headers(),
            transport=self._transport,
        ) as client:
            response = await client.delete(
                f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
            )
            if response.status_code == 404:
                return
            self._raise_for_status(response)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        response = await client.get(url)
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubError("GitHub issue response was not an object")
        return payload

    async def _get_all_pages(
        self, client: httpx.AsyncClient, url: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            response = await client.get(next_url)
            self._raise_for_status(response)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError("GitHub comments response was not a list")
            items.extend(item for item in payload if isinstance(item, dict))
            next_url = response.links.get("next", {}).get("url")
        return items

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            message = response.json().get("message", response.text)
        except ValueError:
            message = response.text
        raise GitHubError(f"GitHub API error {response.status_code}: {message}")


async def ensure_pawchestrator_labels(
    client: GitHubIssueClient,
    owner: str,
    repo: str,
) -> None:
    for label_name, color in PAWCHESTRATOR_LABELS.values():
        await client.ensure_label(owner, repo, label_name, color)


def format_run_comment(
    run_state: dict[str, Any],
    warnings: list[dict[str, str]] | None = None,
) -> str:
    current_stage = str(run_state.get("current_stage") or "pending")
    status = str(run_state.get("status") or "pending")
    started_at = str(run_state.get("created_at") or run_state.get("started_at") or "")
    updated_at = str(run_state.get("updated_at") or "")
    pr_url = run_state.get("pr_url")
    failure_status = status == "failed" or status.endswith("_failed")
    failed_stage = (
        run_state.get("failed_stage") or _failed_stage_from_state(run_state)
        if failure_status
        else None
    )
    error = run_state.get("error") or _error_from_state(run_state) if failure_status else None

    lines = [
        "## Pawchestrator run",
        "",
        f"- Run ID: `{run_state.get('id') or run_state.get('run_id') or ''}`",
        f"- Repository: `{run_state.get('owner') or ''}/{run_state.get('repo') or ''}`",
        f"- Issue: `#{run_state.get('issue_number') or ''}`",
        f"- Branch: `{run_state.get('branch') or ''}`",
        f"- Status: `{status}`",
        f"- Current stage: `{current_stage}`",
        f"- Started at: `{started_at}`",
        f"- Updated at: `{updated_at}`",
    ]
    if pr_url:
        lines.append(f"- PR: {pr_url}")
    if failure_status or failed_stage or error:
        lines.append(f"- Failed stage: `{failed_stage or current_stage}`")
        if error:
            lines.append(f"- Error: `{error}`")
    lines.extend(["", _format_stage_table(run_state)])
    if warnings:
        lines.extend(["", "## Warnings"])
        for warning in warnings:
            lines.append(f"- {warning.get('code', '')}: {warning.get('message', '')}")
    return "\n".join(lines)


def _format_stage_table(run_state: dict[str, Any]) -> str:
    stages = run_state.get("stages")
    if not isinstance(stages, list):
        return ""

    lines = ["| Stage | Status |", "| --- | --- |"]
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = str(stage.get("stage_name") or "")
        status = str(stage.get("status") or "pending")
        label = RUN_STAGE_LABELS.get(name, name)
        lines.append(f"| {label} | `{status}` |")
    return "\n".join(lines)


def _failed_stage_from_state(run_state: dict[str, Any]) -> str | None:
    stages = run_state.get("stages")
    if not isinstance(stages, list):
        return None
    for stage in stages:
        if isinstance(stage, dict) and stage.get("status") == "failed":
            return str(stage.get("stage_name") or "")
    return None


def _error_from_state(run_state: dict[str, Any]) -> str | None:
    stages = run_state.get("stages")
    if not isinstance(stages, list):
        return None
    for stage in stages:
        if isinstance(stage, dict) and stage.get("status") == "failed" and stage.get("error"):
            return str(stage["error"])
    return None


def parse_diff_positions(diff_text: str) -> dict[tuple[str, int], int]:
    positions: dict[tuple[str, int], int] = {}
    current_file: str | None = None
    new_line: int | None = None
    position = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            new_line = None
            position = 0
            continue

        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current_file = None
            elif path.startswith("b/"):
                current_file = path[2:]
            else:
                current_file = path
            continue

        if current_file is None:
            continue

        if line.startswith("@@ "):
            hunk_match = re.search(r"\+(\d+)(?:,\d+)?", line)
            if hunk_match is None:
                new_line = None
                continue
            new_line = int(hunk_match.group(1))
            continue

        if new_line is None:
            continue

        if line.startswith("\\"):
            continue

        marker = line[:1]
        if marker not in {" ", "+", "-"}:
            continue

        position += 1
        if marker == "+":
            positions[(current_file, new_line)] = position
            new_line += 1
        elif marker == " ":
            new_line += 1

    return positions
