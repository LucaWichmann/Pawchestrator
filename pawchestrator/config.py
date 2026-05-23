"""Configuration and filesystem paths for Pawchestrator."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 38472
APP_DIR_NAME = ".pawchestrator"
CONFIG_FILE_NAME = "config.toml"
DATABASE_FILE_NAME = "database.sqlite"


class BackendSettings(BaseSettings):
    """Backend bind settings."""

    host: str = LOCAL_HOST
    port: int = DEFAULT_PORT


class ClaudeRunnerSettings(BaseSettings):
    """Claude Code runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    binary: str = "claude"
    model: str = "sonnet"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"


class CodexRunnerSettings(BaseSettings):
    """Codex runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    binary: str = "codex"
    model: str = "gpt-5.5"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "low"


class RunnerSettings(BaseSettings):
    """Local agent runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    claude: ClaudeRunnerSettings = Field(default_factory=ClaudeRunnerSettings)
    codex: CodexRunnerSettings = Field(default_factory=CodexRunnerSettings)


class Settings(BaseSettings):
    """Runtime settings loaded from defaults and optional config.toml."""

    model_config = SettingsConfigDict(extra="ignore")

    app_dir: Path = Field(default_factory=lambda: Path.home() / APP_DIR_NAME)
    backend: BackendSettings = Field(default_factory=BackendSettings)
    runners: RunnerSettings = Field(default_factory=RunnerSettings)

    @property
    def config_path(self) -> Path:
        return self.app_dir / CONFIG_FILE_NAME

    @property
    def database_path(self) -> Path:
        return self.app_dir / DATABASE_FILE_NAME


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from ~/.pawchestrator/config.toml when present."""

    default_app_dir = Path.home() / APP_DIR_NAME
    resolved_config_path = config_path or default_app_dir / CONFIG_FILE_NAME
    if not resolved_config_path.exists():
        return Settings()

    data = _read_toml(resolved_config_path)
    app_data = data.get("app", {})
    backend_data = data.get("backend", {})
    runners_data = data.get("runners", {})
    app_dir = Path(app_data.get("app_dir", default_app_dir)).expanduser()
    return Settings(
        app_dir=app_dir,
        backend=BackendSettings(**backend_data),
        runners=RunnerSettings(**runners_data),
    )


def ensure_app_dir(settings: Settings) -> Path:
    settings.app_dir.mkdir(parents=True, exist_ok=True)
    return settings.app_dir


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as config_file:
        return tomllib.load(config_file)
