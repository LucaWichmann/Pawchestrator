# Stage lifecycle ceremony extracted into a single deep module

Every agent stage (scout, plan, grill, review, etc.) repeats the same DB ceremony: `start_{stage}_run` → try → write artifact JSON → `complete_{stage}_run` / except → `fail_{stage}_run`. This pattern is shallow — the interface of each stage module is nearly as complex as its implementation, and the ceremony drowns the domain logic (prompt building, runner dispatch, normalization).

We decided to extract this into a single `run_stage_lifecycle(settings, run_id, stage_name, body)` function in `stage_lifecycle.py`. `body(log_path) -> (artifact_dict, artifact_path)` contains only domain logic; the lifecycle wrapper owns file write, DB start/complete/fail, and `RunnerFailedError` classification. A `STAGE_CONFIGS` table maps each stage name to its `(run_status_running, run_status_complete, run_status_failed, artifact_type)` strings — the only stage-specific data the wrapper needs. All stage modules return a shared `StageResult(run_id, artifact_path, log_path, report)` instead of per-stage typed dataclasses.

This eliminates roughly 70 per-stage DB wrapper symbols from `db.py` and reduces adding a new stage to: one row in `STAGE_CONFIGS` + a `body` closure. The lifecycle has one test covering start/complete/fail instead of that test being repeated per stage.

## Considered options

**Caller passes status strings explicitly** — rejected. Status strings are conventions, not caller choices. Encoding them as four required keyword args at every call site is interface bloat; a future reader would reasonably ask "why does the caller know these strings?"

**Keep per-stage Result dataclasses** — rejected in favour of the shared `StageResult`. The typed dataclasses (`ScoutResult`, `PlanResult`, etc.) were structurally identical; callers accessed `.report.get("readiness")` regardless of type. Dropping them removes one maintenance surface with no loss of information.
