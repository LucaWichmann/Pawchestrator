# ADR 0009: Skip verification for non-code-only changes

**Status:** Accepted  
**Date:** 2026-05-26

## Context

The verify stage runs ShellRunner build/test commands on every pipeline run. When Pawchestrator only touches documentation (`.md`, `docs/**`, etc.), running the full build and test suite wastes time with no quality benefit — no executable code changed.

Three approaches were considered:

1. **Always run** — current behaviour; no special-casing.
2. **Implement stage signals it** — `ImplementationResult` grows a `code_files_changed: bool` field populated by the LLM/runner reporting what it touched.
3. **Git-diff heuristic** — after implement, `run_pipeline` runs `git diff --name-only <base>...HEAD` in the worktree and classifies each changed file against a configurable glob list.

## Decision

Use **git-diff heuristic** (option 3).

After implement completes, diff the worktree branch against the base branch. If every changed file matches a configured `non_code_patterns` glob, write a stub `VerificationReport` (`status = "skipped"`, `skip_reason` lists the changed files) and record a `skipped` row in `workflow_stages`. The PR stage reads the stub unchanged.

Two `[pipeline]` config keys control the behaviour:

- `verify_non_code_changes: bool = false` — override to `true` to force verification regardless of file types
- `non_code_patterns: list[str] = ["*.md", "*.txt", "docs/**", "adr/**"]` — files matching any pattern are considered non-code

## Reasons

**Git-diff over implement-signals:** The diff is objective and requires no LLM cooperation. An LLM reporting "only docs changed" is less trustworthy than inspecting the actual commit.

**Fail-safe on diff errors:** If `git diff` fails (detached HEAD, no commits, etc.), verification runs anyway. Skipping when the state is unknown risks shipping broken code silently.

**Stub artifact over absent artifact:** The PR stage hard-reads `verification_report.json`. Writing a typed stub (`status = "skipped"`) keeps PR stage code unchanged and makes the skip reason auditable in the artifact store and PR body.

**`skipped` DB status over absent row:** Every stage should have an auditable record. A `skipped` row makes it immediately clear in `workflow_stages` why verify didn't run — avoids confusion between "never reached" and "intentionally skipped".
