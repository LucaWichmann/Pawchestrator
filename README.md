# Pawchestrator

Local-first GitHub-native agent orchestration platform.

## Development

```powershell
uv sync
uv run pawchestrator doctor
uv run pawchestrator serve
```

The local backend binds to `127.0.0.1:38472` and exposes `GET /health`.

## Runner configuration

Pawchestrator reads optional runner defaults from `~/.pawchestrator/config.toml`.
These defaults keep token use low while still letting you raise effort when needed:

```toml
[app]
debug = true

[runners.claude]
execution = "native"
model = "sonnet"
effort = "low"
allowed_tools = ["Read", "Glob", "Grep"]
bypass_permissions = false

[runners.codex]
execution = "auto"
model = "gpt-5.5"
reasoning_effort = "low"
sandbox = "workspace-write"
approval_policy = "never"
bypass_sandbox = false
```

Set Claude `effort = "medium"` when scout or plan stages need deeper thinking.
Scout and plan default to read-only Claude tools. Codex implementation defaults to
workspace-write sandboxing. On Windows, Codex `execution = "auto"` tries native
first and may retry through Ubuntu WSL for known Windows sandbox failures. It never
turns on dangerous bypass automatically. To bypass permissions completely, set
`bypass_permissions = true` for Claude or `bypass_sandbox = true` for Codex explicitly.
Per-stage permission overrides can be set under `[stages.<stage>.claude]` and
`[stages.<stage>.codex]`.
When `[app] debug = true`, Pawchestrator prints runner argv plus captured stdout/stderr
to the console. Prompts are redacted to their character count so issue content does not
flood the terminal.

## Tampermonkey userscript

1. Start the local backend:

   ```powershell
   uv run pawchestrator serve
   ```

2. Install `Pawchestrator.user.js` in Tampermonkey by opening or dragging the file into the browser.
3. Open a GitHub issue page and click `Work on this issue` in the issue sidebar.

The userscript calls `POST /issue/start` on the local backend and polls `GET /runs/{run_id}` every 3 seconds to render stage progress inline on GitHub.
