from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.codegraph import CodeGraphSyncResult
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


def test_serve_warns_when_port_is_already_bound(monkeypatch, capsys) -> None:
    calls = {}

    def fake_run(app_path: str, **kwargs) -> None:
        calls["app_path"] = app_path
        calls.update(kwargs)

    monkeypatch.setattr(cli, "_port_available", lambda port: False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.serve(port=12345)

    captured = capsys.readouterr()
    assert "already in use" in captured.err
    assert "current code" in captured.err
    assert calls["app_path"] == "pawchestrator.server:create_app"


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

    class FakeClient:
        async def fetch_sub_issues(self, _reference):
            return []

    async def fake_run_pipeline(issue_url, settings, *, repo_path=None):
        calls["issue_url"] = issue_url
        calls["settings"] = settings
        calls["repo_path"] = repo_path

        class Result:
            run_id = "run-123"
            pr_url = "https://github.com/owner/repo/pull/99"

        return Result()

    monkeypatch.setattr(cli, "get_gh_token", lambda: "token")
    monkeypatch.setattr(cli, "GitHubIssueClient", lambda _token: FakeClient())
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


def test_checkbox_check_command_uses_gh_token_and_settings(tmp_path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    settings.checkboxes.headings = ["Done When"]
    calls = {}

    class FakeClient:
        def __init__(self, token: str) -> None:
            calls["token"] = token

    async def fake_check_checkbox(client, reference, index, headings):
        calls["client"] = client
        calls["reference"] = reference
        calls["index"] = index
        calls["headings"] = headings
        return True

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "get_gh_token", lambda: "token")
    monkeypatch.setattr(cli, "GitHubIssueClient", FakeClient)
    monkeypatch.setattr(cli, "check_checkbox", fake_check_checkbox)

    result = CliRunner().invoke(cli.app, ["checkbox", "check", "owner/repo/42", "0"])

    assert result.exit_code == 0
    assert calls["token"] == "token"
    assert calls["reference"].owner == "owner"
    assert calls["reference"].repo == "repo"
    assert calls["reference"].number == 42
    assert calls["index"] == 0
    assert calls["headings"] == ["Done When"]
    assert "Checkbox 0 checked: owner/repo/42" in result.output


def test_repo_add_accepts_credentialed_https_remote(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))

    class Completed:
        returncode = 0
        stdout = (
            "origin https://TOKEN@github.com/owner/repo.git (fetch)\n"
            "origin https://TOKEN@github.com/owner/repo.git (push)\n"
        )
        stderr = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Completed())

    result = CliRunner().invoke(cli.app, ["repo", "add", str(tmp_path)])

    assert result.exit_code == 0
    assert f"Registered owner/repo -> {tmp_path}" in result.output


def test_github_remote_parser_accepts_supported_github_url_forms(tmp_path, monkeypatch) -> None:
    remotes = [
        "https://github.com/owner/repo.git",
        "https://TOKEN@github.com/owner/repo.git",
        "https://user:TOKEN@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
    ]

    for remote in remotes:
        class Completed:
            returncode = 0
            stdout = f"origin {remote} (fetch)\n"
            stderr = ""

        monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Completed())

        assert cli._github_remote_owner_repo(tmp_path) == ("owner", "repo")


def test_github_remote_parser_rejects_non_github_remote(tmp_path, monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = "origin https://example.com/owner/repo.git (fetch)\n"
        stderr = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Completed())

    try:
        cli._github_remote_owner_repo(tmp_path)
    except ValueError as error:
        assert str(error) == f"{tmp_path} has no github.com remote"
    else:
        raise AssertionError("expected non-GitHub remote to fail")


def test_sessions_clear_deletes_sessions_file(tmp_path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    save_sessions(settings, {"tokens": ["known-token"]})
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["sessions", "clear"])

    assert result.exit_code == 0
    assert not settings.sessions_path.exists()
    assert "Cleared pairing sessions" in result.output


def test_codegraph_sync_command_prints_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(app_dir=tmp_path))
    calls = {}

    async def fake_sync_codegraph_run(run_id, settings, *, repo_path=None):
        calls["run_id"] = run_id
        calls["settings"] = settings
        calls["repo_path"] = repo_path
        return CodeGraphSyncResult(
            action="copied",
            source=tmp_path / "worktree" / ".codegraph",
            destination=tmp_path / "source" / ".codegraph",
            message="synced merged CodeGraph index back to source",
        )

    monkeypatch.setattr(cli, "_sync_codegraph_run", fake_sync_codegraph_run)

    result = CliRunner().invoke(
        cli.app,
        ["codegraph", "sync", "run-123", "--repo-path", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert calls["run_id"] == "run-123"
    assert calls["settings"].app_dir == tmp_path
    assert calls["repo_path"] == tmp_path
    assert "copied: synced merged CodeGraph index back to source" in result.output
