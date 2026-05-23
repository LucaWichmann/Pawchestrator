# ADR 0001: Template-only GitHub comments — no LLM-generated text

**Status:** Accepted  
**Date:** 2026-05-23

## Context

Pawchestrator posts a comment to the GitHub issue when a run starts and edits it in-place as each stage completes. The comment is the primary way users see run progress without leaving GitHub.

Two approaches were considered:

1. **LLM-generated comment body** — ask Claude to summarize each stage's output in prose and include it in the comment.
2. **Template-driven comment body** — Pawchestrator writes the comment itself from structured state: run ID, branch, current stage name, timestamps, PR URL on completion.

## Decision

Use **template-only comments**. No LLM-generated text in any GitHub comment.

## Reasons

- **Output tokens are disproportionately expensive.** Claude output tokens cost ~3–5× more than input tokens. Spending output tokens on GitHub comment summaries adds cost to every run with no direct user benefit — the structured data (stage name, status, PR URL) is already more reliable than a prose summary.
- **Template content is more reliable.** LLM-generated summaries can hallucinate, be verbose, or include sensitive internal details. Factual template data (run ID, stage, branch, PR URL) is always accurate and safe to post publicly.
- **Comments don't need to be human-readable prose.** The userscript already renders live run state in the browser. The GitHub comment serves as a permanent record, not a real-time narrative. Run ID + final PR URL is sufficient.

## Trade-offs

**What we lose:** Richer stage summaries visible on GitHub ("Scout found 3 risks, readiness: ready"). These are useful but available locally in artifacts.

**What we gain:** Zero LLM token overhead for comment updates. Predictable, auditable comment format. No risk of sensitive artifact content leaking into public comments.

## Consequences

- The `github` module must expose `post_run_comment(run_state)` and `edit_run_comment(comment_id, run_state)` that format from structured data only.
- `workflow_runs` table needs a `github_comment_id` column.
- Comment format is defined once as a Python string template — not configurable per-run.
- If users want rich summaries, they read local artifacts or the run detail page (future UI).
