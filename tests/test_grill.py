import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator import grill as grill_module
from pawchestrator.config import ClaudeRunnerSettings, Settings, StageSettings
from pawchestrator.db import get_run_warnings, init_db
from pawchestrator.grill import (
    GrillReport,
    append_suggested_criteria,
    build_dedupe_prompt,
    build_grill_prompt,
    dedupe_criteria,
    run_grill,
)
from pawchestrator.github import GENERATED_BY_FOOTER
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
    RunnerFailedError,
    RunnerResult,
    RunnerTask,
    _effective_claude_config,
    resolve_runner,
)


class FakeRunner(Runner):
    id = "fake"
    kind = "agent"

    def __init__(self, artifact: dict[str, Any] | None = None) -> None:
        self.artifact = artifact or {
            "schema": "pawchestrator.grill_report.v1",
            "status": "success",
            "suggested_criteria": ["Adds POST /issue/grill."],
            "unanswerable_questions": [],
        }
        self.task: RunnerTask | None = None

    async def check_health(self) -> tuple[bool, str]:
        return True, "ok"

    async def run_task(self, task: RunnerTask) -> RunnerResult:
        self.task = task
        return RunnerResult(exit_code=0, stdout="{}", stderr="", artifact=self.artifact)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.patched_body: str | None = None
        self.comments: list[str] = []
        self.added_labels: list[str] = []
        self.removed_labels: list[str] = []

    async def patch_issue_body(self, owner: str, repo: str, number: int, body: str) -> None:
        self.patched_body = body

    async def post_comment(self, owner: str, repo: str, issue_number: int, body: str) -> int:
        self.comments.append(body)
        return 123

    async def add_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.added_labels.append(name)

    async def remove_label(self, owner: str, repo: str, issue_number: int, name: str) -> None:
        self.removed_labels.append(name)


def test_build_grill_prompt_includes_read_only_instructions() -> None:
    prompt = build_grill_prompt(
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "title": "Add grill",
            "body": "Body text",
        }
    )

    assert "Issue: #42 - Add grill" in prompt
    assert "Repository: owner/repo" in prompt
    assert "Body text" in prompt
    assert "Use your Read, Glob, Grep tools" in prompt
    assert "unanswerable_questions" in prompt


def test_build_dedupe_prompt_is_compact_json() -> None:
    prompt = build_dedupe_prompt(
        ["Expose a grill endpoint."],
        ["Add POST /issue/grill."],
    )

    payload = json.loads(prompt)

    assert "\n" not in prompt
    assert payload["existing_criteria"] == ["Expose a grill endpoint."]
    assert payload["proposed_criteria"] == ["Add POST /issue/grill."]
    assert "unique_suggested_criteria" in payload["output_schema"]


def test_build_grill_prompt_includes_issue_comments() -> None:
    prompt = build_grill_prompt(
        {
            "owner": "owner",
            "repo": "repo",
            "number": 42,
            "title": "Add grill",
            "body": "Body text",
            "comments": [
                {
                    "author": "octo",
                    "body": "The answer is pytest.",
                    "in_reply_to_id": 123,
                }
            ],
        }
    )

    assert "Issue comments:" in prompt
    assert "- octo: The answer is pytest." in prompt


def test_append_suggested_criteria_appends_new_round_criteria() -> None:
    body, updated = append_suggested_criteria("Original", ["First", "Second"])

    assert updated is True
    assert "## Pawchestrator Suggested Criteria" in body
    assert "- [ ] First" in body

    second_body, second_updated = append_suggested_criteria(body, ["Third"])

    assert second_updated is True
    assert "- [ ] First" in second_body
    assert "- [ ] Second" in second_body
    assert "- [ ] Third" in second_body
    assert second_body.index("- [ ] Third") < second_body.index(GENERATED_BY_FOOTER)


def test_append_suggested_criteria_adds_attribution_for_exact_duplicates() -> None:
    body = "Original\n\n## Pawchestrator Suggested Criteria\n\n- [ ] First\n- [x] Second\n"

    updated_body, updated = append_suggested_criteria(body, ["First", "Second"])

    assert updated is True
    assert updated_body.endswith(f"{GENERATED_BY_FOOTER}\n")


