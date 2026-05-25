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
SESSIONS_FILE_NAME = "sessions.json"
DEFAULT_CHECKBOX_HEADINGS = [
    "Acceptance Criteria",
    "AC",
    "Definition of Gone",
    "DoD",
    "Checklist",
    "Requirements",
    "Tasks",
]


class BackendSettings(BaseSettings):
    """Backend bind settings."""

    host: str = LOCAL_HOST
    port: int = DEFAULT_PORT


class ClaudeRunnerSettings(BaseSettings):
    """Claude Code runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    binary: str = "claude"
    execution: Literal["native", "wsl"] = "native"
    wsl_enabled: bool = True
    wsl_distro: str | None = None
    wsl_binary: str | None = None
    model: str = "sonnet"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Glob", "Grep"])
    bypass_permissions: bool = False


class CodexRunnerSettings(BaseSettings):
    """Codex runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    binary: str = "codex"
    execution: Literal["auto", "native", "wsl"] = "auto"
    wsl_enabled: bool = True
    wsl_distro: str | None = None
    wsl_binary: str | None = None
    model: str = "gpt-5.5"
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "low"
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "workspace-write"
    )
    approval_policy: Literal["untrusted", "on-failure", "on-request", "never"] = "never"
    bypass_sandbox: bool = False


class ClaudeStageSettings(BaseSettings):
    """Per-stage Claude permission overrides."""

    model_config = SettingsConfigDict(extra="ignore")

    allowed_tools: list[str] | None = None
    bypass_permissions: bool | None = None


class CodexStageSettings(BaseSettings):
    """Per-stage Codex permission overrides."""

    model_config = SettingsConfigDict(extra="ignore")

    execution: Literal["auto", "native", "wsl"] | None = None
    wsl_enabled: bool | None = None
    wsl_distro: str | None = None
    wsl_binary: str | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    approval_policy: Literal["untrusted", "on-failure", "on-request", "never"] | None = None
    bypass_sandbox: bool | None = None


class StageSettings(BaseSettings):
    """Per-stage runner permission overrides."""

    model_config = SettingsConfigDict(extra="ignore")

    runner: Literal["claude", "codex"] | None = None
    claude: ClaudeStageSettings = Field(default_factory=ClaudeStageSettings)
    codex: CodexStageSettings = Field(default_factory=CodexStageSettings)


class RunnerSettings(BaseSettings):
    """Local agent runner settings."""

    model_config = SettingsConfigDict(extra="ignore")

    claude: ClaudeRunnerSettings = Field(default_factory=ClaudeRunnerSettings)
    codex: CodexRunnerSettings = Field(default_factory=CodexRunnerSettings)


class CodeGraphSettings(BaseSettings):
    """CodeGraph worktree sync settings."""

    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    directory: str = ".codegraph"
    sync_policy: Literal["safe-lazy"] = "safe-lazy"


class PrSettings(BaseSettings):
    """Pull request creation settings."""

    model_config = SettingsConfigDict(extra="ignore")

    draft: bool = False
    assign: bool = True


class PipelineSettings(BaseSettings):
    """Pipeline orchestration settings."""

    model_config = SettingsConfigDict(extra="ignore")

    verify_repair_attempts: int = Field(default=1, ge=0)


class CheckboxSettings(BaseSettings):
    """Issue body checkbox parsing settings."""

    model_config = SettingsConfigDict(extra="ignore")

    headings: list[str] = Field(default_factory=lambda: DEFAULT_CHECKBOX_HEADINGS.copy())


class Settings(BaseSettings):
    """Runtime settings loaded from defaults and optional config.toml."""

    model_config = SettingsConfigDict(extra="ignore")

    app_dir: Path = Field(default_factory=lambda: Path.home() / APP_DIR_NAME)
    debug: bool = False
    backend: BackendSettings = Field(default_factory=BackendSettings)
    runners: RunnerSettings = Field(default_factory=RunnerSettings)
    codegraph: CodeGraphSettings = Field(default_factory=CodeGraphSettings)
    pr: PrSettings = Field(default_factory=PrSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    checkboxes: CheckboxSettings = Field(default_factory=CheckboxSettings)
    stages: dict[str, StageSettings] = Field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.app_dir / CONFIG_FILE_NAME

    @property
    def database_path(self) -> Path:
        return self.app_dir / DATABASE_FILE_NAME

    @property
    def sessions_path(self) -> Path:
        return self.app_dir / SESSIONS_FILE_NAME


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
    codegraph_data = data.get("codegraph", {})
    pr_data = data.get("pr", {})
    pipeline_data = data.get("pipeline", {})
    checkboxes_data = data.get("checkboxes", {})
    stages_data = data.get("stages", {})
    app_dir = Path(app_data.get("app_dir", default_app_dir)).expanduser()
    return Settings(
        app_dir=app_dir,
        debug=bool(app_data.get("debug", False)),
        backend=BackendSettings(**backend_data),
        runners=RunnerSettings(**runners_data),
        codegraph=CodeGraphSettings(**codegraph_data),
        pr=PrSettings(**pr_data),
        pipeline=PipelineSettings(**pipeline_data),
        checkboxes=CheckboxSettings(**checkboxes_data),
        stages={name: StageSettings(**stage) for name, stage in stages_data.items()},
    )


def ensure_app_dir(settings: Settings) -> Path:
    settings.app_dir.mkdir(parents=True, exist_ok=True)
    return settings.app_dir


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as config_file:
        return tomllib.load(config_file)
