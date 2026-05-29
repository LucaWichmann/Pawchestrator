# ADR 0019 — Plan Approval Gate

## Status

Accepted (grilled 2026-05-29)

## Context

Pawchestrator runs the full pipeline (Snapshot → Scout → Plan → Implement → Verify → PR) without any human checkpoint. If the Plan stage produces a hallucinated file structure or misunderstands the issue, the Implement stage wastes significant compute and tokens generating bad code before anyone notices.

A human-in-the-loop gate between Plan and Implement lets the developer review the AI's proposed approach — which files to touch, what to create vs. modify, the overall strategy — and either approve it, reject it with feedback, or abort the run entirely.

---

## Decisions

### Decision 1 — Feature flag, default on

`plan_approval: bool = True` in `PipelineSettings`. Configurable via `[pipeline]` in `config.toml`.

Default is **on** because the primary risk (wasted compute on bad plans) is present on every run. Users who want fully unattended automation can opt out explicitly.

Two related settings:

```toml
[pipeline]
plan_approval = true                  # enable/disable the gate
plan_approval_max_attempts = 3        # max re-plan cycles before auto-fail (minimum 1)
plan_approval_timeout_hours = null    # null = no timeout; integer = auto-fail after N hours
```

`plan_approval_max_attempts` caps human-initiated re-plan cycles. The cap exists even though re-plans are human-triggered: a confused LLM can loop back to a rejected approach, and the cap provides a safety valve. After the cap is reached, the run transitions to `failed` with a clear error message.

`plan_approval_timeout_hours` is implemented via `asyncio.wait_for` wrapping the event await. When elapsed, the run transitions to `failed` with reason `"plan approval timed out"`. `null` (default) leaves the event waiting indefinitely until daemon restart or explicit abort.

All three settings must be documented in the config reference.

---

### Decision 2 — New run status: `awaiting_plan_approval`

`workflow_runs.status` gains a new value: `awaiting_plan_approval`.

Naming follows the `awaiting_*` prefix convention, leaving room for future gates at other stages (e.g., `awaiting_verify_approval`). Status is written to the DB **before** the pipeline suspends so the checkpoint is durable. On daemon restart, `fail_stale_runs_on_startup` marks any `awaiting_plan_approval` run as `failed`.

---

### Decision 3 — Suspend/resume via `asyncio.Event`

The pipeline coroutine suspends on an `asyncio.Event` keyed by `run_id` in an in-process dict. Multiple concurrent runs are fully independent — each has its own event.

**Why not DB polling:** DB polling would let the daemon resume paused pipelines after restart, but `run_pipeline` is an asyncio coroutine. The coroutine is destroyed on restart regardless of whether the pause state lives in memory or DB. Resume-from-checkpoint would require reconstructing mid-pipeline state from artifacts — a separate, larger feature. For now, restart = fail, consistent with how every other mid-run crash is handled. The durable `awaiting_plan_approval` DB status means a future resume system can detect the checkpoint, verify the artifact exists, and re-prompt the user for approval without losing context.

**Parallelism:** Each `run_id` maps to one `asyncio.Event`. Approving run A does not affect run B. No cross-run interference.

**Crash recovery:** `fail_stale_runs_on_startup` is extended to include `awaiting_plan_approval` in its set of stale statuses. Honest failure ("daemon restarted during plan approval") is preferable to a silent hang.

---

### Decision 4 — `implementation_plan.json` artifact unchanged; server-side projection for UI

`implementation_plan.json` schema is not modified. Its existing consumers (`implement.py`, `pr.py`) continue to read it unchanged.

The Plan stage prompt is updated to also emit `file_operations: [{path, type, description}]` alongside the existing fields. `normalize_implementation_plan` normalizes `file_operations` when present and derives/populates `files_to_modify` from it for backward compatibility. Per-file `description` is constrained to one line (~100 chars) in the prompt to avoid unnecessary output token spend.

`GET /runs/{run_id}/plan` returns a projection for UI consumption:

