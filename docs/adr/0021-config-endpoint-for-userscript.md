# ADR 0021 — /config endpoint for userscript config values

## Status

Accepted (grilled 2026-05-30)

## Context

The Tampermonkey userscript displays two "attempt X of N" counters:

1. **Repair timeline label** — `repair X/Y` where Y was computed from observed workflow stages (`Math.max(repairCount, failedVerifyCount)`). On the first repair this always shows `repair 1/1`, ignoring the configured `verify_repair_attempts` maximum.
2. **Plan approval header** — `Plan attempt X of 3` with the denominator hardcoded to `3`, ignoring the configured `plan_approval_max_attempts`.

Neither counter reads from the daemon. No server endpoint exposed config values. The fix requires the userscript to know `verify_repair_attempts` and `plan_approval_max_attempts` from `PipelineSettings`.

---

## Decisions

### Decision 1 — New GET `/config` endpoint on the daemon

A dedicated `/config` endpoint is added to the FastAPI server. It returns a narrow pipeline-relevant subset of `Settings`:

```json
{
  "pipeline": {
    "verify_repair_attempts": 3,
    "plan_approval_max_attempts": 3
  }
}
```

**Alternatives rejected:**

- **Embed in `/health`** — `/health` is unauthenticated (bypasses token middleware). Exposing user-configurable pipeline settings there feels wrong and sets a bad precedent.
- **Embed in status responses** — Every run response would carry config, bloating the payload and coupling config to run state unnecessarily.
- **Extend status response per-run** — The denominator is a global config value, not per-run state. Embedding it in run responses misrepresents its scope.

---

### Decision 2 — Auth required

`/config` is behind the standard `X-Pawchestrator-Token` middleware, consistent with all non-`/health`/non-`/pair` endpoints.

---

### Decision 3 — Fetched once after pairing, cached in state

The userscript fetches `/config` once immediately after pairing succeeds. The result is stored in `state.ts`. It is not re-fetched on polling cycles.

**Rationale:** Config values do not change while the daemon is running (no hot-reload). Re-fetching every poll cycle is wasteful. If the user restarts the daemon with a new config, the userscript re-pairs and gets fresh values.

---

### Decision 4 — Retry on failure, no hardcoded fallback

If the `/config` fetch fails, the userscript retries. No fallback to hardcoded defaults is implemented.

**Rationale:** No production users exist. A retry loop is simpler than maintaining fallback constants that could silently diverge from the real defaults.

---

### Decision 5 — Strict config value as denominator, no observed-count safety net

The repair timeline denominator becomes strictly `state.config.pipeline.verify_repair_attempts`. The previous `Math.max(repairCount, failedVerifyCount)` formula is removed entirely.

The plan approval header denominator becomes strictly `state.config.pipeline.plan_approval_max_attempts`.

**Alternative rejected:** `Math.max(configValue, observedCount)` as a safety net for config-changed-mid-run. That edge case is not worth coding around — it was a workaround for the missing config value, not an intentional design.

---

## Consequences

- `GET /config` is a new API surface. Future config values exposed to the userscript should be added here, not to other endpoints.
- `src/state.ts` grows a `config` field holding the fetched pipeline settings.
- `src/api.ts` gets a `fetchConfig()` function called once post-pair.
- `src/render/timeline.ts` repair label replaces the `Math.max` formula with `state.config.pipeline.verify_repair_attempts`.
- `src/render/plan-approval.ts` replaces the hardcoded `of 3` with `state.config.pipeline.plan_approval_max_attempts`.
