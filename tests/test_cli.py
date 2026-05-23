from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST
from pawchestrator.config import Settings
from pawchestrator.doctor import STATUS_PASS, CheckResult
from pawchestrator.sessions import save_sessions


def test_serve_uses_localhost_only(monkeypatch) -> None:
    calls = {}

    def fake_run(app_path: str, **kwargs) -> None:
        calls["app_path"] = app_path
        calls.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.serve(port=12345)

    assert calls["app_path"] == "pawchestrator.server:create_app"
    assert calls["factory"] is True
    assert calls["host"] == LOCAL_HOST
    assert calls["host"] != "0.0.0.0"
    assert calls["port"] == 12345


def test_doctor_prints_pass_and_exits_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda settings, port: [
            CheckResult("git", STATUS_PASS, "found"),
        ],
    )

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "Pawchestrator Doctor" in result.output
    assert "PASS git" in result.output


def test_doctor_default_port_is_contract_port() -> None:
    assert DEFAULT_PORT == 38472


def test_issue_start_command_runs_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))
    calls = {}

    async def fake_run_pipeline(issue_url, settings, *, repo_path=None):
        calls["issue_url"] = issue_url
        calls["settings"] = settings
        calls["repo_path"] = repo_path

        class Result:
            run_id = "run-123"
            pr_url = "https://github.com/owner/repo/pull/99"

        return Result()

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    result = CliRunner().invoke(
        cli.app,
        [
            "issue",
            "start",
            "https://github.com/owner/repo/issues/42",
            "--repo-path",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert calls["issue_url"] == "https://github.com/owner/repo/issues/42"
    assert calls["settings"].app_dir == tmp_path
    assert calls["repo_path"] == tmp_path
    assert "Run ID: run-123" in result.output
    assert "Draft PR: https://github.com/owner/repo/pull/99" in result.output


def test_sessions_clear_deletes_sessions_file(tmp_path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    save_sessions(settings, {"tokens": ["known-token"]})
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["sessions", "clear"])

    assert result.exit_code == 0
    assert not settings.sessions_path.exists()
    assert "Cleared pairing sessions" in result.output
