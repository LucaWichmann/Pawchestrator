# ADR 0024 — Run lifecycle management CLI

## Status

Accepted (grilled 2026-06-04)

## Context

As Pawchestrator is used over time, run artifacts accumulate at `~/.pawchestrator/runs/` and worktrees accumulate at `~/.pawchestrator/worktrees/`. There is no way to inspect run history, abort an in-flight run, or reclaim disk space from completed runs. The `run` CLI subapp only exposes per-stage execution commands (`scout`, `plan`, `implement`, `verify`, `pr`), not lifecycle management.

---

## Decisions

### Decision 1 — `pawchestrator run list`

Lists workflow runs from SQLite. Supports filters:

```
pawchestrator run list [--repo owner/repo] [--status failed|complete|running|...] [--limit 20]
```

Output columns per row: `run_id | workflow_type | issue/pr | status | current_stage | created_at | pr_url`.

Default limit: 20 most recent runs.

---

### Decision 2 — `pawchestrator run show <run_id>`

Displays full run detail for a single run:

- Run metadata: id, type, owner/repo, issue/pr number, status, created/updated timestamps
- Stage table: stage name, status, started, completed, error
- Warnings list: stage, code, message, timestamp
- Artifact paths: lists files present under `~/.pawchestrator/runs/{run_id}/`

Gives full observability without requiring direct SQLite access or filesystem browsing.

---

### Decision 3 — `pawchestrator run clean`

Deletes run artifacts and worktrees for matching runs. Does **not** delete SQLite rows — run history remains queryable via `run list` and `run show`.

```
pawchestrator run clean [--older-than 30d] [--status failed|complete] [--dry-run]
```

`--dry-run` prints what would be deleted without deleting. Worktree removal uses `git worktree remove --force` to avoid leaving dangling worktree references.

**Why keep DB rows:** Run history is useful for auditing and debugging. Disk is reclaimed from artifacts (diffs, logs, JSON blobs) and worktrees (git objects). The SQLite rows are negligible in size. Deleting rows would make `run list` misleading — completed runs would silently disappear.

---

### Decision 4 — `auto_clean` config key

```toml
[pipeline]
auto_clean = "14d"    # clean artifacts older than 14 days on daemon start
                      # "false" or omit to disable
```

On daemon startup, if `auto_clean` is set, Pawchestrator runs the equivalent of `run clean --older-than <value> --status failed,complete` non-interactively. Only terminal-status runs are cleaned; active and waiting runs are never touched.

Default 14 days balances disk hygiene with retaining recent run context for debugging. Users who want permanent retention set `auto_clean = false`.

---

### Decision 5 — `pawchestrator run abort <run_id>`

Sends `POST /runs/{run_id}/abort` to the daemon. The daemon cancels the asyncio task for that run, transitions status to `failed` with `error = "aborted by user"`, and closes the SSE stream if active.

The worktree is **not** removed on abort. The user may want to inspect partial changes. Auto-clean handles eventual worktree removal per Decision 4.

**Alternative rejected:** Auto-remove worktree on abort. Rejected because partial implementation output may be valuable for diagnosis or manual salvage.

---

### Decision 6 — `pawchestrator repo remove <owner/repo>`

Deletes the `github_repos` SQLite row for the given `owner/repo`. Does not touch worktrees or run records (those reference owner/repo strings, not the registry row). A new `delete_repo_registration` db function handles the deletion.

---

## Consequences

- `run_app` gains `list`, `show`, `clean`, `abort` subcommands.
- `repo_app` gains `remove` subcommand.
- New db functions: `list_runs`, `get_run_detail`, `list_run_artifacts`, `delete_repo_registration`.
- New server endpoint: `POST /runs/{run_id}/abort`.
- `PipelineSettings` gains `auto_clean: str | Literal[False] = "14d"`.
- Daemon startup calls `auto_clean_runs` when configured.
- `GET /config` response should expose `auto_clean` value so tooling can reflect it.
