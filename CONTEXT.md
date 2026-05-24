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

**RunWarning** — A non-fatal diagnostic event emitted by a Stage during a Run. Stored in the `run_warnings` table (1:n to `workflow_runs`). Surfaced in the GitHub issue comment alongside run state. Distinct from Stage errors: a warning does not fail the Stage; an error does.

**Grill** — A standalone read-only analysis action triggered from the GitHub issue page. Explores the local codebase, infers acceptance criteria from issue context + code, appends a `## Pawchestrator Suggested Criteria` section to the issue body, and posts a comment only if there are questions it cannot answer from codebase context. Does not create a worktree, does not run Codex, does not modify local files. Produces a `GrillReport` artifact. Reuses the run infrastructure with `workflow_type = "grill"` to keep state tracking consistent without coupling to pipeline logic.

**GrillReport** — Artifact produced by the Grill action. Contains `suggested_criteria` (inferred from codebase), `unanswerable_questions` (posted to GitHub if non-empty), `body_updated` (bool), `comment_posted` (bool), `comment_id` (int or null).

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
  → PR          gh pr create             → PR URL + assignment
```

No human gates in MVP 0. No repair loop. No YAML workflow engine. No pairing security token. All hardcoded.

---

## Stage-to-runner mapping (configurable via config.toml)

| Stage    | Default Runner | Notes                                |
|----------|----------------|--------------------------------------|
| Snapshot | GitHub API     | httpx + gh auth token — not configurable |
| Scout    | ClaudeRunner   | Read-only, JSON output               |
| Grill    | ClaudeRunner   | Read-only enforced for Claude; codex has no tool allowlist |
| Plan     | ClaudeRunner   | JSON output                          |
| Implement| CodexRunner    | File edits via patch                 |
| Verify   | ShellRunner    | Runs repo-configured build/test cmds — not configurable |
| PR       | gh CLI         | `gh pr create` (draft configurable) — not configurable |

Agent stages (scout, grill, plan, implement) are configurable via `[stages.X]` in `config.toml`:

```toml
[stages.implement]
runner = "claude"   # override default

[stages.plan]
runner = "codex"    # override default
```

Valid values: `"claude"`, `"codex"`. Validated at config load; unknown values are rejected. Omitting `runner` uses the stage default.

**Runner capability model:** Runner config defines maximum capabilities (ceiling). Stage constraints narrow within that ceiling — e.g. grill forces Claude to read-only tools regardless of runner config. If a stage requires a tool not in the runner's `allowed_tools`, Pawchestrator emits a warning at `doctor` and at pipeline start (non-fatal).

**Codex limitation:** Codex CLI has no tool allowlist equivalent (`--allowedTools`). Stage read-only enforcement applies to ClaudeRunner only. Assigning codex to grill removes the read-only guarantee — documented, not warned at runtime.

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
workflow_runs   (id, owner, repo, issue_number, status, current_stage, workflow_type, created_at, updated_at)
workflow_stages (id, run_id, stage_name, status, started_at, completed_at, error)
artifacts       (id, run_id, artifact_type, file_path, created_at)
run_warnings    (id, run_id, stage_name, code, message, created_at)
```

`workflow_type` distinguishes run kinds: `"pipeline"` (default, snapshot→PR) and `"grill"` (standalone analysis, no worktree). Pipeline code must assert `workflow_type != "grill"` before creating worktrees or invoking Codex.

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
  grill_report.json        ← grill runs only
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

## GitHub comment and label policy

**Active from post-MVP0. On by default for all repos.**

**Comment shape (one editable comment per run):**
- Pawchestrator posts one comment when the run starts and edits it in-place at each stage transition.
- Comment body is a **static template** with factual data only: run ID, branch, current stage, timestamps, PR URL on completion.
- **No LLM-generated text in GitHub comments** — except Grill (see carve-out below). Output tokens are significantly more expensive than input tokens; spending them on comment summaries is wasteful. All non-grill comment content is produced by Pawchestrator from structured state.
- One final edit posts the PR URL when the run completes.
- The comment ID is stored in SQLite (`workflow_runs.github_comment_id`) so subsequent stage transitions can edit it.
- Internal artifacts stay local. GitHub comments show only factual run state.

**Grill comment carve-out:** Grill is the only action that writes LLM-generated text to GitHub. This is intentional — the questions are the product, not a summary. A grill comment is posted only when there are unanswerable questions (questions ClaudeRunner could not resolve from codebase context). Zero unanswerable questions = zero comments. The comment is posted once and never edited. See ADR 0002.

**Label strategy — pipeline runs:**
- Apply `pawchestrator:running` when a run starts, replace with stage label (`pawchestrator:scouting`, `pawchestrator:planning`, etc.) as stages progress.
- On completion: replace with `pawchestrator:pr-ready`. On failure: `pawchestrator:failed`.
- Only one stage label active at a time.
- **Auto-create missing labels** on first use with a default color. Never fail a run because a label is missing.
- Label names are defined in PRD section 9.4.

**Label strategy — grill:**
- `pawchestrator:needs-info` is a readiness state label, not a pipeline stage label. It may coexist with pipeline stage labels.
- Grill applies `pawchestrator:needs-info` when it posts unanswerable questions (issue needs human input before implementation).
- Grill removes `pawchestrator:needs-info` when it finds no unanswerable questions (issue is deemed ready from codebase context).
- If no unanswerable questions and `pawchestrator:needs-info` is not present: no label operation performed.