def test_dedupe_criteria_filters_normalized_duplicates_before_runner(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        {
            "schema": "pawchestrator.criteria_dedupe.v1",
            "unique_suggested_criteria": ["New criterion"],
        }
    )

    result = asyncio.run(
        dedupe_criteria(
            Settings(app_dir=tmp_path),
            run_id="run-123",
            cwd=tmp_path,
            existing_criteria=["Existing criterion"],
            proposed_criteria=["  existing   criterion  ", "New criterion"],
            runner=runner,
        )
    )

    assert result == ["New criterion"]
    assert runner.task is not None
    prompt_payload = json.loads(runner.task.prompt)
    assert prompt_payload["proposed_criteria"] == ["New criterion"]
    assert runner.task.stage_name == "criteria_dedupe"


def test_dedupe_criteria_falls_back_on_invalid_json(tmp_path: Path, caplog) -> None:
    runner = FakeRunner({"schema": "pawchestrator.criteria_dedupe.v1"})

    result = asyncio.run(
        dedupe_criteria(
            Settings(app_dir=tmp_path),
            run_id="run-123",
            cwd=tmp_path,
            existing_criteria=["Existing criterion"],
            proposed_criteria=["New criterion"],
            runner=runner,
        )
    )

    assert result == ["New criterion"]
    assert "criteria dedupe runner failed; using normalized dedupe" in caplog.text


def test_dedupe_criteria_falls_back_to_codex_for_claude_usage_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={
            "criteria_dedupe": StageSettings(
                codex={"sandbox": "danger-full-access", "bypass_sandbox": True}
            )
        },
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    seen: dict[str, object] = {}

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        seen["task"] = task
        seen["sandbox"] = self.stage_overrides["criteria_dedupe"].codex.sandbox
        seen["bypass_sandbox"] = self.stage_overrides[
            "criteria_dedupe"
        ].codex.bypass_sandbox
        return RunnerResult(
            exit_code=0,
            stdout="{}",
            stderr="",
            artifact={
                "schema": "pawchestrator.criteria_dedupe.v1",
                "unique_suggested_criteria": ["Keep this"],
            },
        )

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(
        dedupe_criteria(
            settings,
            run_id=run_id,
            cwd=tmp_path,
            existing_criteria=["Existing"],
            proposed_criteria=["Keep this", "Drop this"],
        )
    )

    assert result == ["Keep this"]
    assert isinstance(seen["task"], RunnerTask)
    assert seen["sandbox"] == "read-only"
    assert seen["bypass_sandbox"] is False
    log = (
        tmp_path / "runs" / run_id / "stdout" / "criteria_dedupe.log"
    ).read_text(encoding="utf-8")
    assert "[claude stdout]" in log
    assert "[codex stdout]" in log
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == [
        "criteria_dedupe_usage_limit_fallback"
    ]
    assert warnings[0]["message"] == (
        "Claude usage limit exhausted; using Codex for criteria_dedupe."
    )


