from typer.testing import CliRunner

from pawchestrator import cli
from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST
from pawchestrator.doctor import STATUS_PASS, CheckResult


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
