# CLI Reference

## Pipeline

| Command | Purpose |
|---|---|
| `pawchestrator issue start <url> [--repo-path <path>]` | Run the full pipeline on an issue (snapshot → scout → plan → implement → verify → PR). If the issue has sub-issues, runs the epic workflow. |
| `pawchestrator issue snapshot <url>` | Capture a GitHub issue snapshot only. |

## Per-stage re-runs

| Command | Purpose |
|---|---|
| `pawchestrator run scout <run-id>` | Re-run scouting for an existing run. |
| `pawchestrator run plan <run-id>` | Re-run planning for an existing run. |
| `pawchestrator run implement <run-id>` | Re-run implementation for an existing run. |
| `pawchestrator run verify <run-id>` | Re-run verification for an existing run. |
| `pawchestrator run pr <run-id>` | Create or reuse the draft PR for a verified run. |

## Backend

| Command | Purpose |
|---|---|
| `pawchestrator serve` | Start the local FastAPI backend on `127.0.0.1:38472`. |
| `pawchestrator doctor` | Check the local environment — required and optional dependencies, port, SQLite, repo registry. |

## Repository registry

| Command | Purpose |
|---|---|
| `pawchestrator repo add <path>` | Register a local clone for browser-triggered runs. |
| `pawchestrator repo list` | List registered `owner/repo → local path` mappings. |

## Utilities

| Command | Purpose |
|---|---|
| `pawchestrator checkbox check <owner>/<repo>/<number> <index>` | Check an issue-body checkbox directly on GitHub. Pass `--run-id` from pipeline agents to record a run-scoped mark. |
| `pawchestrator codegraph sync <run-id>` | Copy a run worktree CodeGraph index back to the source repo after its branch has merged into `main`. |
| `pawchestrator sessions clear` | Revoke all stored browser pairing tokens. |
