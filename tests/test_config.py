from pathlib import Path

import pytest
from pydantic import ValidationError

from pawchestrator.config import Settings, load_settings


def test_runner_settings_defaults_match_low_token_profile() -> None:
    settings = Settings()

    assert settings.runners.claude.binary == "claude"
    assert settings.runners.claude.execution == "native"
    assert settings.runners.claude.wsl_enabled is True
    assert settings.runners.claude.wsl_distro is None
    assert settings.runners.claude.wsl_binary is None
    assert settings.runners.claude.model == "sonnet"
    assert settings.runners.claude.effort == "low"
    assert settings.runners.claude.allowed_tools == ["Read", "Glob", "Grep"]
    assert settings.runners.claude.bypass_permissions is False
    assert settings.runners.codex.binary == "codex"
    assert settings.runners.codex.execution == "auto"
    assert settings.runners.codex.wsl_enabled is True
    assert settings.runners.codex.wsl_distro is None
    assert settings.runners.codex.wsl_binary is None
    assert settings.runners.codex.model == "gpt-5.5"
    assert settings.runners.codex.reasoning_effort == "low"
    assert settings.runners.codex.sandbox == "workspace-write"
    assert settings.runners.codex.approval_policy == "never"
    assert settings.runners.codex.bypass_sandbox is False
    assert settings.runners.codex.previous_response_not_found_attempts == 3
    assert settings.codegraph.enabled is True
    assert settings.codegraph.directory == ".codegraph"
    assert settings.codegraph.sync_policy == "safe-lazy"
    assert settings.pr.draft is False
    assert settings.pr.assign is True
    assert settings.review.default_runner == "claude"
    assert settings.review.cross_review is True
    assert settings.pipeline.verify_repair_attempts == 3
    assert settings.pipeline.plan_approval is True
    assert settings.pipeline.plan_approval_max_attempts == 3
    assert settings.pipeline.plan_approval_timeout_hours is None
    assert settings.pipeline.epic_fail_fast is True
    assert settings.pipeline.epic_confirm is False
    assert settings.pipeline.verify_non_code_changes is False
    assert settings.pipeline.non_code_patterns == ["*.md", "*.txt", "docs/**", "adr/**"]
    assert settings.pipeline.epic_branch_mode == "epic"
    assert settings.pipeline.smart_routing.enabled is False
    assert settings.pipeline.smart_routing.skip_plan_when == ["implement"]
    assert settings.pipeline.smart_routing.require_readiness == ["ready"]
    assert settings.pipeline.smart_routing.require_max_risk == "low"
    assert settings.pipeline.smart_routing.confirm_skip is False
    assert settings.checkboxes.headings == [
        "Acceptance Criteria",
        "AC",
        "Definition of Gone",
        "DoD",
        "Checklist",
        "Requirements",
        "Tasks",
    ]
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
execution = "wsl"
wsl_enabled = true
wsl_distro = "Ubuntu"
wsl_binary = "claude-linux"
model = "opus"
effort = "medium"
allowed_tools = ["Read", "Glob"]
bypass_permissions = true

[runners.codex]
binary = "codex-dev"
execution = "wsl"
wsl_enabled = true
wsl_distro = "Ubuntu"
wsl_binary = "codex-linux"
model = "gpt-5.5-fast"
reasoning_effort = "medium"
sandbox = "read-only"
approval_policy = "on-request"
bypass_sandbox = true
previous_response_not_found_attempts = 5

[codegraph]
enabled = false
directory = ".custom-codegraph"
sync_policy = "safe-lazy"

[pr]
draft = true
assign = false

[review]
default_runner = "codex"
cross_review = false

[pipeline]
verify_repair_attempts = 2
plan_approval = false
plan_approval_max_attempts = 4
plan_approval_timeout_hours = 12
epic_fail_fast = false
epic_confirm = true
verify_non_code_changes = true
non_code_patterns = ["*.md", "notes/**"]
epic_branch_mode = "epic-with-sub-issues"

[pipeline.smart_routing]
enabled = true
skip_plan_when = ["implement", "verify"]
require_readiness = ["ready", "accepted"]
require_max_risk = "medium"
confirm_skip = true

[checkboxes]
headings = ["Done When", "Ship List"]

[stages.scout]
usage_limit_fallback_runner = "codex"

[stages.scout.claude]
allowed_tools = ["Read"]
bypass_permissions = false

