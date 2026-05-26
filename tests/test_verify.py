import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path

import aiosqlite
import pytest
from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import Settings
from pawchestrator.db import init_db
from pawchestrator.verify import (
    CommandResult,
    ShellRunner,
    VerificationResult,
    all_files_match_non_code,
    load_verify_commands,
    repo_verify_config_path_for,
    run_verify,
)


class FakeShellRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, str, Path]] = []

    async def run_command(self, name: str, command: str, cwd: Path) -> CommandResult:
        self.calls.append((name, command, cwd))
        return self.results.pop(0)


def test_load_verify_commands_returns_none_for_missing_config(tmp_path: Path) -> None:
    assert load_verify_commands(tmp_path / "missing.toml") is None


def test_load_verify_commands_skips_empty_values_and_preserves_order(tmp_path: Path) -> None:
    config_path = tmp_path / "repo.toml"
    config_path.write_text(
        '[commands]\nbuild = "python -m build"\ntest = "pytest"\nlint = ""\n',
        encoding="utf-8",
    )

    commands = load_verify_commands(config_path)

    assert commands is not None
    assert [(command.name, command.command) for command in commands] == [
        ("build", "python -m build"),
        ("test", "pytest"),
    ]


def test_all_files_match_non_code_returns_true_for_docs_only_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> object:
        assert args[0] == ["git", "diff", "--name-only", "main...HEAD"]
        assert kwargs["cwd"] == tmp_path
        return subprocess_completed(stdout="docs/usage.md\ndocs/setup/install.md\n")

    monkeypatch.setattr("pawchestrator.verify.subprocess.run", fake_run)

    assert all_files_match_non_code(tmp_path, "main", ["docs/**"])


def test_all_files_match_non_code_returns_false_for_mixed_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pawchestrator.verify.subprocess.run",
        lambda *args, **kwargs: subprocess_completed(
            stdout="docs/usage.md\npawchestrator/verify.py\n"
        ),
    )

    assert not all_files_match_non_code(tmp_path, "main", ["docs/**"])


def test_all_files_match_non_code_returns_false_for_git_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr("pawchestrator.verify.subprocess.run", fake_run)

    assert not all_files_match_non_code(tmp_path, "main", ["docs/**"])


def test_all_files_match_non_code_returns_false_for_empty_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pawchestrator.verify.subprocess.run",
        lambda *args, **kwargs: subprocess_completed(stdout=""),
    )

    assert not all_files_match_non_code(tmp_path, "main", ["docs/**"])


def test_shell_runner_captures_exit_code_stdout_stderr(tmp_path: Path) -> None:
    runner = ShellRunner(timeout_seconds=5)

    result = asyncio.run(
        runner.run_command(
            "test",
            (
                "python -c \"import sys; "
                "print('out'); print('err', file=sys.stderr); sys.exit(3)\""
            ),
            tmp_path,
        )
    )

    assert result.name == "test"
    assert result.exit_code == 3
    assert "out" in result.stdout
    assert "err" in result.stderr


def test_shell_runner_reports_timeout(tmp_path: Path) -> None:
    runner = ShellRunner(timeout_seconds=1)

    result = asyncio.run(
        runner.run_command(
            "test",
            "python -c \"import time; time.sleep(5)\"",
            tmp_path,
        )
    )

    assert result.exit_code == 124
    assert "Command timed out after 1 seconds." in result.stderr


def test_run_verify_debug_streams_shell_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(app_dir=tmp_path, debug=True)
    run_id = "run-debug"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))
    _write_repo_config(
        worktree_path,
        build=(
            "python -c \"import sys; "
            "print('debug out'); print('debug err', file=sys.stderr)\""
        ),
        test="",
    )

    result = asyncio.run(run_verify(run_id, settings))

    output = capsys.readouterr().out
    assert result.report["status"] == "passed"
    assert "[pawchestrator:debug] run=run-debug stage=verify command=build" in output
    assert "[pawchestrator:debug] shell=python -c" in output
    assert "[pawchestrator:debug] stdout:" in output
    assert "debug out" in output
    assert "[pawchestrator:debug] stderr:" in output
    assert "debug err" in output
    assert "[pawchestrator:debug] run=run-debug stage=verify command=build exit_code=0" in output


