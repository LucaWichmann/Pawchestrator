# ADR 0014: uv tool install as the distribution model

**Status:** Accepted  
**Date:** 2026-05-26

## Context

Pawchestrator agents (Claude Code, Codex) run inside repo worktrees and invoke the `pawchestrator` CLI directly — e.g. `pawchestrator checkbox check ...`. The original install model cloned the repo to `~/.pawchestrator-cli` and ran everything via `uv run pawchestrator`. This never adds `pawchestrator` to PATH, so agent subprocesses cannot find the binary. The LLM then emits a "not found on PATH" message, searches for alternatives, and retries — wasting tokens and breaking tool calls that depend on the CLI being accessible as a plain command.

## Decisions

### End-user install via `uv tool install git+...`

The install scripts (`install.sh`, `install.ps1`) now run:

```
uv tool install git+https://github.com/LucaWichmann/Pawchestrator.git
```

`uv tool` places a shim in `~/.local/bin` (Unix) or `%APPDATA%\uv\bin` (Windows) — both of which uv adds to PATH at uv install time. After install, `pawchestrator` is available as a bare command in any shell and any subprocess, including agent worktrees.

Updates use `uv tool upgrade pawchestrator`, which re-fetches from the git source. This replaces the previous `git pull` workflow.

### Contributor install via `--editable`

Contributors who need a local clone for development use:

```
git clone https://github.com/LucaWichmann/Pawchestrator.git
cd Pawchestrator
uv tool install --editable .
```

Editable install gives the same PATH shim while reflecting local source changes immediately. The install scripts do not handle the contributor path — contributors are assumed to know `uv`.

### Doctor adds optional `pawchestrator` PATH check

`pawchestrator doctor` gains a `check_binary("pawchestrator", required=False)` entry. The check will always pass when invoked via the installed binary, but surfaces a warning if an agent runs doctor in a context where the shim is missing — prompting the user to re-run `uv tool install`.
