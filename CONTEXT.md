# Pawchestrator — Domain Context

Local-first GitHub-native agent orchestration platform. Augments GitHub issue pages with workflow controls. Coordinates local agents (Claude Code, Codex) through subscription-backed tooling to run issue-to-PR pipelines on the developer's machine.

---

## Core terms

**Run** — One execution instance of a workflow against a GitHub issue. Has a unique ID. Produces artifacts and a draft PR.

**Stage** — A discrete unit of work within a Run. Stages are sequential in MVP 0. Each stage has a status, runner, input artifacts, and output artifacts.

**Runner** — An adapter that invokes an external agent or tool and captures its output. Runners are isolated from each other and expose a common interface.

**Artifact** — A typed JSON or text file produced by a Stage. Artifacts are stored on disk and passed as input to subsequent stages. Agents hand off artifacts, not prose.

**Worktree** — An isolated `git worktree` created per issue. One issue = one branch = one worktree. Located at `~/.pawchestrator/worktrees/{owner}/{repo}/issue-{number}/`.

**Workflow** — A declarative stage graph. Hardcoded in MVP 0; YAML-defined in MVP 1.

**Gate** — A human approval checkpoint before a dangerous action (push, PR creation). Auto-approved in MVP 0; explicit in MVP 1.

**Skill** — A reusable instruction pack for a specific agent task (Scout, Plan, Implement, etc.). Inline strings in MVP 0; loaded from files in MVP 1.

**Doctor** — CLI command that checks all required and optional dependencies and reports their status.

---

## MVP 0 pipeline (hardcoded, no YAML engine)

```
Tampermonkey button click on GitHub issue
  → POST /issue/start {owner, repo, number}
  → Snapshot    GitHub API              → IssueSnapshot artifact
  → Scout       ClaudeRunner            → ScoutReport artifact
  → Plan        ClaudeRunner            → ImplementationPlan artifact
  → Implement   CodexRunner             → ImplementationReport + file edits
  → Verify      ShellRunner             → VerificationReport artifact
  → PR          gh pr create (draft)    → PR URL
```

No human gates in MVP 0. No repair loop. No YAML workflow engine. No pairing security token. All hardcoded.

---

## Stage-to-runner mapping (MVP 0, hardcoded)

| Stage    | Runner         | Notes                                |
|----------|----------------|--------------------------------------|
| Snapshot | GitHub API     | httpx + gh auth token                |
| Scout    | ClaudeRunner   | Read-only, JSON output               |
| Plan     | ClaudeRunner   | JSON output                          |
| Implement| CodexRunner    | File edits via patch                 |
| Verify   | ShellRunner    | Runs repo-configured build/test cmds |
| PR       | gh CLI         | `gh pr create --draft`               |

Configurable per-stage via workflow YAML in MVP 1.

---

## Tech stack

| Layer        | Choice                              |
|--------------|-------------------------------------|
| Backend      | Python 3.12+ / FastAPI / uvicorn    |
| Database     | SQLite via aiosqlite                |
| Install      | `uvx pawchestrator`                 |
| GitHub auth  | `gh auth token` (reuse gh CLI)      |
| GitHub API   | httpx (raw REST)                    |
| PR creation  | `gh pr create`                      |
| Desktop      | TBD — PyWebView or Electron+sidecar (MVP 1) |
| Frontend MVP0| Tampermonkey userscript             |

---

## Runner commands (proven via spike)

### ClaudeRunner
```bash
claude -p "{prompt}" \
  --allowedTools "Edit,Write,Read,Bash,Glob,Grep" \
  --output-format json \        # Scout/Plan stages only
  --dangerously-skip-permissions
```
- Invoked from worktree directory (`cwd=worktree_path`)
- Inherits MCP config from `~/.claude/` automatically
- `--output-format json` for Scout/Plan; omit for Implement if needed
- Exits cleanly, stdout is structured

### CodexRunner
```bash
codex exec "{prompt}" \
  -C {worktree_path} \
  -s workspace-write
# If shell execution needed inside Codex:
  --dangerously-bypass-approvals-and-sandbox
```
- `-s workspace-write` works for file edits on Windows
- Shell commands within Codex sandbox fail on Windows (`CreateProcessWithLogonW failed: 1326`) — not blocking since verify runs via ShellRunner
- Inherits MCP config from `~/.codex/` automatically
- `--json` flag emits JSONL events (useful for streaming)
- `-o <file>` writes last agent message to file

### ShellRunner
Pawchestrator spawns subprocess directly. Uses repo-configured commands. No LLM involved.

---

## MCP server inheritance

Both Claude and Codex inherit their MCP configs from global user config when invoked:
- Claude: `~/.claude/settings.json`
- Codex: `~/.codex/config.toml`

Pawchestrator does NOT inject MCP config per invocation. Doctor checks both configs and warns if MCP servers are unreachable (non-blocking).

---

## Persistence

**SQLite** from day 1 (not deferred). Minimum MVP 0 schema:

```sql
workflow_runs   (id, owner, repo, issue_number, status, current_stage, created_at, updated_at)
workflow_stages (id, run_id, stage_name, status, started_at, completed_at, error)
artifacts       (id, run_id, artifact_type, file_path, created_at)
```

**Artifact files on disk:**
```
~/.pawchestrator/runs/{run_id}/
  state.json
  issue.snapshot.json
  scout_report.json
  implementation_plan.json
  implementation_report.json
  verification_report.json
  pr_draft.json
```

---

## Worktree and branch conventions

- Location: `~/.pawchestrator/worktrees/{owner}/{repo}/issue-{number}/`
- Branch: `paw/issue-{number}-{slug}`
- One issue = one worktree = one branch. No sharing.

---

## Verification

Requires explicit repo config. Missing config = verify stage skipped with warning in run log.

```toml
[commands]
build = "cmake --build build"
test  = "ctest --test-dir build"
lint  = ""
```

Verify runs build, then test. Lint/format if configured. ShellRunner captures exit codes and stdout/stderr into VerificationReport.

---

## GitHub auth

MVP 0: reuse `gh auth token`. No device flow implementation.
MVP 1: GitHub OAuth device flow, PAT support.

---

## GitHub comment policy

- One start comment per run (optional in MVP 0).
- One editable status comment updated at each stage.
- One final summary comment with PR link.
- Internal artifacts stay local. Public comments get summaries only.

---

## Branch naming

```
paw/issue-{number}-{slug}
```
Example: `paw/issue-42-handler-memoization`

---

## Non-goals for MVP 0

- No workflow YAML engine
- No Tampermonkey pairing/security token
- No human gates (auto-approve everything)
- No repair loop
- No desktop app (Tampermonkey only)
- No Codex API key / billing mode
- No multi-repo workflows
- No parallel stages
- No skill file loading system
- No org/team features

---

## Open decisions (deferred to MVP 1)

- Desktop app framework: PyWebView vs Electron+Python sidecar
- Workflow YAML engine design
- Tampermonkey pairing token and session security
- Human gate UI
- Configurable stage-to-runner mapping via workflow YAML
- Skill files format and loading
- GitHub REST API direct PR creation (replacing `gh` dependency)
- Failure repair loop (max 2 attempts)
- SQLite migration strategy