def test_dedupe_criteria_respects_disabled_usage_limit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={
            "criteria_dedupe": StageSettings(usage_limit_fallback_runner="none")
        },
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        raise AssertionError("codex should not run")

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(
        dedupe_criteria(
            settings,
            run_id=run_id,
            cwd=tmp_path,
            existing_criteria=[],
            proposed_criteria=["Keep this"],
        )
    )

    assert result == ["Keep this"]
    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_dedupe_criteria_codex_primary_does_not_self_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"criteria_dedupe": StageSettings(runner="codex")},
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))

    async def fake_check_health(self: CodexRunner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(exit_code=1, stdout="", stderr="codex failed", artifact=None)

    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(
        dedupe_criteria(
            settings,
            run_id=run_id,
            cwd=tmp_path,
            existing_criteria=[],
            proposed_criteria=["Keep this"],
        )
    )

    assert result == ["Keep this"]
    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_dedupe_criteria_uses_normalized_fallback_after_codex_fallback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(exit_code=1, stdout="", stderr="codex failed", artifact=None)

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(
        dedupe_criteria(
            settings,
            run_id=run_id,
            cwd=tmp_path,
            existing_criteria=["Existing"],
            proposed_criteria=[" existing ", "Keep this"],
        )
    )

    assert result == ["Keep this"]
    log = (
        tmp_path / "runs" / run_id / "stdout" / "criteria_dedupe.log"
    ).read_text(encoding="utf-8")
    assert "[claude stdout]" in log
    assert "[codex stderr]" in log
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == [
        "criteria_dedupe_usage_limit_fallback"
    ]


def test_append_suggested_criteria_inserts_before_following_section() -> None:
    body = (
        "Original\n\n"
        "## Pawchestrator Suggested Criteria\n\n"
        "- [ ] Existing\n\n"
        "## Notes\n\n"
        "Keep this section after criteria.\n"
    )

    updated_body, updated = append_suggested_criteria(body, ["New"])

    assert updated is True
    assert updated_body.index("- [ ] New") < updated_body.index("## Notes")
    assert updated_body.index(GENERATED_BY_FOOTER) < updated_body.index("## Notes")
    assert "Keep this section after criteria." in updated_body


def test_run_grill_updates_body_without_comment_when_questions_empty(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    fake_runner = FakeRunner()
    fake_client = FakeGitHubClient()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            repo_path=repo_path,
            runner=fake_runner,
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert fake_runner.task is not None
    assert fake_runner.task.cwd == repo_path.resolve()
    assert fake_client.patched_body is not None
    assert fake_client.comments == []
    assert fake_client.added_labels == []
    assert fake_client.removed_labels == ["pawchestrator:needs-info"]
    assert result.report.body_updated is True
    assert result.report.comment_posted is False
    assert json.loads(result.artifact_path.read_text(encoding="utf-8"))["schema"] == (
        "pawchestrator.grill_report.v1"
    )


def test_run_grill_reuses_existing_run_snapshot_before_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    fake_client = FakeGitHubClient()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    async def fail_snapshot_issue(*args: object, **kwargs: object) -> object:
        raise AssertionError("snapshot_issue should not be called")

    monkeypatch.setattr(grill_module, "snapshot_issue", fail_snapshot_issue)

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            repo_path=repo_path,
            runner=FakeRunner(),
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert result.run_id == run_id
    assert result.report.schema == "pawchestrator.grill_report.v1"


def test_run_grill_posts_comment_and_applies_label_for_questions(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id, body="Original\n\n## Pawchestrator Suggested Criteria\n\n- [ ] Existing")
    fake_runner = FakeRunner(
        {
            "schema": "pawchestrator.grill_report.v1",
            "status": "needs_info",
            "suggested_criteria": ["New"],
            "unanswerable_questions": ["Which command verifies this?"],
        }
    )
    fake_client = FakeGitHubClient()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            repo_path=repo_path,
            runner=fake_runner,
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert fake_client.patched_body is not None
    assert "- [ ] Existing" in fake_client.patched_body
    assert "- [ ] New" in fake_client.patched_body
    assert fake_client.patched_body.index("- [ ] New") < fake_client.patched_body.index(
        GENERATED_BY_FOOTER
    )
    assert len(fake_client.comments) == 1
    assert "Which command verifies this?" in fake_client.comments[0]
    assert fake_client.comments[0].endswith(GENERATED_BY_FOOTER)
    assert fake_client.added_labels == ["pawchestrator:needs-info"]
    assert fake_client.removed_labels == []
    assert result.report.comment_posted is True
    assert result.report.comment_id == 123


def test_run_grill_uses_dedupe_runner_to_skip_paraphrased_criteria(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(
        settings,
        run_id,
        body="- [ ] The issue should not append paraphrased criteria twice.",
    )
    grill_runner = FakeRunner(
        {
            "schema": "pawchestrator.grill_report.v1",
            "status": "success",
            "suggested_criteria": [
                "Avoid adding duplicate acceptance criteria when phrased differently.",
                "Log when the fallback deduper is used.",
            ],
            "unanswerable_questions": [],
        }
    )
    dedupe_runner = FakeRunner(
        {
            "schema": "pawchestrator.criteria_dedupe.v1",
            "unique_suggested_criteria": [
                "Log when the fallback deduper is used.",
            ],
        }
    )
    fake_client = FakeGitHubClient()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    def fake_resolve_runner(
        resolved_settings: Settings, stage_name: str, default: str
    ) -> Runner:
        assert resolved_settings == settings
        assert default == "claude"
        assert stage_name == "criteria_dedupe"
        return dedupe_runner

    monkeypatch.setattr(grill_module, "resolve_runner", fake_resolve_runner)

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            repo_path=repo_path,
            runner=grill_runner,
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert dedupe_runner.task is not None
    assert fake_client.patched_body is not None
    assert "Avoid adding duplicate acceptance criteria" not in fake_client.patched_body
    assert "- [ ] Log when the fallback deduper is used." in fake_client.patched_body
    assert result.report.suggested_criteria == ["Log when the fallback deduper is used."]


def test_run_grill_falls_back_to_codex_for_claude_usage_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={
            "grill": StageSettings(
                codex={"sandbox": "danger-full-access", "bypass_sandbox": True}
            )
        },
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    fake_client = FakeGitHubClient()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    seen: dict[str, object] = {}

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        seen["task"] = task
        seen["sandbox"] = self.stage_overrides["grill"].codex.sandbox
        seen["bypass_sandbox"] = self.stage_overrides["grill"].codex.bypass_sandbox
        return RunnerResult(
            exit_code=0,
            stdout="{}",
            stderr="",
            artifact={
                "schema": "pawchestrator.grill_report.v1",
                "status": "success",
                "suggested_criteria": [],
                "unanswerable_questions": [],
            },
        )

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            repo_path=repo_path,
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert result.report.schema == "pawchestrator.grill_report.v1"
    assert isinstance(seen["task"], RunnerTask)
    assert seen["sandbox"] == "read-only"
    assert seen["bypass_sandbox"] is False
    log = result.log_path.read_text(encoding="utf-8")
    assert "[claude stdout]" in log
    assert "[codex stdout]" in log
    warnings = asyncio.run(get_run_warnings(settings, run_id))
    assert [warning["code"] for warning in warnings] == ["grill_usage_limit_fallback"]
    assert warnings[0]["message"] == "Claude usage limit exhausted; using Codex for grill."


def test_run_grill_respects_disabled_usage_limit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"grill": StageSettings(usage_limit_fallback_runner="none")},
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    async def fake_check_health(self: Runner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_claude_run_task(
        self: ClaudeRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(
            exit_code=1,
            stdout=json.dumps(
                {
                    "is_error": True,
                    "api_error_status": 429,
                    "error": "Claude usage limit reached for this session.",
                }
            ),
            stderr="",
            artifact=None,
        )

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        raise AssertionError("codex should not run")

    monkeypatch.setattr(ClaudeRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(ClaudeRunner, "run_task", fake_claude_run_task)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
        asyncio.run(
            run_grill(
                "https://github.com/owner/repo/issues/42",
                settings,
                run_id=run_id,
                repo_path=repo_path,
                github_client=FakeGitHubClient(),  # type: ignore[arg-type]
            )
        )

    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_run_grill_codex_primary_does_not_self_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        app_dir=tmp_path,
        stages={"grill": StageSettings(runner="codex")},
    )
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    async def fake_check_health(self: CodexRunner) -> tuple[bool, str]:
        return True, "ok"

    async def fake_codex_run_task(
        self: CodexRunner,
        task: RunnerTask,
    ) -> RunnerResult:
        return RunnerResult(exit_code=1, stdout="", stderr="codex failed", artifact=None)

    monkeypatch.setattr(CodexRunner, "check_health", fake_check_health)
    monkeypatch.setattr(CodexRunner, "run_task", fake_codex_run_task)

    with pytest.raises(RunnerFailedError, match="Runner exited with code 1"):
        asyncio.run(
            run_grill(
                "https://github.com/owner/repo/issues/42",
                settings,
                run_id=run_id,
                repo_path=repo_path,
                github_client=FakeGitHubClient(),  # type: ignore[arg-type]
            )
        )

    assert asyncio.run(get_run_warnings(settings, run_id)) == []


def test_publish_report_marks_body_unchanged_when_all_criteria_are_semantic_duplicates(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    client = FakeGitHubClient()
    dedupe_runner = FakeRunner(
        {
            "schema": "pawchestrator.criteria_dedupe.v1",
            "unique_suggested_criteria": [],
        }
    )

    report = asyncio.run(
        grill_module._publish_report(
            client,  # type: ignore[arg-type]
            {
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "body": (
                    "## Acceptance Criteria\n\n"
                    "- [ ] Avoid adding duplicate acceptance criteria when phrased differently.\n"
                ),
            },
            {
                "schema": "pawchestrator.grill_report.v1",
                "status": "success",
                "suggested_criteria": [
                    "Do not append paraphrased acceptance criteria more than once.",
                ],
                "unanswerable_questions": [],
            },
            settings=settings,
            run_id="run-123",
            repo_path=repo_path,
            dedupe_runner=dedupe_runner,
        )
    )

    assert dedupe_runner.task is not None
    assert client.patched_body is None
    assert report.body_updated is False
    assert report.suggested_criteria == []


def test_publish_report_treats_checked_existing_criteria_as_duplicates(
    tmp_path: Path,
) -> None:
    settings = Settings(app_dir=tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    client = FakeGitHubClient()

    report = asyncio.run(
        grill_module._publish_report(
            client,  # type: ignore[arg-type]
            {
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "body": (
                    "## Pawchestrator Suggested Criteria\n\n"
                    "- [x] Preserve completed criteria across grill rounds.\n"
                ),
            },
            {
                "schema": "pawchestrator.grill_report.v1",
                "status": "success",
                "suggested_criteria": [
                    "  preserve completed criteria across grill rounds.  ",
                ],
                "unanswerable_questions": [],
            },
            settings=settings,
            run_id="run-123",
            repo_path=repo_path,
            dedupe_runner=FakeRunner(),
        )
    )

    assert client.patched_body is None
    assert report.body_updated is False
    assert report.suggested_criteria == []


def test_run_grill_degrades_without_registered_repo(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_run(settings, run_id))
    _write_snapshot(settings, run_id)
    fake_runner = FakeRunner()
    fake_client = FakeGitHubClient()

    result = asyncio.run(
        run_grill(
            "https://github.com/owner/repo/issues/42",
            settings,
            run_id=run_id,
            runner=fake_runner,
            github_client=fake_client,  # type: ignore[arg-type]
        )
    )

    assert fake_runner.task is None
    assert result.report.status == "needs_info"
    assert result.report.unanswerable_questions
    assert fake_client.comments
    assert fake_client.added_labels == ["pawchestrator:needs-info"]


def test_build_report_payload_resolves_grill_runner(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(app_dir=tmp_path)
    fake_runner = FakeRunner()
    calls: dict[str, object] = {}
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    def fake_resolve_runner(
        resolved_settings: Settings, stage_name: str, default: str
    ) -> Runner:
        calls["settings"] = resolved_settings
        calls["stage_name"] = stage_name
        calls["default"] = default
        return fake_runner

    monkeypatch.setattr(grill_module, "resolve_runner", fake_resolve_runner)

    payload = asyncio.run(
        grill_module._build_report_payload(
            "run-123",
            settings,
            {
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "title": "Add grill",
                "body": "Issue body",
            },
            local_repo_path=repo_path,
            runner=None,
            log_path=tmp_path / "grill.log",
        )
    )

    assert calls == {
        "settings": settings,
        "stage_name": "grill",
        "default": "claude",
    }
    assert fake_runner.task is not None
    assert fake_runner.task.stage_name == "grill"
    assert payload["schema"] == "pawchestrator.grill_report.v1"


def test_grill_claude_config_forces_read_only_tools() -> None:
    config = ClaudeRunnerSettings(
        allowed_tools=["Read", "Write", "Bash"],
        bypass_permissions=True,
    )
    effective = _effective_claude_config(
        config,
        {
            "grill": StageSettings(
                claude={
                    "allowed_tools": ["Read", "Write"],
                    "bypass_permissions": True,
                }
            )
        },
        "grill",
    )

    assert effective.allowed_tools == ["Read", "Glob", "Grep"]
    assert effective.bypass_permissions is False


def test_grill_defaults_to_claude_runner() -> None:
    runner = resolve_runner(Settings(), "grill", "claude")

    assert isinstance(runner, ClaudeRunner)


def test_grill_stage_can_route_to_codex_runner() -> None:
    settings = Settings(stages={"grill": StageSettings(runner="codex")})

    runner = resolve_runner(settings, "grill", "claude")

    assert isinstance(runner, CodexRunner)


def test_issue_grill_command_prints_outcome(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    async def fake_run_grill(issue_url: str, settings: Settings):
        assert issue_url == "https://github.com/owner/repo/issues/42"
        assert settings.app_dir == tmp_path

        class Result:
            run_id = "run-123"
            artifact_path = tmp_path / "runs" / "run-123" / "grill_report.json"
            report = GrillReport(
                schema="pawchestrator.grill_report.v1",
                status="success",
                suggested_criteria=["criterion"],
                unanswerable_questions=[],
                body_updated=True,
                comment_posted=False,
                comment_id=None,
            )

        return Result()

    monkeypatch.setattr(cli, "run_grill", fake_run_grill)

    result = CliRunner().invoke(
        cli.app,
        ["issue", "grill", "https://github.com/owner/repo/issues/42"],
    )

    assert result.exit_code == 0
    assert "Run ID: run-123" in result.output
    assert "Suggested criteria: 1" in result.output
    assert "Comment posted: False" in result.output


async def _insert_run(settings: Settings, run_id: str) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, workflow_type, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'grill', 'pending', NULL,
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.commit()


def _write_snapshot(settings: Settings, run_id: str, *, body: str = "Issue body") -> None:
    path = settings.app_dir / "runs" / run_id / "issue.snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "pawchestrator.issue_snapshot.v1",
                "owner": "owner",
                "repo": "repo",
                "number": 42,
                "title": "Add grill",
                "body": body,
                "comments": [],
            }
        ),
        encoding="utf-8",
    )