---

## Branch naming

```
paw/issue-{number}-{slug}
```
Example: `paw/issue-42-handler-memoization`

---

## Token efficiency — first-class principle

Output tokens from LLMs (Claude, Codex) are significantly more expensive than input tokens. Pawchestrator must minimize output token spend:

- **Prompts must instruct agents to be terse.** No verbose narrative in JSON artifacts.
- **Inter-stage context must be compact.** When passing artifacts as input to the next stage, summarize or compress rather than including raw verbose JSON.
- **GitHub comments contain zero LLM-generated text.** All comment content is template-driven from structured state data.
- This principle overrides "richer outputs are better" — useful and cheap beats comprehensive and expensive.

---

## Current development phase (post-MVP0)

MVP0 pipeline is complete and end-to-end verified (snapshot → scout → plan → implement → verify → PR). Post-MVP0 sprint (comments, labels, repo registry, pairing token, terse prompts) also complete.

**Next sprint target: Grill**
- New standalone action triggered from "🔥 Grill Issue" button in userscript
- ClaudeRunner, read-only tools only (`Read`, `Glob`, `Grep`), no worktree, no Codex
- Appends `## Pawchestrator Suggested Criteria` to issue body (idempotent — skips if heading exists)
- Posts comment only if unanswerable questions exist; zero questions = zero comments
- Degrades gracefully if no local repo registered (questions-only mode, no codebase exploration)
- Userscript: second button + separate status div (grill and pipeline statuses don't clobber each other)
- New API endpoint: `POST /issue/grill {owner, repo, number}`
- New DB column: `workflow_type` on `workflow_runs` (`"pipeline"` or `"grill"`)
- New GitHub API method: `patch_issue_body()` on `GitHubIssueClient`

**Deferred:**
- Tauri desktop viewer (MVP1 per PRD)
- Workflow YAML engine
- Human gates UI (push + PR approval)
- Repair loop (max 2 verify retries)
- Skill file loading

**`Path.cwd()` bug:** When `POST /issue/start` is triggered from Tampermonkey, no `repo_path` is provided. `pipeline.py` falls back to `Path.cwd()`, which is the server's working directory — almost certainly wrong. Repo registry fixes this: the pipeline looks up `owner/repo` in the registry to get the correct local path.

---

## Repo registry

`pawchestrator repo add <path>` reads the git remote of `<path>` to determine `owner/repo`, then stores the mapping in SQLite (`github_repos` table: `owner, repo, local_path`).

When a run starts via browser trigger, the pipeline looks up `(owner, repo)` in the registry to find `local_path`. If no match: run fails immediately with a clear error ("Repo not registered — run `pawchestrator repo add <path>` first").

`pawchestrator doctor` reports how many repos are registered.

---

## Pairing token

**Threat model:** CORS (`allow_origins=["https://github.com"]`) + localhost-only bind already blocks cross-origin requests. The pairing token defends against rogue scripts on other GitHub tabs triggering runs. DH key exchange was considered and rejected — it prevents eavesdropping (no threat on localhost) but cannot authenticate "legitimate userscript" vs "any JS on github.com." Simple token + terminal confirmation is sufficient.

**Flow (one-time per browser install):**
1. Userscript calls `POST /pair` on first load (unauthenticated, no token yet).
2. Backend logs `Pairing request from github.com — press Enter to approve (Ctrl+C to deny)` to the terminal.
3. User presses Enter → backend generates a 32-byte random session token, persists it to `~/.pawchestrator/sessions.json`, returns it in the response.
4. Userscript stores token via `GM_setValue("pawchestrator_token", token)`.
5. All subsequent requests include `X-Pawchestrator-Token: {token}`. Backend returns 403 if missing or wrong.
6. `/health` is exempt from token check (used for offline detection).
7. If user presses Ctrl+C / denies: backend returns 403, userscript shows "Pairing denied".

**Re-pair:** `pawchestrator sessions clear` revokes all tokens. Userscript detects 403 and re-initiates pairing.

**No secrets in script source.** Token lives in browser's Tampermonkey storage (GM_getValue/GM_setValue), never in `.user.js`.

---

## PR creation policy

**Draft flag:** configurable via `[pr] draft = false` in `config.toml`. Default is non-draft (ready-for-review).

**Assignment:** configurable via `[pr] assign = true`. Default on. When enabled:
- All assignees from `IssueSnapshot.assignees` are set as PR assignees and review requesters.
- If `assignees` is empty, Pawchestrator queries `GET /repos/{owner}/{repo}/collaborators?permission=admin` to find admin collaborators and uses that list as fallback.
- If the admin collaborators call fails or returns empty, Pawchestrator emits a `RunWarning` (code: `assignment_lookup_failed`) and continues — PR is created unassigned.
- Assignment is applied whether the PR is freshly created or already exists (`gh pr edit --add-assignee`).

---

## Open decisions

- Desktop app framework: decision deferred. Python + userscript is the active mode.
- Skill files format and loading
- GitHub REST API direct PR creation (replacing `gh` dependency)
- Failure repair loop (max 2 attempts)
- SQLite migration strategy
