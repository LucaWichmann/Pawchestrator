import socket
from pathlib import Path

from pawchestrator.config import (
    LOCAL_HOST,
    DEFAULT_PORT,
    ClaudeRunnerSettings,
    CodexRunnerSettings,
    RunnerSettings,
    StageSettings,
)
from pawchestrator.config import Settings
from pawchestrator.doctor import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    CheckResult,
    check_backend_routes,
    check_claude_runner,
    check_codex_runner,
    check_cross_review_runners,
    check_port_available,
    check_stage_tool_mismatches,
    check_sqlite_writable,
    check_wsl,
    has_required_failures,
)
from pawchestrator.sessions import save_sessions


def test_sqlite_check_initializes_database(tmp_path: Path) -> None:
    result = check_sqlite_writable(Settings(app_dir=tmp_path))

    assert result.status == STATUS_PASS
    assert result.required is True
    assert (tmp_path / "database.sqlite").exists()


def test_required_failures_make_doctor_fail() -> None:
    assert has_required_failures(
        [
            CheckResult("required", STATUS_FAIL, "broken", required=True),
        ]
    ) is True


def test_port_check_fails_for_occupied_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind((LOCAL_HOST, 0))
        occupied.listen()
        port = occupied.getsockname()[1]

        result = check_port_available(port)

    assert result.status == STATUS_FAIL


def test_backend_route_check_passes_when_live_app_exposes_grill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    save_sessions(settings, {"tokens": ["known-token"]})

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"paths": {"/issue/start": {}, "/issue/grill": {}}}

    def fake_get(url, *, headers, timeout):
        assert url == f"http://{LOCAL_HOST}:12345/openapi.json"
        assert headers == {"X-Pawchestrator-Token": "known-token"}
        assert timeout == 2.0
        return Response()

    monkeypatch.setattr("pawchestrator.doctor.httpx.get", fake_get)

    result = check_backend_routes(settings, 12345)

    assert result.status == STATUS_PASS
    assert result.required is True


def test_backend_route_check_fails_when_live_app_is_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    save_sessions(settings, {"tokens": ["known-token"]})

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"paths": {"/issue/start": {}}}

    monkeypatch.setattr(
        "pawchestrator.doctor.httpx.get",
        lambda *args, **kwargs: Response(),
    )

    result = check_backend_routes(settings, 12345)

    assert result.status == STATUS_FAIL
    assert result.required is True
    assert "missing /issue/grill" in result.message


def test_backend_route_check_warns_without_pairing_token(tmp_path: Path) -> None:
    result = check_backend_routes(Settings(app_dir=tmp_path), 12345)

    assert result.status == STATUS_WARN
    assert result.required is False


def test_default_port_constant_matches_issue_contract() -> None:
    assert DEFAULT_PORT == 38472


def test_claude_runner_check_is_optional_warning(
    monkeypatch,
) -> None:
    async def fake_check_health(self) -> tuple[bool, str]:
        assert self.config.model == "sonnet"
        assert self.config.effort == "low"
        return False, "claude binary not found on PATH"

    monkeypatch.setattr(
        "pawchestrator.doctor.ClaudeRunner.check_health",
        fake_check_health,
    )

    result = check_claude_runner(Settings())

    assert result.label == "claude"
    assert result.status == STATUS_WARN
    assert result.required is False


def test_codex_runner_check_is_optional_warning(monkeypatch) -> None:
    async def fake_check_health(self) -> tuple[bool, str]:
        assert self.config.model == "gpt-5.5"
        assert self.config.reasoning_effort == "low"
        return False, "codex not found"

    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fake_check_health,
    )

    result = check_codex_runner(Settings())

    assert result.label == "codex"
    assert result.status == STATUS_WARN
    assert result.required is False


def test_codex_runner_check_passes_when_healthy(monkeypatch) -> None:
    async def fake_check_health(self) -> tuple[bool, str]:
        return True, "found at C:\\bin\\codex.exe (codex 1.0.0)"

    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fake_check_health,
    )

    result = check_codex_runner()

    assert result.label == "codex"
    assert result.status == STATUS_PASS
    assert result.required is False


def test_codex_runner_check_warns_for_explicit_wsl_health_failure(monkeypatch) -> None:
    async def fake_check_health(self) -> tuple[bool, str]:
        assert self.config.execution == "wsl"
        return False, "codex is not runnable in WSL: missing linux package"

    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fake_check_health,
    )

    result = check_codex_runner(
        Settings(
            runners=RunnerSettings(
                codex=CodexRunnerSettings(execution="wsl"),
            )
        )
    )

    assert result.label == "codex"
    assert result.status == STATUS_WARN
    assert "not runnable in WSL" in result.message
    assert result.required is False


