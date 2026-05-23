# Pawchestrator

Local-first GitHub-native agent orchestration platform.

## Development

```powershell
uv sync
uv run pawchestrator doctor
uv run pawchestrator serve
```

The local backend binds to `127.0.0.1:38472` and exposes `GET /health`.
