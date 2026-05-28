# ADR 0018 — Epic deferred verification and explicit repair framing

## Status
Accepted

## Context

Epic runs (`epic_branch_mode = "epic"`) commit all sub-issue changes into a single shared worktree on a single branch. Running verification after every sub-issue pipeline is correct but expensive — build and test suites run N times for N sub-issues when a single post-epic run would suffice. The time cost grows linearly with epic size.

Additionally, the existing repair loop has a framing problem: when verification fails, `run_implement` is re-invoked with repair context appended to the primary "implement this GitHub issue" prompt. The agent receives its task as "implement the issue" with failure output bolted on as a secondary concern. This is the wrong mental model — the agent's *actual* task is to fix failing tests, not to re-implement the feature.

## Decision

### 1. Verification mode is implicit in `epic_branch_mode` — no new config key

| `epic_branch_mode` | Verification behaviour |
|---|---|
| `epic` | Skip verify in all sub-pipelines. Run a single verify on the epic worktree after all sub-issues complete. |
| `epic-with-sub-issues` | Verify after each sub-issue pipeline (current behaviour, unchanged). |

In `epic` mode, every sub-pipeline passes a skip signal to its verify stage. The sub-run records a `workflow_stages` entry with `status = "skipped"` and `skip_reason = "verification deferred to epic level"` — auditable, timeline-complete, consistent with the existing non-code skip pattern.

### 2. Epic-level verification runs on the parent run

After all sub-issues complete (in `epic` mode), `run_epic` invokes verify against the parent_run_id. The epic worktree contains all sub-issue changes. Verify stores its result as a `workflow_stages` entry on the parent_run_id (`stage_name = "verify"`). The `verification_report.json` artifact is written under `runs/{parent_run_id}/`.

Repair implement attempts (if verify fails) are also stored as `workflow_stages` entries on the parent_run_id (`stage_name = "implement"`). The number of repair attempts reuses `verify_repair_attempts` — one config knob for both contexts.

### 3. Repair framing improved for all repair runs (per-issue and epic)

A new `RepairVerification` skill file replaces `WorkOnIssue` as the primary instruction when a repair attempt runs. The skill's fallback default (used when no user override exists) is terse and explicit:

```
Verification or tests have failed after your implementation.
Your task is to inspect the failure output and fix the failing tests or build errors.
Do not re-implement the feature — only fix what is failing.

Background (what was implemented):
{issue_context}

Verification failure:
{failure_output}
```

Key properties:
- Primary instruction is "fix the failure", not "implement the issue".
- Issue body is present as labeled background, not the task.
- Failure output is piped in explicitly and prominently.
- Instructions are terse — no token waste, but unambiguous.
- `WorkOnIssue` skill is not loaded for repair attempts.

For epic-level repair, `{issue_context}` is the epic issue body + a list of sub-issues that were implemented. For per-issue repair, it is the sub-issue body as today.

### 4. Tampermonkey — "Epic Verification" row

The epic section in the Tampermonkey panel gains a dedicated "Epic Verification" row rendered below all sub-run timelines. It displays a mini pipeline timeline built from the parent run's `workflow_stages` entries (verify + any repair implement stages). The row is only visible in `epic` branch mode and only when the parent run has at least one verify-related stage. Visual language matches sub-run timelines (step indicators, status colours).

The API's `epic` status key gains a `parent_stages` array — the parent run's `workflow_stages` rows — so Tampermonkey can render the epic verification row without a separate fetch.

### 5. PR body — single epic-level verification summary

In `epic` mode, the `## Verification` section of the epic PR body is replaced with a single line derived from the parent run's `verification_report.json`:

```
Verification: passed   (or: failed, skipped)
```

The per-sub-issue verification list (which would show all entries as `skipped`) is suppressed — it is noise when there is a single authoritative epic-level result.

In `epic-with-sub-issues` mode, the PR body is unchanged.

## Alternatives considered

- **Separate `epic_verification_mode` config key** — rejected: the right behaviour is already fully determined by `epic_branch_mode`. A second key adds surface and allows contradictory combinations (`epic-with-sub-issues` + `deferred`) with no meaningful use case.
- **Fail immediately on epic verify failure (no repair)** — rejected: all sub-issue changes land on one branch; repair is feasible and meaningful. Failing immediately forces a full re-trigger for a fixable test failure.
- **Repair at epic level using `WorkOnIssue` + repair context** — rejected: `WorkOnIssue` frames the task as feature implementation. An agent receiving that framing alongside a test failure report has conflicting instruction signals. Explicit repair framing is always correct and always cheaper (less instruction noise).
- **Remove issue body from repair prompt entirely** — rejected: issue body encodes implementation intent that helps the agent understand *why* something was written, which is relevant when diagnosing a test failure. Kept as labeled background, not as primary instruction.
- **Per-sub-issue verify rows in epic PR body showing "skipped"** — rejected: a list of N skipped entries adds noise with zero signal. Single epic-level result is the authoritative answer.
- **Epic verification status inlined into epic section header badge** — rejected: too compact; no room for repair attempt steps. A dedicated row with a timeline is consistent with sub-run rendering and extensible.