[stages.implement.codex]
execution = "native"
sandbox = "danger-full-access"
approval_policy = "never"
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.app_dir == app_dir
    assert settings.debug is True
    assert settings.runners.claude.binary == "claude-beta"
    assert settings.runners.claude.execution == "wsl"
    assert settings.runners.claude.wsl_enabled is True
    assert settings.runners.claude.wsl_distro == "Ubuntu"
    assert settings.runners.claude.wsl_binary == "claude-linux"
    assert settings.runners.claude.model == "opus"
    assert settings.runners.claude.effort == "medium"
    assert settings.runners.claude.allowed_tools == ["Read", "Glob"]
    assert settings.runners.claude.bypass_permissions is True
    assert settings.runners.codex.binary == "codex-dev"
    assert settings.runners.codex.execution == "wsl"
    assert settings.runners.codex.wsl_enabled is True
    assert settings.runners.codex.wsl_distro == "Ubuntu"
    assert settings.runners.codex.wsl_binary == "codex-linux"
    assert settings.runners.codex.model == "gpt-5.5-fast"
    assert settings.runners.codex.reasoning_effort == "medium"
    assert settings.runners.codex.sandbox == "read-only"
    assert settings.runners.codex.approval_policy == "on-request"
    assert settings.runners.codex.bypass_sandbox is True
    assert settings.runners.codex.previous_response_not_found_attempts == 5
    assert settings.codegraph.enabled is False
    assert settings.codegraph.directory == ".custom-codegraph"
    assert settings.codegraph.sync_policy == "safe-lazy"
    assert settings.pr.draft is True
    assert settings.pr.assign is False
    assert settings.review.default_runner == "codex"
    assert settings.review.cross_review is False
    assert settings.pipeline.verify_repair_attempts == 2
    assert settings.pipeline.plan_approval is False
    assert settings.pipeline.plan_approval_max_attempts == 4
    assert settings.pipeline.plan_approval_timeout_hours == 12
    assert settings.pipeline.epic_fail_fast is False
    assert settings.pipeline.epic_confirm is True
    assert settings.pipeline.verify_non_code_changes is True
    assert settings.pipeline.non_code_patterns == ["*.md", "notes/**"]
    assert settings.pipeline.epic_branch_mode == "epic-with-sub-issues"
    assert settings.pipeline.smart_routing.enabled is True
    assert settings.pipeline.smart_routing.skip_plan_when == ["implement", "verify"]
    assert settings.pipeline.smart_routing.require_readiness == ["ready", "accepted"]
    assert settings.pipeline.smart_routing.require_max_risk == "medium"
    assert settings.pipeline.smart_routing.confirm_skip is True
    assert settings.checkboxes.headings == ["Done When", "Ship List"]
    assert settings.stages["scout"].usage_limit_fallback_runner == "codex"
    assert settings.stages["scout"].claude.allowed_tools == ["Read"]
    assert settings.stages["scout"].claude.bypass_permissions is False
    assert settings.stages["implement"].codex.execution == "native"
    assert settings.stages["implement"].codex.sandbox == "danger-full-access"
    assert settings.stages["implement"].codex.approval_policy == "never"


def test_load_settings_defaults_pr_when_section_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
debug = true
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.pr.draft is False
    assert settings.pr.assign is True
    assert settings.review.default_runner == "claude"
    assert settings.review.cross_review is True


def test_load_settings_defaults_smart_routing_when_section_missing(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[pipeline]
verify_repair_attempts = 1
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.pipeline.smart_routing.enabled is False
    assert settings.pipeline.smart_routing.skip_plan_when == ["implement"]
    assert settings.pipeline.smart_routing.require_readiness == ["ready"]
    assert settings.pipeline.smart_routing.require_max_risk == "low"
    assert settings.pipeline.smart_routing.confirm_skip is False


def test_load_settings_rejects_invalid_review_default_runner(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[review]
default_runner = "unknown"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(config_path)


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


@pytest.mark.parametrize(
    "pipeline_toml",
    [
        'verify_non_code_changes = "true"',
        'non_code_patterns = "*.md"',
        'non_code_patterns = ["*.md", 42]',
    ],
)
def test_load_settings_rejects_invalid_pipeline_non_code_types(
    tmp_path: Path, pipeline_toml: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[pipeline]
{pipeline_toml}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(config_path)
