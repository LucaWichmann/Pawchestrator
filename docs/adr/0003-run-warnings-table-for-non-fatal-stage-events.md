# ADR 0003: `run_warnings` table for non-fatal stage events

**Status:** Accepted  
**Date:** 2026-05-24

## Context

The PR stage attempts to assign assignees and request reviews on newly created PRs. When the issue has no assignees, Pawchestrator falls back to querying the repo's admin collaborators via the GitHub API. This call can fail (permissions, network) or return an empty list. In either case the PR should still be created — but the failure should be visible to the user in the GitHub issue comment.

Three approaches were considered for storing and surfacing this warning:

1. **Field in `pr_draft.json` artifact** — write `"assignment_warning": "..."` into the artifact file. `format_run_comment` would need to read the artifact file directly, breaking the current invariant that comments are driven solely from DB state.
2. **`error` column on `workflow_stages`** — reuse the existing error field. But `error` on a stage means the stage failed; a non-fatal warning that lets the stage succeed doesn't fit that semantic.
3. **Separate `run_warnings` table** — a new table with FK to `workflow_runs`, one row per warning. `format_run_comment` queries this table and appends a warnings section. Comment formatting stays DB-driven.

## Decision

Add a `run_warnings` table:

```sql
CREATE TABLE IF NOT EXISTS run_warnings (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  stage_name TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

`code` is a machine-readable tag (e.g. `assignment_lookup_failed`) for filtering. `message` is human-readable text surfaced in the issue comment.

`format_run_comment` queries `run_warnings` by `run_id` and appends a warnings block when warnings exist.

## Rationale

- Keeps comment formatting fully DB-driven (no file reads in comment formatter).
- Preserves the semantic distinction between stage failure (`workflow_stages.error`) and non-fatal advisory events (`run_warnings`).
- The 1:n structure is correct: one run can emit multiple warnings across multiple stages.
- `code` field enables future filtering or deduplication without parsing message text.

## Consequences

- DB schema gains one new table. Added via `CREATE TABLE IF NOT EXISTS` in `SCHEMA_SQL` — no migration tooling required.
- `format_run_comment` gains a dependency on `run_warnings` query results (passed in or fetched).
- Any stage can emit warnings in future by inserting into `run_warnings` — not limited to the PR stage.
