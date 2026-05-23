from pathlib import Path

import pytest
from pydantic import ValidationError

from pawchestrator.config import Settings, load_settings


def test_runner_settings_defaults_match_low_token_profile() -> None:
    settings = Settings()

    assert settings.runners.claude.binary == "claude"
    assert settings.runners.claude.model == "sonnet"
    assert settings.runners.claude.effort == "low"
    assert settings.runners.codex.binary == "codex"
    assert settings.runners.codex.model == "gpt-5.5"
    assert settings.runners.codex.reasoning_effort == "low"
    assert settings.debug is False


def test_load_settings_reads_runner_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    app_dir = tmp_path / "app"
    config_path.write_text(
        f"""
[app]
app_dir = "{app_dir.as_posix()}"
debug = true

[runners.claude]
binary = "claude-beta"
model = "opus"
effort = "medium"

[runners.codex]
binary = "codex-dev"
model = "gpt-5.5-fast"
reasoning_effort = "medium"
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.app_dir == app_dir
    assert settings.debug is True
    assert settings.runners.claude.binary == "claude-beta"
    assert settings.runners.claude.model == "opus"
    assert settings.runners.claude.effort == "medium"
    assert settings.runners.codex.binary == "codex-dev"
    assert settings.runners.codex.model == "gpt-5.5-fast"
    assert settings.runners.codex.reasoning_effort == "medium"


def test_load_settings_rejects_invalid_runner_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[runners.codex]
reasoning_effort = "max"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(config_path)
