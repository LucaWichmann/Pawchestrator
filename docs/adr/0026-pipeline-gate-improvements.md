# ADR 0026 — Pipeline gate improvements

## Status

Accepted (grilled 2026-06-04)

## Context

Three related gaps in pipeline gate behavior:

1. **PlanApprovalGate does not survive daemon restarts.** Runs in `awaiting_plan_approval` are currently failed immediately by `fail_stale_runs_on_startup`. The plan artifact exists on disk when the gate fires — resuming is possible but not implemented. A daemon restart (update, crash, intentional stop) loses the pending plan approval.

2. **EpicArchitect creates sub-issues without confirmation.** `POST /issue/epic-architect` immediately calls the GitHub sub-issues API after the Architect stage completes. Creating sub-issues is a visible, hard-to-reverse GitHub action. A gate (parallel to PlanApprovalGate) was deferred at design time. The `epic_confirm` config key already exists in `PipelineSettings` but is wired to nothing.

3. **Base branch is hardcoded to `"main"`.** Repositories using `develop`, `master`, or other trunk branch names require passing `--base-branch` on every CLI invocation. There is no config-level default.

---

## Decisions

### Decision 1 — Resume `awaiting_plan_approval` runs on daemon start

On daemon startup, instead of failing runs in `awaiting_plan_approval`, the daemon re-registers the approval event for each such run and re-surfaces the plan approval UI in the panel.

The plan artifact on disk is treated as valid and does not need to be re-generated. The pipeline resumes from the gate: user approves → implement runs; user rejects → re-plan cycle; user aborts → run fails.

**Staleness check:** Before re-surfacing the gate, the daemon fetches the issue's `updated_at` from GitHub and compares it against the plan artifact's `created_at`. If the issue was updated after the plan was created, a `RunWarning` is emitted (code: `plan_stale_after_restart`) with the delta. The user sees the warning in the panel alongside the plan approval UI and can choose to reject and re-plan.

The check is advisory — the gate is still surfaced even when stale. Forcing a re-plan would be surprising and paternalistic; the warning gives the user the information to decide.

**`fail_stale_runs_on_startup` change:** `awaiting_plan_approval` is removed from the set of statuses that are auto-failed on startup. All other paused/mid-run statuses continue to be failed.

---

### Decision 2 — EpicArchitect gate wired to `epic_confirm`

When `epic_confirm = true`, after the `epic_architect` stage produces `EpicArchitectPlan`, the run transitions to a new `awaiting_epic_approval` status. The panel surfaces the proposed sub-issue list (titles, descriptions, dependency order) with Approve and Abort buttons. Only on Approve does Pawchestrator call the GitHub sub-issues API.

The gate reuses the approval event infrastructure from PlanApprovalGate (`register_approval_event`, `approval_decision`). No reject cycle — EpicArchitect has no re-plan equivalent; Abort is the only alternative to Approve.

**`epic_confirm` default is `false`.**  Pawchestrator's primary value proposition is autonomous operation. Creating sub-issues is less risky than pushing code; users who want the gate opt in explicitly. This is consistent with the principle that gates are available but not mandatory.

**Alternative rejected:** Default `true` (confirmation required). Rejected because it breaks the autonomous-by-default principle. A new user triggering "Turn into Epic" and seeing a confirmation gate they didn't ask for is more surprising than sub-issues being created directly.

---

### Decision 3 — Configurable default base branch

```toml
[pipeline]
base_branch = "main"
```

Added to `PipelineSettings`. All pipeline, implement, and verify code already accepts `base_branch` as a parameter — this change threads the config value through rather than using the hardcoded string `"main"`.

Repositories using `develop` or `master` as trunk no longer require `--base-branch` on every CLI invocation.

---

## Consequences

- `fail_stale_runs_on_startup` removes `awaiting_plan_approval` from its target set.
- Daemon startup gains a `resume_pending_approvals` step that re-registers events for pending plan approval runs and fetches GitHub `updated_at` for each.
- New `awaiting_epic_approval` run status added to `workflow_runs` valid status set and DB.
- `run_epic_architect` wires the `epic_confirm` config key to a new gate step.
- Panel renders an EpicArchitect approval sub-view (sub-issue list, Approve/Abort) when `status = "awaiting_epic_approval"`.
- `PipelineSettings` gains `base_branch: str = "main"`.
- `run_pipeline`, `run_implement`, `run_verify` read `settings.pipeline.base_branch` as their default instead of the hardcoded string.
- `GET /config` response exposes `base_branch` and `epic_confirm` so the panel can reflect them.
