import asyncio
import json
from pathlib import Path

import pytest

from pawchestrator.config import Settings
from pawchestrator.github import with_generated_attribution
from pawchestrator.review_issues import (
    assemble_issue_body,
    fetch_source_snippet,
    review_issue_format,
)
from pawchestrator.runners import Runner, RunnerResult, RunnerTask


def test_fetch_source_snippet_reads_context_lines(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        "\n".join(f"line {line_number}" for line_number in range(1, 41)),
        encoding="utf-8",
    )

    snippet = fetch_source_snippet(tmp_path, "app.py", 20)

    assert snippet is not None
    assert " 5 | line 5" in snippet
    assert "20 | line 20" in snippet
    assert "35 | line 35" in snippet
    assert " 4 | line 4" not in snippet
    assert "36 | line 36" not in snippet


def test_assemble_issue_body_uses_required_layout() -> None:
    body = assemble_issue_body(
        file="app.py",
        line=12,
        problem="The retry path is untested.",
        acceptance_criteria=["Add a regression test.", "Document the edge case."],
    )

    assert body == with_generated_attribution(
        "**Where:** `app.py:12`\n\n"
        "The retry path is untested.\n\n"
        "## Acceptance Criteria\n\n"
        "- [ ] Add a regression test.\n"
        "- [ ] Document the edge case."
    )


def test_review_issue_format_happy_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "\n".join(f"line {line_number}" for line_number in range(1, 41)),
        encoding="utf-8",
    )
    runner = FakeRunner(
        {
            "title": "T" * 300,
            "problem": "The retry path is untested.",
            "acceptance_criteria": ["Add a regression test."],
        }
    )

    issue = asyncio.run(
        review_issue_format(
            Settings(app_dir=tmp_path),
            run_id="run-123",
            cwd=tmp_path,
            hint="Add retry tests",
            pr_summary="Adds retries.",
            inline_comment={
                "file": "app.py",
                "line": 20,
                "body": "Please add coverage here.",
            },
            repo_path=repo,
            runner=runner,
        )
    )

    assert issue.title == "T" * 256
    assert issue.body == with_generated_attribution(
        "**Where:** `app.py:20`\n\n"
        "The retry path is untested.\n\n"
        "## Acceptance Criteria\n\n"
        "- [ ] Add a regression test."
    )
    assert runner.tasks[0].stage_name == "review_issue_format"
    prompt = json.loads(runner.tasks[0].prompt)
    assert prompt["hint"] == "Add retry tests"
    assert prompt["pr_summary"] == "Adds retries."
    assert prompt["inline_comment_body"] == "Please add coverage here."
    assert " 5 | line 5" in prompt["source_snippet"]
    assert "35 | line 35" in prompt["source_snippet"]


def test_review_issue_format_omits_missing_source_snippet_gracefully(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        {
            "title": "Add tests",
            "problem": "The retry path is untested.",
            "acceptance_criteria": ["Add a regression test."],
        }
    )

    issue = asyncio.run(
        review_issue_format(
            Settings(app_dir=tmp_path),
            run_id="run-123",
            cwd=tmp_path,
            hint="Add retry tests",
            pr_summary="Adds retries.",
            inline_comment={
                "file": "app.py",
                "line": 20,
                "body": "Please add coverage here.",
            },
            repo_path=None,
            runner=runner,
        )
    )

    prompt = json.loads(runner.tasks[0].prompt)
    assert prompt["source_snippet"] is None
    assert issue.title == "Add tests"


@pytest.mark.parametrize(
    "artifact",
    [
        {"problem": "Missing title.", "acceptance_criteria": []},
        {"title": "Bad problem.", "problem": 123, "acceptance_criteria": []},
        {"title": "Bad criteria.", "problem": "Problem.", "acceptance_criteria": [1]},
    ],
)
def test_review_issue_format_invalid_model_response_raises(
    tmp_path: Path,
    artifact: dict[str, object],
) -> None:
    runner = FakeRunner(artifact)

    with pytest.raises(ValueError, match="review issue formatter"):
        asyncio.run(
            review_issue_format(
                Settings(app_dir=tmp_path),
                run_id="run-123",
                cwd=tmp_path,
                hint="Add retry tests",
                pr_summary="Adds retries.",
                inline_comment={
                    "file": "app.py",
                    "line": 20,
                    "body": "Please add coverage here.",
                },
                repo_path=None,
                runner=runner,
            )
        )


class FakeRunner(Runner):
    id = "fake"
    kind = "model"

    def __init__(self, artifact: dict[str, object]) -> None:
        self.artifact = artifact
        self.tasks: list[RunnerTask] = []

    async def check_health(self) -> tuple[bool, str]:
        return True, "ok"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.tasks.append(task)
        return RunnerResult(
            exit_code=0,
            stdout="",
            stderr="",
            artifact=self.artifact,
        )
