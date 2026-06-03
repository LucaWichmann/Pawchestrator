# ADR 0025 — SSE for real-time run streaming

## Status

Accepted (grilled 2026-06-04)

## Context

The Tampermonkey panel polls `GET /issue/{owner}/{repo}/{number}/status` every 3 seconds while any run is active. This approach works but has three weaknesses:

1. **3-second lag** on stage transitions — visible delay between stage completing and panel updating.
2. **Constant HTTP overhead** — polling fires throughout long implement/verify runs regardless of whether anything changed.
3. **No live log output** — the panel cannot show runner stdout in real time; the user has no feedback during long-running agent stages.

Server-Sent Events (SSE) allow the daemon to push updates to the panel as they happen, eliminating polling lag and enabling live log streaming.

---

## Decisions

### Decision 1 — New `GET /runs/{run_id}/stream` SSE endpoint

The daemon exposes a Server-Sent Events endpoint. The panel subscribes when a run becomes active. The connection stays open until the run reaches a terminal status.

Event types:

```
event: stage_transition
data: {"stage": "implement", "status": "running", "updated_at": "..."}

event: warning
data: {"stage": "implement", "code": "...", "message": "..."}

event: run_complete
data: {"status": "complete", "pr_url": "..."}

event: run_failed
data: {"status": "failed", "error": "..."}

event: log_line
data: {"stage": "implement", "line": "..."}
```

`log_line` events carry individual stdout lines from the runner as they are emitted. This is the primary UX improvement — users see the agent working in real time rather than waiting for stage completion.

---

### Decision 2 — SSE replaces active polling while run is active

When a run is active, the panel subscribes to `GET /runs/{run_id}/stream` instead of polling. The 3-second `POLL_INTERVAL_MS` poll continues only for:

- **Pre-run state** — before a run exists for the issue (panel still needs `GET /issue/.../status` to show repo registration and runner health).
- **Reconnect fallback** — if the SSE connection drops, the panel falls back to polling until reconnection succeeds.

**Alternative rejected:** SSE as an additive layer alongside polling. Rejected because it creates redundant traffic and split state — the panel would receive both SSE events and polling responses and need to reconcile them.

---

### Decision 3 — In-memory async queue per active run

The daemon maintains an in-memory `asyncio.Queue` per active `run_id`. Stage lifecycle code pushes events to the queue as transitions occur. The SSE endpoint consumes from the queue and streams to the client.

On run completion or terminal status, the daemon pushes a sentinel value to close the stream. Queues are removed from the registry when the SSE connection closes or the run terminates.

**No persistence of SSE events** — events are transient. A panel that connects after a run completes gets no replayed events; it falls back to `GET /issue/.../status` for final state. SSE is for live observation only.

---

### Decision 4 — Authentication

`GET /runs/{run_id}/stream` requires `X-Pawchestrator-Token`, consistent with all non-health/non-pair endpoints.

---

## Consequences

- New FastAPI `StreamingResponse` endpoint with `media_type = "text/event-stream"`.
- Stage lifecycle emits events to the run's async queue at each transition.
- Runner stdout capture is updated to emit `log_line` events per line during streaming (not just after completion).
- `src/api.ts` gains `openRunStream(runId)` returning an `EventSource`.
- Panel polling logic is refactored: poll when no active run, subscribe to SSE when run active, fall back to poll on disconnect.
- `run_id` must be persisted client-side (already via `GM_setValue`) to reconnect after SPA navigation.
