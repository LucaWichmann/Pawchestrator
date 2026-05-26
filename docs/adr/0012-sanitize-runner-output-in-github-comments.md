# ADR 0012: Sanitize runner failure output in GitHub comments

**Status:** Accepted  
**Date:** 2026-05-26

## Context

When a queued job fails, Pawchestrator posts a status comment to the GitHub issue. The failure path in `stage_fallback.py` builds an error message via `runner_failure_detail()`, which returns the runner's raw `stderr` or `stdout`:

```python
def runner_failure_detail(result: RunnerResult, runner_id: str) -> str:
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or f"{runner_id.capitalize()} runner failed"
    )
```

This string becomes a `RuntimeError`, which each stage catches and stores verbatim in the `workflow_stages.error` DB column via `fail_*_run(..., error=str(error))`. `format_run_comment` then reads that column and includes it in the GitHub issue comment.

Runner stdout can contain API tokens, environment variables, and other secrets — particularly when the runner process crashes mid-operation or when the Claude CLI emits debug output. Posting this verbatim to a public GitHub issue is a security risk.

## Decision

Introduce `RunnerFailedError` with a `public_message` field that is safe to post publicly. Each `fail_*_run()` callsite passes `error.public_message` (for `RunnerFailedError`) or a generic fallback string (for all other exception types) to the DB — never raw exception text.

### `RunnerFailedError` shape

```python
@dataclass
class RunnerFailedError(Exception):
    public_message: str  # safe for GitHub
    exit_code: int
    stderr: str
    stdout: str
```

### Public message format

- Single runner failure: `"Runner exited with code {exit_code}"`
- Dual failure (Claude + Codex fallback both fail): `"Claude exited with code {N}; Codex fallback exited with code {M}"`

### Callsite pattern (all stage files)

```python
except Exception as error:
    if isinstance(error, RunnerFailedError):
        db_error = error.public_message
    else:
        db_error = "Stage failed. See local run logs."
    await fail_*_run(..., error=db_error)
    raise  # full exception with stderr/stdout propagates locally
```

## Reasons

- **Stdout can contain secrets.** Claude CLI and Codex may emit tokens, environment values, or credential-bearing URLs before crashing. These must not appear in public GitHub comments.
- **Non-runner exceptions are equally risky.** Git, network, and DB errors can contain repository URLs with embedded credentials or internal hostnames. All non-runner exceptions are also sanitized to a generic message.
- **Local logs already capture full output.** `write_attempt_log` writes the full `RunnerResult` to disk before any exception is raised. Exit code is sufficient signal in the comment; full detail is available locally.
- **Exit code is genuinely diagnostic.** Code 1 = logical failure, 137 = OOM, 124 = timeout. Worth preserving in the public message.

## Trade-offs

**What we lose:** Live failure detail in the GitHub comment. Developers must check local run logs to see the actual error output.

**What we gain:** No risk of secrets, tokens, or internal URLs leaking into public GitHub issue comments. Consistent with ADR 0001 (template-only comments, no sensitive artifact content in public comments).

## Consequences

- `RunnerFailedError` lives in `runners.py` alongside `RunnerResult`.
- `run_task_with_usage_limit_fallback` in `stage_fallback.py` raises `RunnerFailedError` instead of `RuntimeError`.
- All six stage files (`scout.py`, `plan.py`, `implement.py`, `verify.py`, `pr.py`, `grill.py`) update their `fail_*_run` callsites to use `public_message` or the generic fallback.
- The `runner_failure_detail` helper is removed; its logic moves into `RunnerFailedError` construction in `stage_fallback.py`.
