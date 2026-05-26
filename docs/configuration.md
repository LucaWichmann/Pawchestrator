# Configuration

Pawchestrator loads optional defaults from `~/.pawchestrator/config.toml`.

## Full example

```toml
[app]
debug = true

[runners.claude]
execution = "native"
model = "sonnet"
effort = "low"
allowed_tools = ["Read", "Glob", "Grep"]
bypass_permissions = false

[runners.codex]
execution = "auto"
model = "gpt-5.5"
reasoning_effort = "low"
sandbox = "workspace-write"
approval_policy = "never"
bypass_sandbox = false
previous_response_not_found_attempts = 3

[codegraph]
enabled = true
directory = ".codegraph"
sync_policy = "safe-lazy"

[pipeline]
verify_repair_attempts = 1
epic_fail_fast = true
epic_confirm = false
epic_branch_mode = "epic"
```

## Notes

- `debug = true` prints runner argv plus captured stdout/stderr.
- `execution = "auto"` on Codex tries native first and may fall back to WSL on known Windows sandbox failures.
- `previous_response_not_found_attempts` caps Codex recovery attempts including the original attempt.

## Per-stage runner

Set a stage's primary runner and usage-limit fallback:

```toml
[stages.scout]
runner = "claude"
usage_limit_fallback_runner = "codex"
```

Disable fallback for a stage:

```toml
[stages.plan]
runner = "claude"
usage_limit_fallback_runner = "none"
```

Runner-specific overrides per stage live under `[stages.<stage>.claude]` and `[stages.<stage>.codex]`.

## Usage-limit fallback

Fallback is stage-local and only handles recognized Claude usage/session exhaustion. Known Claude-primary stages (`scout`, `plan`, `grill`, `criteria_dedupe`) default to Codex fallback when `usage_limit_fallback_runner` is unset. Codex-primary stages do not self-fallback.

Fallback preserves the stage's artifact contract and permission intent. Read-only stages run Codex with a read-only sandbox. See [ADR 0010](adr/0010-claude-usage-limit-fallback.md).

## Criteria dedupe

`grill` runs a `criteria_dedupe` utility stage before publishing suggested criteria to GitHub. It removes semantic duplicates so the `## Pawchestrator Suggested Criteria` section doesn't repeat existing acceptance criteria.

Default (Claude Haiku):

```toml
[stages.criteria_dedupe]
runner = "claude"

[stages.criteria_dedupe.claude]
model = "haiku"
effort = "low"
```

Alternative (Codex):

```toml
[stages.criteria_dedupe]
runner = "codex"

[stages.criteria_dedupe.codex]
model = "gpt-5.4-mini"
reasoning_effort = "low"
```

If the configured LLM fails or returns invalid JSON, Pawchestrator falls back to deterministic normalized dedupe.

## Epic workflow

Pawchestrator treats an issue as an epic when GitHub's sub-issues endpoint returns one or more sub-issues. Only direct sub-issues are expanded.

```toml
[pipeline]
epic_branch_mode = "epic"
```

| Mode | Branches | PRs |
|---|---|---|
| `"epic"` | One shared `paw/epic-{number}-{slug}` branch for all sub-issues. | One final PR from the epic branch to `main`. |
| `"epic-with-sub-issues"` | One epic branch plus one issue branch per sub-issue. | Draft epic PR to `main` first, then each sub-issue opens a PR into the epic branch. |

In `"epic-with-sub-issues"` mode, humans merge child PRs into the epic branch and mark the epic PR ready when complete.

## Local state paths

| Path | Contents |
|---|---|
| `~/.pawchestrator/config.toml` | Optional app and runner defaults. |
| `~/.pawchestrator/database.sqlite` | Workflow runs, stages, repo registrations, and artifact metadata. |
| `~/.pawchestrator/sessions.json` | Browser pairing tokens. |
| `~/.pawchestrator/runs/{run_id}/` | Issue snapshot, scout report, plan, implementation report, verification report, PR draft, and logs. |
| `~/.pawchestrator/worktrees/{owner}/{repo}/issue-{number}/` | Isolated git worktree for each issue run. |
| `<repo>/.pawchestrator/verify.toml` | Tracked repo verification commands. |

## CodeGraph indexes

- If the source repo has `.codegraph/codegraph.db`, Pawchestrator copies it into the issue worktree before the implementation agent runs.
- The copy uses SQLite backup semantics and does not copy WAL/SHM files.
- Worktree index changes stay isolated while the branch is unmerged.
- Sync-back only happens when the branch HEAD is already in `main`, either opportunistically or via `pawchestrator codegraph sync <run-id>`.

## Repo verification config

Commit `.pawchestrator/verify.toml` to each repo so every contributor and every Pawchestrator worktree uses the same verification steps.

```toml
[commands]
build = "cmake --build build"
test = "ctest --test-dir build"
lint = "ruff check ."
```

Pawchestrator runs commands in `build`, `test`, `lint` order and stops on first failure. If the config is missing or no build/test commands are configured, verify skips with a warning.