def test_cross_review_check_warns_when_only_one_runner_is_available(monkeypatch) -> None:
    async def fake_claude_check_health(self) -> tuple[bool, str]:
        return True, "found at C:\\bin\\claude.exe (claude 1.0.0)"

    async def fake_codex_check_health(self) -> tuple[bool, str]:
        return False, "codex not found"

    monkeypatch.setattr(
        "pawchestrator.doctor.ClaudeRunner.check_health",
        fake_claude_check_health,
    )
    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fake_codex_check_health,
    )

    result = check_cross_review_runners(Settings())

    assert result.label == "cross review"
    assert result.status == STATUS_WARN
    assert result.required is False
    assert "only one runner is available" in result.message


def test_cross_review_check_passes_when_disabled(monkeypatch) -> None:
    async def fail_check_health(self) -> tuple[bool, str]:
        raise AssertionError("health check should not run")

    monkeypatch.setattr(
        "pawchestrator.doctor.ClaudeRunner.check_health",
        fail_check_health,
    )
    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fail_check_health,
    )

    result = check_cross_review_runners(Settings(review={"cross_review": False}))

    assert result.label == "cross review"
    assert result.status == STATUS_PASS
    assert result.required is False


def test_stage_tool_mismatch_check_passes_when_claude_allows_required_tools() -> None:
    results = check_stage_tool_mismatches(Settings())

    assert results == []


def test_stage_tool_mismatch_check_warns_for_partial_claude_allowlist() -> None:
    settings = Settings(
        runners=RunnerSettings(
            claude=ClaudeRunnerSettings(allowed_tools=["Read"]),
        ),
        stages={"implement": StageSettings(runner="claude")},
    )

    results = check_stage_tool_mismatches(settings)

    assert [result.label for result in results] == [
        "scout tools",
        "plan tools",
        "implement tools",
    ]
    assert all(result.status == STATUS_WARN for result in results)
    assert all(result.required is False for result in results)
    assert "Glob, Grep" in results[0].message
    assert "Glob, Grep" in results[1].message
    assert "Edit" in results[2].message


def test_stage_tool_mismatch_check_skips_codex_runner() -> None:
    settings = Settings(
        runners=RunnerSettings(
            claude=ClaudeRunnerSettings(allowed_tools=[]),
        ),
        stages={
            "scout": StageSettings(runner="codex"),
            "plan": StageSettings(runner="codex"),
            "grill": StageSettings(runner="codex"),
            "implement": StageSettings(runner="codex"),
        },
    )

    results = check_stage_tool_mismatches(settings)

    assert results == []


def test_wsl_check_warns_when_missing_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("pawchestrator.doctor.sys.platform", "win32")
    monkeypatch.setattr("pawchestrator.doctor.shutil.which", lambda name: None)

    result = check_wsl(Settings())

    assert result.label == "WSL"
    assert result.status == STATUS_WARN
    assert result.required is False
    assert result.message == "wsl.exe not found"


def test_wsl_check_passes_when_available(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = "Default Distribution: Ubuntu"
        stderr = ""

    monkeypatch.setattr("pawchestrator.doctor.sys.platform", "win32")
    monkeypatch.setattr(
        "pawchestrator.doctor.shutil.which",
        lambda name: "C:\\Windows\\System32\\wsl.exe"
        if name in {"wsl.exe", "wsl"}
        else None,
    )
    monkeypatch.setattr(
        "pawchestrator.doctor.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    result = check_wsl(Settings())

    assert result.label == "WSL"
    assert result.status == STATUS_PASS
    assert "available" in result.message


def test_runner_checks_use_configured_settings(monkeypatch) -> None:
    seen = {}

    async def fake_claude_check_health(self) -> tuple[bool, str]:
        seen["claude"] = self.config
        return True, "ok"

    async def fake_codex_check_health(self) -> tuple[bool, str]:
        seen["codex"] = self.config
        return True, "ok"

    monkeypatch.setattr(
        "pawchestrator.doctor.ClaudeRunner.check_health",
        fake_claude_check_health,
    )
    monkeypatch.setattr(
        "pawchestrator.doctor.CodexRunner.check_health",
        fake_codex_check_health,
    )

    settings = Settings(
        runners=RunnerSettings(
            claude=ClaudeRunnerSettings(model="opus", effort="medium"),
            codex=CodexRunnerSettings(model="gpt-5.5-fast", reasoning_effort="medium"),
        )
    )

    check_claude_runner(settings)
    check_codex_runner(settings)

    assert seen["claude"].model == "opus"
    assert seen["claude"].effort == "medium"
    assert seen["codex"].model == "gpt-5.5-fast"
    assert seen["codex"].reasoning_effort == "medium"
