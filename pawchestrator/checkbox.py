"""Issue checkbox update helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pawchestrator.config import DEFAULT_CHECKBOX_HEADINGS
from pawchestrator.github import (
    CHECKED_CHECKBOX_RE,
    HEADING_RE,
    GitHubError,
    GitHubIssueClient,
    IssueReference,
)

CHECKBOX_RE = re.compile(
    r"^(?P<prefix>\s*[-*+]\s+)\[(?P<mark>[ xX])\](?P<suffix>\s+.+?)\s*$"
)


class CheckboxError(RuntimeError):
    """Raised when a checkbox cannot be checked."""


@dataclass(frozen=True)
class ScopedCheckbox:
    index: int
    line_number: int
    line: str
    checked: bool


async def check_checkbox(
    client: GitHubIssueClient,
    reference: IssueReference,
    index: int,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
) -> bool:
    if index < 0:
        raise CheckboxError("checkbox index must be non-negative")

    last_error: GitHubError | None = None
    for attempt in range(2):
        body, etag = await client.fetch_issue_body_with_etag(reference)
        updated_body = check_checkbox_in_body(body, index, headings)
        if updated_body == body:
            return False

        try:
            await client.patch_issue_body_with_etag(reference, updated_body, etag)
            return True
        except GitHubError as error:
            last_error = error
            if "GitHub API error 412:" not in str(error) or attempt == 1:
                raise

    raise CheckboxError(f"checkbox update failed after retry: {last_error}")


def check_checkbox_in_body(
    body: str,
    index: int,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
) -> str:
    if not has_in_scope_heading(body, headings):
        raise CheckboxError("no in-scope headings found in issue body")

    checkboxes = find_scoped_checkboxes(body, headings)
    if not checkboxes:
        raise CheckboxError("no in-scope checkboxes found in issue body")
    if index >= len(checkboxes):
        raise CheckboxError(
            f"checkbox index {index} out of range; "
            f"found {len(checkboxes)} in-scope checkboxes"
        )

    checkbox = checkboxes[index]
    if checkbox.checked:
        return body

    lines = body.splitlines(keepends=True)
    lines[checkbox.line_number] = _check_line(lines[checkbox.line_number])
    return "".join(lines)


def find_scoped_checkboxes(
    body: str,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
) -> list[ScopedCheckbox]:
    allowed_headings = {heading.casefold() for heading in headings}
    in_scope = False
    checkboxes: list[ScopedCheckbox] = []

    for line_number, raw_line in enumerate(body.splitlines()):
        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            in_scope = heading_text.casefold() in allowed_headings
            continue

        if not in_scope:
            continue

        checkbox_match = CHECKBOX_RE.match(raw_line)
        if checkbox_match:
            checkboxes.append(
                ScopedCheckbox(
                    index=len(checkboxes),
                    line_number=line_number,
                    line=raw_line,
                    checked=CHECKED_CHECKBOX_RE.match(raw_line) is not None,
                )
            )

    return checkboxes


def has_in_scope_heading(
    body: str,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
) -> bool:
    allowed_headings = {heading.casefold() for heading in headings}
    for line in body.splitlines():
        heading_match = HEADING_RE.match(line)
        heading_text = (
            heading_match.group(2).strip().casefold() if heading_match else ""
        )
        if heading_text in allowed_headings:
            return True
    return False


def _check_line(line: str) -> str:
    return re.sub(r"(\s*[-*+]\s+)\[\s\]", r"\1[x]", line, count=1)