```json
{
  "approach_summary": "string",
  "estimated_risk": "low | medium | high",
  "file_operations": [
    { "path": "src/api/users.py", "type": "modify", "description": "Add GET /users route." },
    { "path": "tests/test_users.py", "type": "create", "description": "Add pytest cases." }
  ],
  "steps": [
    { "order": 1, "description": "string", "files_to_modify": ["..."], "notes": "string" }
  ]
}
```

This avoids coupling the UI response shape to the internal artifact schema and does not risk regressions in implement or PR stages.

---

### Decision 5 — Rejection feedback as artifact file

When the developer rejects a plan, feedback is transmitted via `POST /runs/{run_id}/reject` body (`{"feedback": "string"}`). The daemon appends the feedback entry to `plan_rejections.json`:

```json
[
  { "attempt": 1, "feedback": "Use axios instead of fetch." },
  { "attempt": 2, "feedback": "Move the logic into a service class, not the controller." }
]
```

**Why an artifact file:** All inter-stage context in Pawchestrator flows through artifact files on disk. Rejection feedback is inter-iteration context — it must survive across re-plan invocations and be readable by the prompt builder. Storing it in memory would lose it on restart; storing it in a DB column couples feedback history to the run row schema; storing it as an artifact is consistent with every other piece of context passed between stages.

`plan_rejections.json` is written on the first rejection and appended on every subsequent rejection. The re-plan prompt builder reads the full history so the LLM cannot loop back to a previously-rejected approach.

---

### Decision 6 — Accumulated feedback across rejections

Each re-plan call receives the full rejection history, not just the latest feedback. Cost is negligible (plan prompts are short). Passing only the latest feedback means the LLM can regress to an approach rejected in a prior cycle.

Feedback is injected into `build_plan_prompt` as an appended section after the existing issue/scout context:

```
## Previous plan rejections
Attempt 1: "Use axios instead of fetch."
Attempt 2: "Move the logic into a service class, not the controller."
```

---

### Decision 7 — Abort transitions run to `failed`

No new `cancelled` status is introduced. Abort sets `workflow_runs.status = 'failed'` with a clear abort reason. Existing failure handling (GitHub comment edit, label swap to `pawchestrator:failed`) covers the cleanup path without special-casing.

The abort code path is isolated (single `POST /runs/{run_id}/abort` endpoint, single branch in the approval event handler) so a `cancelled` status can be added in the future by changing only the abort branch without touching approve/reject logic.

---

### Decision 8 — Plan approval sub-view inside existing panel

The plan approval UI is rendered as a sub-view inside the existing `#pawchestrator-panel`, not as a separate DOM injection. The panel auto-expands when `status === 'awaiting_plan_approval'`. Collapsing back to idle state happens when the status transitions away from `awaiting_plan_approval`.

Sub-view content:
- `approach_summary` — one-paragraph plan description
- `estimated_risk` — badge (low / medium / high)
- `file_operations` — grouped by `type` (Create / Modify / Delete), each entry shows path + description
- `steps` — ordered checklist of implementation steps
- Action bar: **Approve** (`btn-primary`), **Reject** (opens inline feedback textarea + submit), **Abort** (`btn-danger`)

Reject flow within panel: clicking Reject reveals an inline `<textarea>` for feedback. Submitting sends `POST /runs/{run_id}/reject`. Panel returns to a "re-planning…" polling state. After the new plan is ready (status returns to `awaiting_plan_approval`), the sub-view re-renders with the updated plan.

**Why the existing panel:** The panel is already positioned directly after the issue body — the exact location the PRD targets. A second injection point would duplicate MutationObserver logic and add surface area for GitHub DOM drift to break.

---

### Decision 9 — New API endpoints

