# ADR 0013: PR review and repair architecture

**Status:** Accepted  
**Date:** 2026-05-26

## Context

Pawchestrator automates the issue→PR pipeline but leaves code review to humans. Adding AI-driven review from the PR page creates several non-obvious design choices that would confuse a future reader without this record.

## Decisions

### Cross-review: opposite runner reviews paw-PRs

When Pawchestrator opens a PR, the reviewer is deliberately the opposite of the implementer: Codex-implemented PRs are reviewed by Claude; Claude-implemented PRs are reviewed by Codex. This is intentional — the two models have different failure modes and blind spots, so cross-reviewing catches more issues than same-model review. For human-opened PRs (no implementation attribution), the configured `[review] default_runner` is used. When only one runner is healthy, cross-review silently falls back to that runner; `pawchestrator doctor` warns but does not block.

### Agent picks verdict, including APPROVE

The review agent determines the GitHub review event (`REQUEST_CHANGES`, `APPROVE`, or `COMMENT`). It can auto-approve if no blocking issues are found. This is intentional: constraining the agent to always emit `REQUEST_CHANGES` destroys the signal — if the code is good, the review should say so. Blocking issues (bugs, security risks, wrong behavior) trigger `REQUEST_CHANGES`. Minor items that don't block merge are surfaced as SuggestedIssues instead.

### SuggestedIssues are proposed, not auto-created

When the agent approves but flags minor items, it emits `suggested_issues: [{title, body}]` in its artifact. Pawchestrator proposes these in the PR panel; the human clicks "Create Issues" to open them on GitHub. They are never auto-created. Auto-creation would fire GitHub notifications at all repo watchers and permanently populate the issue tracker with AI-generated items without human sign-off — unacceptable before review prompt quality is validated.

### RepairRun is a new run, not an extension of the original

Clicking "Work on Request Changes" creates a new `run_id` with `workflow_type = "repair"`. The original pipeline run is not extended or mutated. Two reasons: (1) the original run may not exist (human-opened PRs have no Pawchestrator run to extend); (2) extending a completed run's state machine requires finding the original by PR number (not indexed) and reopening terminal state — fragile and error-prone. The PR URL is the natural join key if the two runs ever need to be linked in the UI.

### `pr_number` is a separate nullable column, not a reuse of `issue_number`

ReviewRun and RepairRun are scoped to a PR number, not an issue number. Rather than storing PR numbers in the existing `issue_number` column, a new nullable `pr_number` column is added to `workflow_runs`. Reusing `issue_number` creates a lookup ambiguity: issue 42 and PR 42 can coexist in the same repo, and a query for "runs for issue 42" would return repair runs for PR 42.

## Consequences

- New `workflow_type` values: `"review"`, `"repair"`. New stages: `review`, `post`, `issues`, `repair`, `push`.
- New nullable `pr_number` column in `workflow_runs` (migration required).
- New config section `[review]` with `default_runner` and `cross_review` keys.
- New API endpoints: `POST /runs/review/start`, `POST /runs/repair/start`, `GET /prs/{owner}/{repo}/{number}/review-state`, `POST /runs/{run_id}/create-issues`.
- Tampermonkey injects a PR panel above the Conversation tab on `/pull/\d+` pages; polls status every 3s; enforces one active review or repair run per PR.
- Review agent artifact must include `{inline_comments, summary, verdict, suggested_issues}`. Backend translates file line numbers to GitHub diff positions before submitting the review.
- After repair pushes, Pawchestrator re-requests review from all reviewers who had `CHANGES_REQUESTED` state (fetched from GitHub reviews API).
