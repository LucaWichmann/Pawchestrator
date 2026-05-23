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
first and may retry through Ubuntu WSL for known Windows sandbox failures, including
cases where native Codex reports sandbox setup failures without producing a diff. It
never turns on dangerous bypass automatically. To bypass permissions completely, set
`bypass_permissions = true` for Claude or `bypass_sandbox = true` for Codex explicitly.
Per-stage permission overrides can be set under `[stages.<stage>.claude]` and
`[stages.<stage>.codex]`.
When `[app] debug = true`, Pawchestrator prints runner argv plus captured stdout/stderr
to the console. Prompts are redacted to their character count so issue content does not
flood the terminal.

### Windows Codex sandbox notes

Native Codex on Windows can fail in background or noninteractive Pawchestrator runs
when Codex is configured for the elevated Windows sandbox and setup needs administrator
approval. Symptoms include `windows sandbox: spawn setup refresh` or `os error 740` in
the run log, followed by no changed files.

Prefer one of these manual fixes:

1. Run Codex once interactively in the repo so its Windows sandbox setup can complete.
2. In `~/.codex/config.toml`, set `[windows] sandbox = "unelevated"` if elevated
   sandbox setup is blocked on your machine.
3. Install Codex inside the Linux WSL distro and set Pawchestrator
   `[runners.codex] execution = "wsl"`.

   ```powershell
   wsl --exec sh -lc "npm install -g @openai/codex@latest && codex --version"
   ```

WSL fallback requires a Linux Codex install. A Windows Codex npm shim visible under
`/mnt/c/...` is not enough and can fail with missing Linux package errors.

Use `[runners.codex] bypass_sandbox = true` only as an intentional last resort for
trusted repos. Pawchestrator will not enable it for you.

## Tampermonkey userscript

1. Start the local backend:

   ```powershell
   uv run pawchestrator serve
   ```

2. Install `Pawchestrator.user.js` in Tampermonkey by opening or dragging the file into the browser.
3. Open a GitHub issue page and click `Work on this issue` in the issue sidebar.

The userscript calls `POST /issue/start` on the local backend and polls `GET /runs/{run_id}` every 3 seconds to render stage progress inline on GitHub.
