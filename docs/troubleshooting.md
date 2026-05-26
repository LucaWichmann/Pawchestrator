# Troubleshooting

## GitHub issue runs cannot find the repo

Browser-triggered runs rely on `owner/repo → local path` registration. If Pawchestrator says the repo is not registered, run:

```powershell
uv run pawchestrator repo add C:\src\REPO
```

## Pairing does not work

- Make sure the backend is running on `127.0.0.1`.
- Approve the pairing prompt in the terminal after the first `POST /pair`.
- To reset the browser token: `uv run pawchestrator sessions clear`.

## Windows Codex sandbox issues

If native Codex on Windows fails with sandbox setup errors, `os error 740`, or a run that produces no diff:

1. Run Codex once interactively in the repo so the Windows sandbox setup can finish.
2. Set `[windows] sandbox = "unelevated"` in `~/.codex/config.toml` if elevated setup is blocked.
3. Install Codex inside WSL and set `[runners.codex] execution = "wsl"` for Pawchestrator.

```powershell
wsl --exec sh -lc "npm install -g @openai/codex@latest && codex --version"
```

Use `bypass_sandbox = true` only as an intentional last resort for trusted repos.

## Codex `previous_response_not_found`

Usually transient. Rerunning the same prompt or resuming the latest Codex exec session often succeeds.

Pawchestrator handles this inside `CodexRunner`. When Codex exits nonzero and reports both `previous_response_not_found` and `previous_response_id`, it retries with:

```powershell
codex exec resume --last -
```

Default cap is 3 total attempts including the first failing attempt. Configure with:

```toml
[runners.codex]
previous_response_not_found_attempts = 3
```
