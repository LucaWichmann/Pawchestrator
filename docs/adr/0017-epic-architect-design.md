# ADR 0017 — EpicArchitect: decompose plain issue into GitHub sub-issues

## Status
Accepted

## Context

Agents work best on atomic, well-scoped issues. Large issues ("Add user management system") produce bloated PRs and unreliable results. A feature is needed that decomposes a plain GitHub issue into focused sub-issues, turning it into a GitHub Epic that `EpicRun` can then fan out across.

The original design included an approval modal (user reviews, edits, and approves the proposed sub-issue list before creation). This was rejected in favour of full automation — the UX cost of a review step outweighs the benefit for the typical case, and the Gate infrastructure needed to support it properly is deferred to MVP 1.

## Decision

**Trigger:** "Turn into Epic" button in the issue panel. Hidden when the issue already has sub-issues (already a GitHub Epic). API is not gated beyond this — re-run guard is UI-only, keeping the backend open for future scenarios (e.g. further splitting an existing Epic).

**No approval Gate.** Fully automated. User clicks → sub-issues created. Gate mechanism (pause, present plan, wait for user approval) is deferred to MVP 1 as a generic infrastructure concern.

**`workflow_type = "epic_architect"`** with a `workflow_runs` row, same infrastructure as Grill. Endpoint: `POST /issue/epic-architect {owner, repo, number}` returns `{run_id}`. Panel polls `GET /issue/{owner}/{repo}/{number}/status` which gains an `epic_architect` key.

**Two stages:**
- `epic_scout` — small model (Haiku / GPT-5.4-mini, same pattern as `CriteriaDedupe`). Read-only. Produces `EpicScoutReport`: `{relevant_files: [{path, reason, snippet}], tech_context: string}`. Finds source code implied by the issue body so the Architect stage has accurate codebase context.
- `epic_architect` — LLM decomposes issue into sub-issues. Produces `EpicArchitectPlan`: `{epic_analysis: string, sub_issues: [{title, description, depends_on_indexes: []}]}`. `epic_analysis` is one terse sentence. No labels, no `estimated_complexity`.

**GitHub linking:** Native sub-issues API only (`POST /repos/{owner}/{repo}/issues/{number}/sub_issues`). No markdown checklist appended to the parent — GitHub's native sub-issues UI surfaces the result. No GitHub comment posted for the same reason.

**Dependency ordering:** `depends_on_indexes` (array indexes into `sub_issues`) retained. Pawchestrator owns cycle detection and range validation — not delegated to the LLM. Invalid entries are stripped with a `RunWarning` per strip; remaining valid sub-issues are still created. Creation order follows topological sort; "Depends on: #N" is appended to dependent issue descriptions.

**Partial creation failure:** Fail the stage on first GitHub API error. Record already-created issue numbers in the artifact before failing. Stage status = `failed`; panel shows which sub-issues were created.

## Alternatives considered

- **Approval modal (Gate)** — rejected for now: Gate infrastructure is MVP-1 scope; the synchronous stop/resume pattern is non-trivial to build correctly. Deferred rather than built ad-hoc.
- **Markdown checklist on parent (task list)** — rejected: GitHub's native sub-issues relationship already surfaces a progress bar and linked issues. A markdown checklist is redundant noise that would drift out of sync.
- **Single combined LLM call (scout + architect together)** — rejected: two dedicated stages give granular failure tracking and let the small scout model be tuned independently of the architect model.
- **Delegate cycle detection to LLM prompt** — rejected: adds output tokens, unreliable, and Pawchestrator can validate a dependency graph in O(n) trivially. Less LLM output = lower cost.
- **Show button on all issues including Epics** — rejected: "Turn into Epic" on an already-Epic issue has ambiguous semantics. Hidden when sub-issues exist; future expansion (further splitting) will be a deliberate design decision.
