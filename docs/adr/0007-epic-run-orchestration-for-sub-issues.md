# ADR 0007 — Epic run orchestration for issues with sub-issues

## Status
Accepted

## Context

GitHub issues can have sub-issues (child issues linked via the sub-issues feature). Pawchestrator previously had no awareness of this relationship — clicking "Work on this issue" on an epic would run the pipeline against the parent issue body, ignoring all children.

Two cases needed handling:
1. **Pure epic** — parent issue has no own implementation work; all work is in sub-issues.
2. **Issue with sub-issues** — same treatment decided: parent is always skipped.

GitHub also has a Projects v2 "issue type" concept (Epic as a typed field), accessible only via GraphQL. This was rejected as a detection mechanism — it couples Pawchestrator to GitHub Projects and requires a GraphQL client.

## Decision

**Epic detection:** An issue is an epic if `GET /repos/{owner}/{repo}/issues/{number}/sub_issues` returns a non-empty list. No GraphQL, no Projects dependency.

**Parent pipeline:** Never run the pipeline on a parent that has sub-issues, regardless of its body content. If the parent has own work, the user can trigger it manually after sub-issues are complete.

**Orchestrator:** New `epic.py` module with `run_epic` function. Called pre-pipeline from `server.py`. Fetches sub-issues, then calls `run_pipeline` sequentially for each child. This mirrors the existing pattern (pipeline.py, grill.py) and keeps `run_pipeline` linear and unmodified.

**Depth:** One level of sub-issue expansion only. Sub-issues of sub-issues are not expanded.

**Execution:** Sequential. Sub-issues often touch related code; parallel runs risk conflicting PRs.

**Failure:** Stop on first child failure by default. Configurable via `[pipeline] epic_fail_fast = false`.

**DB grouping:** `group_id` column added to `workflow_runs`. All child runs in an EpicRun share the epic's `group_id`. Queried as `WHERE group_id = ?` to load grouped state.

**API — start:** `POST /issue/start` returns `group_id` + `sub_runs: [{issue_number, run_id}]` when the target issue is an epic.

**API — status:** `GET /issue/{owner}/{repo}/{number}/status` gains an `epic` key (mutually exclusive with `pipeline`). Shape: `{group_id, sub_runs: [{issue_number, run_id, status, current_stage, stages, pr_url}]}`.

**Re-trigger:** No resume logic. Re-triggering an epic after partial failure creates a new EpicRun with a new `group_id`. Previous completed sub-issue runs are orphaned but their PRs remain. Resume logic deferred to a future sprint.

**UX:** Silent auto-start (no confirmation dialog). Configurable via `[pipeline] epic_confirm = true` for teams that want an explicit confirm step before N pipelines launch.

## Alternatives considered

- **GraphQL + Projects issue type for epic detection** — rejected: requires GraphQL client, couples to GitHub Projects, adds auth complexity.
- **Body heuristic to detect "pure epic"** — rejected: fragile, false positives.
- **Run parent pipeline after sub-issues** — rejected: rare case, ambiguous definition of "own work", user can trigger manually.
- **Parallel sub-issue execution** — rejected: conflicting PRs, unpredictable resource usage.
- **Full recursive expansion** — rejected: complexity, cycle risk, rare in practice.
- **Resume from failed sub-issue** — deferred: requires cross-run state tracking; not worth the complexity now.
