# ADR 0002 — Grill posts LLM-generated text to GitHub

**Status:** Accepted  
**Date:** 2026-05-24

## Context

Pawchestrator's standing policy is that no LLM-generated text appears in GitHub comments or issue bodies. This rule exists because output tokens are expensive and run-status summaries add no value that structured state data cannot provide more cheaply.

The Grill action breaks this rule by design. Grill explores the codebase, infers acceptance criteria, and produces clarifying questions the issue author has not answered. This content cannot be derived from structured state — it is the product of the action. There is no cheaper non-LLM equivalent.

Two alternatives were considered:

1. **Keep output local, show in userscript panel only.** User would have to manually copy questions into GitHub. Adds friction, defeats the purpose of making GitHub the control surface.
2. **Post a template-formatted comment listing structured fields.** The questions themselves are LLM output; wrapping them in a template does not change that. This would be dishonest framing of the same content.

## Decision

Grill is the only Pawchestrator action permitted to write LLM-generated text to GitHub. It may:

- Append `## Pawchestrator Suggested Criteria` to the issue body.
- Post one comment containing unanswerable questions.

It may not:
- Edit or replace existing issue body sections written by humans.
- Post more than one comment per grill run.
- Post a comment if there are zero unanswerable questions.

All other Pawchestrator actions (run comments, labels, PR bodies from pipeline runs) remain template-only.

## Consequences

- CONTEXT.md comment policy updated with explicit grill carve-out.
- `## Pawchestrator Suggested Criteria` heading is idempotent — grill checks for it before appending. Running grill twice on the same issue is safe.
- The grill comment is posted once and never edited (no comment_id tracking needed for grill).
- Future actions that want to write LLM text to GitHub must justify a new carve-out explicitly.
