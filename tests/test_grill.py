import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator import grill as grill_module
from pawchestrator.config import ClaudeRunnerSettings, Settings, StageSettings
from pawchestrator.db import init_db
from pawchestrator.grill import (
    GrillReport,
    append_suggested_criteria,
    build_dedupe_prompt,
    build_grill_prompt,
    dedupe_criteria,
    run_grill,
)
from pawchestrator.runners import (
    ClaudeRunner,
    CodexRunner,
    Runner,
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


def test_append_suggested_criteria_skips_exact_duplicates() -> None:
    body = "Original\n\n## Pawchestrator Suggested Criteria\n\n- [ ] First\n- [x] Second\n"

    updated_body, updated = append_suggested_criteria(body, ["First", "Second"])

    assert updated is False
    assert updated_body == body


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
    assert len(fake_client.comments) == 1
    assert "Which command verifies this?" in fake_client.comments[0]
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
