# Pawchestrator

Local-first GitHub-native agent orchestration platform.

## Development

```powershell
uv sync
uv run pawchestrator doctor
uv run pawchestrator serve
```

The local backend binds to `127.0.0.1:38472` and exposes `GET /health`.

## Tampermonkey userscript

1. Start the local backend:

   ```powershell
   uv run pawchestrator serve
   ```

2. Install `Pawchestrator.user.js` in Tampermonkey by opening or dragging the file into the browser.
3. Open a GitHub issue page and click `Work on this issue` in the issue sidebar.

The userscript calls `POST /issue/start` on the local backend and polls `GET /runs/{run_id}` every 3 seconds to render stage progress inline on GitHub.
