# ADR 0008 — Multi-round grill continuation

**Status:** Accepted  
**Date:** 2026-05-25

## Context

Grill posts unanswerable questions as a GitHub comment and previously ended with `grill_complete`. Pawchestrator had no mechanism to detect that the user answered the questions, re-evaluate those answers, or continue grilling until the issue was fully resolved. Users had to manually re-trigger grill and the re-run ignored previous context.

## Decision

Grill becomes multi-round. When questions are posted, the run transitions to `grill_waiting` instead of `grill_complete`. The loop continues until the agent is satisfied (no remaining unanswerable questions).

### Detection

The Tampermonkey userscript detects when the user replies to the questions comment using a `MutationObserver` on `#issuecomment-{comment_id}`. When the reply form appears as a descendant of that element, the submit button label is mutated to "Answer Questions" with a tooltip explaining the action will continue grilling. On submit (reply form disappears from DOM), Tampermonkey fires `POST /issue/grill` automatically.

### Re-grill trigger via panel button

When grill status is `grill_waiting`, the "🔥 Grill Issue" panel button relabels to "Re-grill" and shows a GitHub-styled confirmation dialog before triggering — same pattern as the pipeline-while-grilling guard.

### Pipeline guard

Starting a pipeline run while grill is `grill_waiting` on the same issue shows a GitHub-styled confirmation dialog: "Grill is still waiting for answers on this issue. Are you sure you want to start agentic work?" Yes proceeds, No cancels. Pipeline and grill are otherwise fully independent — other issues are never blocked.

### Server-side continuation

`POST /issue/grill` auto-detects a `grill_waiting` run for the given `owner/repo/number`. If found, it resumes the existing run (same `run_id`); if not, it creates a new run. On resumption, the endpoint:

1. Fetches comments with `in_reply_to_id` matching the stored `comment_id` (the questions comment).
2. Extracts only comment body text (strips author, timestamps) to minimize token spend.
3. Builds re-grill prompt: previous `unanswerable_questions` + reply bodies. Does not re-inject the full issue body if unchanged.
4. Runs the grill agent. Agent is instructed to evaluate answers and emit only *remaining* unresolved questions.
5. If questions remain: posts a new comment, updates `comment_id` in the run, stays `grill_waiting`.
6. If no questions remain: transitions to `grill_complete`, removes `pawchestrator:needs-info` label.

`GrillReport` artifact is overwritten each round with the latest state.

### Stale run handling

`grill_waiting` is excluded from `fail_stale_runs_on_startup`. It is a legitimately parked state with no in-flight agent process and survives server restarts.

### Snapshot schema

`IssueSnapshot.comments` gains `in_reply_to_id: int | null` per entry, populated from the GitHub API, so the re-grill context builder can filter reply comments without a separate API call.

## Alternatives considered

- **Tampermonkey polls for any new comment after questions** — too fickle; unrelated comments trigger re-grill.
- **Separate `POST /issue/grill/continue` endpoint** — splits API surface; no benefit over auto-detection in the existing endpoint.
- **New run per re-grill** — loses conversation thread; agent has no memory of prior rounds.
- **Server-side polling loop** — adds background thread and GitHub rate-limit pressure; Tampermonkey already has the page context to detect replies instantly.

## Consequences

- `grill_waiting` added to `workflow_runs` status space; excluded from stale-run cleanup.
- `IssueSnapshot.comments` schema gains `in_reply_to_id`.
- ADR 0002 updated: "one comment per grill run" → "one comment per round."
- `POST /issue/grill` gains auto-detect-and-resume logic.
- Tampermonkey gains `MutationObserver` reply detection, button label mutation, and two confirmation dialogs (pipeline-while-grilling, re-grill-while-waiting).
