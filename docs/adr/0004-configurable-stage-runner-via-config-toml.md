# ADR 0004: Configurable stage-to-runner mapping via config.toml

**Status:** Accepted  
**Date:** 2026-05-24

## Context

CONTEXT.md originally deferred configurable stage-to-runner mapping to MVP 1, to be driven by a YAML workflow engine. The MVP 0 mapping was hardcoded: scout/grill/plan → ClaudeRunner, implement → CodexRunner.

The user's requirement is immediate: different workflows benefit from different runner assignments (e.g. claude for implement, codex for plan). Waiting for a YAML engine would block this.

Two approaches were considered:

1. **YAML workflow engine** — full declarative pipeline DSL; stage runner assignment is one field among many. Powerful but a large scope investment before the simpler problem is solved.
2. **Extend `config.toml` `[stages.X]`** — add a `runner` field to the existing per-stage config section. Minimal surface area; leverages the config infrastructure already in place.

## Decision

Implement runner assignment via `[stages.X] runner = "claude"|"codex"` in `config.toml`. Closed enum; validated at load by Pydantic. Stage defaults are backwards-compatible (scout/grill/plan → claude, implement → codex). A `resolve_runner(settings, stage_name, default)` factory in `runners.py` centralises resolution.

The YAML workflow engine remains deferred. This decision solves runner selection only — stage ordering, branching, and human gates are still YAML scope.

## Runner capability model

Runner config defines the ceiling of what a runner may do. Stage constraints narrow within that ceiling. If a stage requires a tool not present in the runner's `allowed_tools`, Pawchestrator warns at `doctor` and at pipeline start (non-fatal, non-aborting).

Grill's read-only constraint (restricts Claude to `Read`, `Glob`, `Grep`) is applied as a hardcoded default in `_effective_claude_config` for `stage_name == "grill"`. This applies regardless of which runner is assigned — but only ClaudeRunner supports tool allowlists. Codex has no CLI equivalent; assigning codex to grill removes the read-only guarantee.

## Consequences

- Runner selection is now a config concern, not a code change.
- Config schema is committed: `StageSettings.runner` is a public field. Future runners must be added to the `Literal` type and registered in `resolve_runner`.
- Codex tool restriction remains a gap. If codex adds `--allowedTools` support, grill's read-only enforcement can be made symmetric.
- The YAML engine, when it arrives, should supersede this field or absorb it — not run in parallel.
