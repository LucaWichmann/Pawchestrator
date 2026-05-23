"""GitHub issue snapshot fetching."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from pawchestrator.db import utc_now_iso

ISSUE_SNAPSHOT_SCHEMA = "pawchestrator.issue_snapshot.v1"
GITHUB_API_BASE = "https://api.github.com"


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

    async def fetch_snapshot(self, reference: IssueReference) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(
            base_url=self._api_base,
            headers=headers,
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

        return {
            "schema": ISSUE_SNAPSHOT_SCHEMA,
            "owner": reference.owner,
            "repo": reference.repo,
            "number": reference.number,
            "title": issue.get("title") or "",
            "body": issue.get("body") or "",
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
                }
                for comment in comments
            ],
            "source_url": reference.source_url,
            "fetched_at": utc_now_iso(),
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