All endpoints are `run_id`-scoped, no `/api/` prefix (consistent with existing route conventions), and require the pairing token:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/runs/{run_id}/plan` | Returns projection of `implementation_plan.json` for UI rendering |
| `POST` | `/runs/{run_id}/approve` | Transitions `awaiting_plan_approval` → resumes pipeline into Implement |
| `POST` | `/runs/{run_id}/reject` | Body: `{"feedback": "string"}`. Appends to `plan_rejections.json`, signals re-plan |
| `POST` | `/runs/{run_id}/abort` | Marks run `failed`, signals event to unblock pipeline coroutine |

Error responses:
- `409 Conflict` if `approve`/`reject`/`abort` called when run is not in `awaiting_plan_approval`
- `404` if `run_id` not found
- `429` if `reject` called after `plan_approval_max_attempts` is exhausted (run already failed)

---

## Implementation scope

### Backend (`pawchestrator/pipeline.py`, `plan.py`, `server.py`, `config.py`, `db.py`)

1. **`config.py`:** Add `plan_approval`, `plan_approval_max_attempts`, `plan_approval_timeout_hours` to `PipelineSettings`. Document all three in config reference.
2. **`plan.py`:** Update `ImplementationPlan` skill prompt to emit `file_operations`. Update `normalize_implementation_plan` to normalize `file_operations` and derive `files_to_modify` from it.
3. **`pipeline.py`:** After `plan_stage()` completes, if `plan_approval = true`: write `awaiting_plan_approval` to DB, register `asyncio.Event` for `run_id`, `await asyncio.wait_for(event, timeout)` (or `await event.wait()` if no timeout). On timeout: mark failed. On resume: check approval decision (approve → continue, reject → re-run plan loop, abort → mark failed).
4. **`server.py`:** Add four new endpoints. `POST /approve` and `POST /abort` call `event.set()` after updating DB state. `POST /reject` appends to `plan_rejections.json` and calls `event.set()` (the pipeline coroutine reads the rejection file to decide what to do next). `GET /plan` reads `implementation_plan.json` and returns the projection.
5. **`db.py` / `fail_stale_runs_on_startup`:** Include `awaiting_plan_approval` in the set of stale statuses that are failed on startup.
6. **Status endpoint (`/issue/{owner}/{repo}/{number}/status`):** Include `awaiting_plan_approval` as a recognized pipeline status. No schema change needed — status is already a free-form string in the response.

### Frontend (`Pawchestrator.user.js`)

1. Add `PLAN_APPROVAL_ID = "pawchestrator-plan-approval"` constant.
2. In the polling callback: detect `status === 'awaiting_plan_approval'`. If true, fetch `GET /runs/{run_id}/plan` and render the plan approval sub-view inside the panel body.
3. Plan approval sub-view renders: approach summary, risk badge, file operations grouped by type, steps checklist, Approve / Reject / Abort action bar.
4. Reject flow: inline textarea reveal → `POST /runs/{run_id}/reject` → polling resumes → sub-view re-renders on next `awaiting_plan_approval` poll.
5. Approve: `POST /runs/{run_id}/approve` → panel returns to normal stage-progress view.
6. Abort: `POST /runs/{run_id}/abort` → panel transitions to failed state.
7. MutationObserver already handles re-injection — no additional observer needed.

### Prompt engineering

Update the `ImplementationPlan` skill (or fallback string in `plan.py:_PLAN_FALLBACK`) to require `file_operations` in the output JSON:

```
file_operations: array of {path: string, type: "create"|"modify"|"delete", description: string (one line, ≤100 chars)}
```

Instruction: emit one entry per file. Do not repeat descriptions or add narrative. Token budget is tight.

---

## Consequences

- Every pipeline run now pauses at the Plan stage by default. Users who want unattended runs set `plan_approval = false`.
- `implementation_plan.json` gains a `file_operations` field. Existing consumers (`implement.py`, `pr.py`) are unaffected — they read `files_to_modify`, which is still populated.
- `plan_rejections.json` is a new optional artifact. Absent when no rejections occur.
- Four new API endpoints added to the server.
- `asyncio.Event` dict is a new in-process global — must be initialized at app startup and cleaned up when a run terminates (complete, failed, or aborted).
- Future resume capability: `awaiting_plan_approval` + `implementation_plan.json` present = sufficient state to re-prompt user for approval after restart. No code change needed at that layer — the artifact is already there.
