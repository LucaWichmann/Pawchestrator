# ADR 0010: Claude usage-limit fallback for agent stages

**Status:** Accepted  
**Date:** 2026-05-26

## Context

Several agent stages can use Claude, and Claude CLI can fail mid-run when its session or usage limit is exhausted. That can interrupt Pawchestrator even when Codex could produce the same stage artifact.

Three approaches were considered:

1. **Fail immediately** — simple, but leaves common Claude usage exhaustion as a hard workflow stop.
2. **Fallback on any Claude failure** — resilient, but hides real failures such as invalid JSON, bad schema, missing binaries, or permission problems.
3. **Fallback only on recognized usage exhaustion** — keeps real failures visible while allowing Pawchestrator to continue through the common quota/session-limit case.

## Decision

Use **recognized usage exhaustion only** (option 3).

Usage-limit fallback is stage-local. Known Claude-backed stages with known permission intent (`scout`, `plan`, `grill`, `criteria_dedupe`) default to Codex fallback. Users can disable it per stage with `usage_limit_fallback_runner = "none"` or make it explicit with `usage_limit_fallback_runner = "codex"`.

Fallback applies only when Claude is the primary runner. Codex-primary stages do not self-fallback. Future Claude stages must opt in explicitly once their permission intent is defined.

Pawchestrator emits a `RunWarning` before invoking the fallback runner so browser overlays and GitHub comments can show that Codex is taking over while the stage is still running.

## Reasons

**Stage-local over pipeline-wide:** Runner assignment is already stage-local via `[stages.X] runner`, so fallback belongs beside the stage's runner policy.

**Usage exhaustion only:** Claude usage exhaustion is expected and recoverable. Other Claude failures usually signal broken configuration, invalid output, or a real stage bug and should remain visible.

**Same stage row:** Fallback preserves the stage boundary. The stage still produces one artifact type, and attempt history belongs in the stage log plus `RunWarning`, not extra `workflow_stages` rows.

**Permission intent preserved:** Codex fallback must not gain broader powers just because the runner changed. For read-only Claude stages, Codex fallback runs with a read-only sandbox even if generic Codex stage overrides would be broader for primary Codex execution. If `implement` is explicitly configured with Claude primary and Codex usage-limit fallback, Codex may use the normal write-capable implement permissions because that stage is write-capable by intent.