def test_run_verify_does_not_print_debug_when_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(app_dir=tmp_path, debug=False)
    run_id = "run-debug-off"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))
    _write_repo_config(
        worktree_path,
        build="python -c \"print('quiet out')\"",
        test="",
    )

    result = asyncio.run(run_verify(run_id, settings))

    output = capsys.readouterr().out
    assert result.report["status"] == "passed"
    assert "[pawchestrator:debug]" not in output
    assert "quiet out" not in output


def test_shell_runner_debug_prints_one_label_for_progress_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = ShellRunner(timeout_seconds=5, debug=True, run_id="run-progress")

    result = asyncio.run(
        runner.run_command(
            "test",
            (
                "python -c \"import sys; "
                "[sys.stdout.write('.') or sys.stdout.flush() for _ in range(20)]\""
            ),
            tmp_path,
        )
    )

    output = capsys.readouterr().out
    assert result.exit_code == 0
    assert output.count("[pawchestrator:debug] stdout:") == 1
    assert "...................." in output


def test_run_verify_writes_passed_report_log_and_records_stage(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))
    _write_repo_config(worktree_path)
    runner = FakeShellRunner(
        [
            CommandResult("build", "python -m build", 0, "built\n", ""),
            CommandResult("test", "pytest", 0, "passed\n", ""),
        ]
    )

    result = asyncio.run(run_verify(run_id, settings, runner=runner))

    assert runner.calls == [
        ("build", "python -m build", worktree_path),
        ("test", "pytest", worktree_path),
    ]
    assert result.artifact_path == tmp_path / "runs" / run_id / "verification_report.json"
    assert result.log_path == tmp_path / "runs" / run_id / "stdout" / "verify.log"
    report = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert report["schema"] == "pawchestrator.verification_report.v1"
    assert report["status"] == "passed"
    assert set(report["commands"][0]) == {
        "command",
        "exit_code",
        "stdout_summary",
        "stderr_summary",
    }
    assert report["commands"][0]["stdout_summary"] == "built"
    assert "[command] build: python -m build" in result.log_path.read_text(encoding="utf-8")

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (run_id,),
        ).fetchone()
        artifact = db.execute(
            """
            SELECT artifact_type, file_path FROM artifacts
            WHERE run_id = ? AND artifact_type = 'verification_report'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("verify_complete", "verify")
    assert stage == ("complete", None)
    assert artifact == ("verification_report", str(result.artifact_path))


def test_run_verify_stops_after_first_failure(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))
    _write_repo_config(worktree_path, lint="ruff check .")
    runner = FakeShellRunner(
        [
            CommandResult("build", "python -m build", 0, "built\n", ""),
            CommandResult("test", "pytest", 8, "", "failed\n"),
            CommandResult("lint", "ruff check .", 0, "", ""),
        ]
    )

    result = asyncio.run(run_verify(run_id, settings, runner=runner))

    assert [call[0] for call in runner.calls] == ["build", "test"]
    assert result.report["status"] == "failed"
    assert result.report["commands"][-1]["stderr_summary"] == "failed"

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        run = db.execute(
            "SELECT status, current_stage FROM workflow_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (run_id,),
        ).fetchone()

    assert run == ("verify_failed", "verify")
    assert stage == ("failed", "test exited 8: failed")


def test_run_verify_skips_missing_config(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))

    result = asyncio.run(run_verify(run_id, settings, runner=FakeShellRunner([])))

    assert result.report["status"] == "skipped"
    assert result.report["commands"] == []
    assert "no repo config found" in result.report["skip_reason"]
    assert "skipped" in result.log_path.read_text(encoding="utf-8")

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (run_id,),
        ).fetchone()

    assert stage[0] == "skipped"
    assert "no repo config found" in stage[1]


def test_run_verify_skips_when_build_and_test_empty(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=worktree_path))
    _write_repo_config(worktree_path, build="", test="", lint="ruff check .")

    result = asyncio.run(run_verify(run_id, settings, runner=FakeShellRunner([])))

    assert result.report["status"] == "skipped"
    assert "no build or test commands configured" in result.report["skip_reason"]


def test_run_verify_fails_when_worktree_record_missing(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    run_id = "run-123"
    asyncio.run(_insert_implement_run(settings, run_id, worktree_path=None))

    with pytest.raises(RuntimeError, match="worktree record not found"):
        asyncio.run(run_verify(run_id, settings, runner=FakeShellRunner([])))

    with sqlite3.connect(tmp_path / "database.sqlite") as db:
        stage = db.execute(
            """
            SELECT status, error FROM workflow_stages
            WHERE run_id = ? AND stage_name = 'verify'
            """,
            (run_id,),
        ).fetchone()

    assert stage[0] == "failed"
    assert "worktree record not found" in stage[1]


def test_run_verify_reports_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with pytest.raises(ValueError, match="run not found: missing"):
        asyncio.run(run_verify("missing", settings, runner=FakeShellRunner([])))


def test_run_verify_command_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    async def fake_run_verify(run_id: str, settings: Settings) -> VerificationResult:
        assert run_id == "run-123"
        assert settings.app_dir == tmp_path
        return VerificationResult(
            run_id=run_id,
            artifact_path=tmp_path / "runs" / run_id / "verification_report.json",
            log_path=tmp_path / "runs" / run_id / "stdout" / "verify.log",
            report={
                "status": "passed",
                "commands": [
                    {
                        "name": "build",
                        "command": "python -m build",
                        "exit_code": 0,
                    },
                    {
                        "name": "test",
                        "command": "pytest",
                        "exit_code": 0,
                    },
                ],
                "skip_reason": None,
            },
        )

    monkeypatch.setattr(cli, "run_verify", fake_run_verify)

    result = CliRunner().invoke(cli.app, ["run", "verify", "run-123"])

    assert result.exit_code == 0
    assert "[verify] build... exit 0 PASS" in result.output
    assert "[verify] test... exit 0 PASS" in result.output
    assert "[verify] PASSED" in result.output


async def _insert_implement_run(
    settings: Settings,
    run_id: str,
    *,
    worktree_path: Path | None,
) -> None:
    await init_db(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            """
            INSERT INTO workflow_runs (
              id, owner, repo, issue_number, status, current_stage,
              created_at, updated_at
            )
            VALUES (
              ?, 'owner', 'repo', 42, 'implement_complete', 'implement',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        await db.execute(
            """
            INSERT INTO workflow_stages (
              id, run_id, stage_name, status, started_at, completed_at
            )
            VALUES (
              'stage-123', ?, 'implement', 'complete',
              '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
            )
            """,
            (run_id,),
        )
        if worktree_path is not None:
            await db.execute(
                """
                INSERT INTO worktrees (
                  id, run_id, owner, repo, issue_number, branch, path,
                  created_at, updated_at
                )
                VALUES (
                  'worktree-123', ?, 'owner', 'repo', 42,
                  'paw/issue-42-test', ?,
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
                )
                """,
                (run_id, str(worktree_path)),
            )
        await db.commit()


def _write_repo_config(
    worktree_path: Path,
    *,
    build: str = "python -m build",
    test: str = "pytest",
    lint: str = "",
) -> None:
    path = repo_verify_config_path_for(worktree_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "[commands]\n"
            f"build = {json.dumps(build)}\n"
            f"test = {json.dumps(test)}\n"
            f"lint = {json.dumps(lint)}\n"
        ),
        encoding="utf-8",
    )


def subprocess_completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
