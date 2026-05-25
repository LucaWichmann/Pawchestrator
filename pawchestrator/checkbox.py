"""Issue checkbox update helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from pawchestrator.config import DEFAULT_CHECKBOX_HEADINGS, Settings
from pawchestrator.db import SCHEMA_SQL, utc_now_iso
from pawchestrator.github import (
    CHECKED_CHECKBOX_RE,
    HEADING_RE,
    GitHubIssueClient,
    IssueReference,
)

CHECKBOX_RE = re.compile(
    r"^(?P<prefix>\s*[-*+]\s+)\[(?P<mark>[ xX])\](?P<suffix>\s+.+?)\s*$"
)
CHECKBOX_APPLY_ATTEMPTS = 3


class CheckboxError(RuntimeError):
    """Raised when a checkbox cannot be checked."""


@dataclass(frozen=True)
class ScopedCheckbox:
    index: int
    line_number: int
    line: str
    text: str
    checked: bool


async def check_checkbox(
    client: GitHubIssueClient,
    reference: IssueReference,
    index: int,
    headings: Sequence[str] = DEFAULT_CHECKBOX_HEADINGS,
    *,
    run_id: str | None = None,
    db_path: Path | None = None,
) -> bool:
    if index < 0:
        raise CheckboxError("checkbox index must be non-negative")
    if run_id is not None and db_path is None:
        raise CheckboxError("db_path is required when run_id is provided")

    if run_id is not None:
        assert db_path is not None
        return await _check_run_scoped_checkbox(
            client,
            reference,
            index,
            headings,
            run_id=run_id,
            db_path=db_path,
        )

    body = await client.fetch_issue_body(reference)
    updated_body = check_checkbox_in_body(body, index, headings)

    if updated_body == body:
        return False

    await client.patch_issue_body(
        reference.owner,
        reference.repo,
        reference.number,
        updated_body,
    )
    return True


async def reconcile_checkbox_marks(
    settings: Settings,
    run_id: str,
    client: GitHubIssueClient,
) -> tuple[bool, list[dict[str, object]]]:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA_SQL)
        marks = await _get_checkbox_marks_for_run(db, run_id=run_id)

    if not marks:
        return False, []

    changed = False
    warnings: list[dict[str, object]] = []
    for issue_marks in _group_marks_by_issue(marks):
        first_mark = issue_marks[0]
        reference = IssueReference(
            owner=str(first_mark["owner"]),
            repo=str(first_mark["repo"]),
            number=int(first_mark["issue_number"]),
            source_url=(
                "https://github.com/"
                f"{first_mark['owner']}/{first_mark['repo']}/issues/"
                f"{first_mark['issue_number']}"
            ),
        )
        body = await client.fetch_issue_body(reference)
        issue_changed, issue_warnings = await _patch_until_stored_marks_checked(
            client,
            reference,
            body,
            issue_marks,
            settings.checkboxes.headings,
        )
        changed = issue_changed or changed
        warnings.extend(issue_warnings)
    return changed, warnings


async def _check_run_scoped_checkbox(
    client: GitHubIssueClient,
    reference: IssueReference,
    index: int,
    headings: Sequence[str],
    *,
    run_id: str,
    db_path: Path,
) -> bool:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.execute("BEGIN IMMEDIATE")
        try:
            body = await client.fetch_issue_body(reference)
            checkboxes = find_scoped_checkboxes(body, headings)
            if not checkboxes:
                raise CheckboxError("no in-scope checkboxes found in issue body")
            if index >= len(checkboxes):
                raise CheckboxError(
                    f"checkbox index {index} out of range; "
                    f"found {len(checkboxes)} in-scope checkboxes"
                )

            await _record_checkbox_mark(
                db,
                run_id=run_id,
                owner=reference.owner,
                repo=reference.repo,
                issue_number=reference.number,
                checkbox_index=index,
                checkbox_text=checkboxes[index].text,
            )
            marks = await _get_checkbox_marks_for_run_issue(
                db,
                run_id=run_id,
                owner=reference.owner,
                repo=reference.repo,
                issue_number=reference.number,
            )

            changed, _warnings = await _patch_until_stored_marks_checked(
                client,
                reference,
                body,
                marks,
                headings,
            )
            await db.commit()
            return changed
        except Exception:
            await db.rollback()
            raise


async def _patch_until_stored_marks_checked(
    client: GitHubIssueClient,
    reference: IssueReference,
    body: str,
    marks: Sequence[dict[str, object]],
    headings: Sequence[str],
) -> tuple[bool, list[dict[str, object]]]:
    changed = False
    current_body = body
    warnings: list[dict[str, object]] = []

    for _ in range(CHECKBOX_APPLY_ATTEMPTS):
        active_marks, stale_warnings = _matching_checkbox_marks(
            reference,
            current_body,
            marks,
            headings,
        )
        warnings.extend(
            warning for warning in stale_warnings if warning not in warnings
        )
        if not active_marks:
            return changed, warnings

        updated_body = _apply_checkbox_marks(current_body, active_marks, headings)
        if updated_body != current_body:
            await client.patch_issue_body(
                reference.owner,
                reference.repo,
                reference.number,
                updated_body,
            )
            changed = True

        verified_body = await client.fetch_issue_body(reference)
        active_marks, stale_warnings = _matching_checkbox_marks(
            reference,
            verified_body,
            active_marks,
            headings,
        )
        warnings.extend(
            warning for warning in stale_warnings if warning not in warnings
        )
        if not active_marks or _stored_marks_checked(
            verified_body,
            active_marks,
            headings,
        ):
            return changed, warnings

        current_body = verified_body

    raise CheckboxError(
        "GitHub issue body is missing stored checkbox marks after "
        f"{CHECKBOX_APPLY_ATTEMPTS} patch attempts"
    )


def _group_marks_by_issue(
    marks: Sequence[dict[str, object]],
) -> list[list[dict[str, object]]]:
    grouped: dict[tuple[str, str, int], list[dict[str, object]]] = {}
    for mark in marks:
        key = (
            str(mark["owner"]),
            str(mark["repo"]),
            int(mark["issue_number"]),
        )
        grouped.setdefault(key, []).append(mark)
    return list(grouped.values())


def _apply_checkbox_marks(
    body: str,
    marks: Sequence[dict[str, object]],
    headings: Sequence[str],
) -> str:
    updated_body = body
    for mark in marks:
        updated_body = check_checkbox_in_body(
            updated_body,
            int(mark["checkbox_index"]),
            headings,
        )
    return updated_body


def _matching_checkbox_marks(
    reference: IssueReference,
    body: str,
    marks: Sequence[dict[str, object]],
    headings: Sequence[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    checkboxes = find_scoped_checkboxes(body, headings)
    active_marks: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    for mark in marks:
        checkbox_index = int(mark["checkbox_index"])
        stored_text = str(mark["checkbox_text"])
        current_text = (
            checkboxes[checkbox_index].text
            if checkbox_index < len(checkboxes)
            else None
        )
        if current_text != stored_text:
            warnings.append(
                {
                    "owner": reference.owner,
                    "repo": reference.repo,
                    "issue_number": reference.number,
                    "checkbox_index": checkbox_index,
                    "stored_text": stored_text,
                    "current_text": current_text,
                }
            )
            continue
        active_marks.append(mark)

    return active_marks, warnings


def _stored_marks_checked(
    body: str,
    marks: Sequence[dict[str, object]],
    headings: Sequence[str],
) -> bool:
    checkboxes = find_scoped_checkboxes(body, headings)
    for mark in marks:
        checkbox_index = int(mark["checkbox_index"])
        if checkbox_index >= len(checkboxes) or not checkboxes[checkbox_index].checked:
            return False
    return True


async def _record_checkbox_mark(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
    checkbox_index: int,
    checkbox_text: str,
) -> None:
    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO checkbox_marks (
          run_id, owner, repo, issue_number, checkbox_index, checkbox_text,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, owner, repo, issue_number, checkbox_index)
        DO UPDATE SET
          checkbox_text = excluded.checkbox_text,
          updated_at = excluded.updated_at
        """,
        (
            run_id,
            owner,
            repo,
            issue_number,
            checkbox_index,
            checkbox_text,
            now,
            now,
        ),
    )


async def _get_checkbox_marks_for_run_issue(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> list[dict[str, object]]:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT run_id, owner, repo, issue_number, checkbox_index, checkbox_text,
               created_at, updated_at
        FROM checkbox_marks
        WHERE run_id = ?
          AND owner = ?
          AND repo = ?
          AND issue_number = ?
        ORDER BY checkbox_index
        """,
        (run_id, owner, repo, issue_number),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _get_checkbox_marks_for_run(
    db: aiosqlite.Connection,
    *,
    run_id: str,
) -> list[dict[str, object]]:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT run_id, owner, repo, issue_number, checkbox_index, checkbox_text,
               created_at, updated_at
        FROM checkbox_marks
        WHERE run_id = ?
        ORDER BY owner, repo, issue_number, checkbox_index
        """,
        (run_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


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
                    text=checkbox_match.group("suffix").strip(),
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
