# Pawchestrator — Domain Context

Local agent orchestration triggered from GitHub issues, right from inside your browser. Augments GitHub issue pages with workflow controls. Coordinates local agents (Claude Code, Codex) through a pipeline that runs on the developer's machine and surfaces results back to GitHub.

---

## Core terms

**Run** — One execution instance of a workflow against a GitHub issue. Has a unique ID. Produces artifacts and a draft PR.

**Stage** — A discrete unit of work within a Run. Stages are sequential in MVP 0. Each stage has a status, runner, input artifacts, and output artifacts.

**Runner** — An adapter that invokes an external agent or tool and captures its output. Runners are isolated from each other and expose a common interface.

**UsageLimitFallback** — An optional per-stage policy that reruns a failed Stage with another configured Runner only when the primary Runner failed because its usage/session limit is exhausted. It preserves the Stage boundary and artifact contract: the fallback Runner receives the same Stage prompt and must produce the same Stage artifact type.

**Artifact** — A typed JSON or text file produced by a Stage. Artifacts are stored on disk and passed as input to subsequent stages. Agents hand off artifacts, not prose.

**Worktree** — An isolated `git worktree` created per issue. One issue = one branch = one worktree. Located at `~/.pawchestrator/worktrees/{owner}/{repo}/issue-{number}/`.

**Workflow** — A declarative stage graph. Hardcoded in MVP 0; YAML-defined in MVP 1.

**Gate** — A human approval checkpoint before a dangerous action (push, PR creation). Auto-approved in MVP 0; explicit in MVP 1.

**Skill** — A reusable instruction pack for a specific agent task (Scout, Plan, Implement, etc.). Loaded from bundled `Skill.md` files under `pawchestrator/skills/`; user overrides from `<app_dir>/skills/` take precedence. Each skill file contains the full prompt including terseness instructions — there is no separate runner-level system prompt for terseness.

**Doctor** — CLI command that checks all required and optional dependencies and reports their status. Includes an optional check that `pawchestrator` itself is on PATH — relevant for agents running inside worktrees that invoke the CLI directly. See ADR 0014.

**RunWarning** — A non-fatal diagnostic event emitted by a Stage during a Run. Stored in the `run_warnings` table (1:n to `workflow_runs`). Surfaced in the GitHub issue comment alongside run state. Distinct from Stage errors: a warning does not fail the Stage; an error does.

Usage-limit fallback emits a `RunWarning` before invoking the fallback Runner so browser overlays and GitHub comments can show that Pawchestrator is continuing with another agent while the Stage is still running.

**Grill** — A standalone read-only analysis action triggered from the GitHub issue page. Explores the local codebase, infers acceptance criteria from issue context + code, appends a `## Pawchestrator Suggested Criteria` section to the issue body, and posts a questions comment if there are questions it cannot answer from codebase context. Does not create a worktree and does not modify local files. Produces a `GrillReport` artifact. Reuses the run infrastructure with `workflow_type = "grill"` to keep state tracking consistent without coupling to pipeline logic.

Grill is multi-round: if questions are posted, the run transitions to `grill_waiting` and pauses. The user replies to the questions comment on GitHub; the Tampermonkey panel detects the reply (via DOM `MutationObserver` on `#issuecomment-{comment_id}`) and re-triggers `POST /issue/grill`. The endpoint auto-detects the `grill_waiting` run and resumes it: fetches reply comments (filtered by `in_reply_to_id`), re-runs the grill agent with previous questions + reply bodies as context, posts a new questions comment if unresolved questions remain (loop), or transitions to `grill_complete` if satisfied. One `GrillReport` artifact per run, overwritten each round with the latest state. See ADR 0008.

**GrillReport** — Artifact produced by the Grill action. Contains `suggested_criteria` (inferred from codebase), `unanswerable_questions` (posted to GitHub if non-empty), `body_updated` (bool), `comment_posted` (bool), `comment_id` (int or null — always the most recent questions comment for this run).

**CriteriaDedupe** — Utility stage used by Grill before it updates the GitHub issue body. It compares existing acceptance criteria and newly inferred `suggested_criteria`, then returns only suggestions that are genuinely new. Its output is used for criteria publishing only; it does not alter the `GrillReport` schema or artifact shape.

**SemanticCriteriaDedupe** — The LLM-backed CriteriaDedupe behavior. In Grill terminology, it treats paraphrases and same-requirement restatements as duplicates even when the markdown text differs. If the configured utility LLM is unavailable, fails, or returns invalid JSON, Grill falls back to deterministic normalized dedupe, which removes exact normalized duplicates but does not reason about paraphrases.

**CheckboxCriterion** — A single `- [ ]` item parsed from the issue body that falls under a configured acceptance-criteria heading. Identified by a scoped integer index (0-based, counting only in-scope checkboxes, ignoring all others). Stored in `IssueSnapshot.checkboxes` as `{index, text}`. During pipeline runs, implement/verify agents express run-scoped intent by calling `pawchestrator checkbox check <owner>/<repo>/<number> <index> --run-id <run-id>` via Bash; Pawchestrator stores the index plus snapshot text in `checkbox_marks` and later reconciles those intents to the latest GitHub issue body. Reconciliation only checks a stored mark when the current checkbox at that scoped index still has the same text. If the issue body changed and the text no longer matches, Pawchestrator skips that stale mark and emits a `RunWarning` instead of checking the wrong item. Manual checks without `--run-id` remain supported: Pawchestrator fetches the latest issue body and PATCHes the updated Markdown directly. If the agent never calls the tool, checkboxes remain unchecked — this is intentional and honest (unchecked = agent did not confirm).

**CheckboxHeadings** — The configured list of markdown heading texts under which `- [ ]` items are treated as CheckboxCriteria. Case-insensitive. Defaults: `Acceptance Criteria`, `AC`, `Definition of Done`, `DoD`, `Checklist`, `Requirements`, `Tasks`. Configurable via `[checkboxes] headings = [...]` in `config.toml`.

**Epic** — Any GitHub issue that has sub-issues (detected via `GET /repos/{owner}/{repo}/issues/{number}/sub_issues`). No GitHub Projects or issue-type classification required. Epics are never run through the pipeline directly — all work is in their sub-issues. Pawchestrator detects epics pre-pipeline and fans out to an EpicRun.

**EpicRun** — An orchestrated sequence of pipeline Runs, one per sub-issue of an Epic, executed sequentially. Identified by a `group_id` (the epic's own run ID). All child Runs share this `group_id` in `workflow_runs`. Stops on first child failure by default (`epic_fail_fast = false` to continue). No resume across separate EpicRuns — re-triggering creates a new EpicRun.

**SubIssue** — A GitHub issue linked as a direct child of another issue via GitHub's sub-issues feature. Pawchestrator resolves one level of sub-issues only; sub-issues of sub-issues are not expanded.

---

## MVP 0 pipeline (hardcoded, no YAML engine)

```
Tampermonkey button click on GitHub issue
  → POST /issue/start {owner, repo, number}
  → Snapshot    GitHub API              → IssueSnapshot artifact (includes CheckboxCriteria)
  → Scout       ClaudeRunner            → ScoutReport artifact
  → Plan        ClaudeRunner            → ImplementationPlan artifact
  → Implement   CodexRunner             → ImplementationReport + file edits
                                           (agent calls `pawchestrator checkbox check` per criterion)
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
| CriteriaDedupe | ClaudeRunner | Grill utility stage; defaults to Claude Haiku, or Codex GPT-5.4-Mini low reasoning when assigned to codex |
| ReviewIssueFormat | ClaudeRunner | Review issues utility stage; defaults to Claude Haiku (no reasoning), or Codex GPT-5.4-Mini low reasoning. Non-agentic: returns structured JSON snippets only, no tool calls. See ADR 0015. |
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

Usage-limit fallback is configured per stage with `usage_limit_fallback_runner`. Unset means the stage-aware default: known Claude-backed stages with known permission intent (`scout`, `plan`, `grill`, `criteria_dedupe`) fall back to Codex when Claude reports usage/session exhaustion. `"codex"` makes that fallback explicit. `"none"` disables fallback for that Stage. Fallback only applies when the primary runner for that Stage is Claude; if the Stage primary runner is already Codex, the default fallback is inert. Future Claude stages should opt in explicitly once their permission intent is defined. Fallback does not change the Stage artifact contract: Scout must still emit `ScoutReport`, Plan must still emit `ImplementationPlan`, Grill must still emit `GrillReport`, and CriteriaDedupe must still emit dedupe output, regardless of runner. If Implement is explicitly configured with Claude primary and Codex usage-limit fallback, Codex uses the normal write-capable implement permissions because Implement is write-capable by intent.

Fallback attempts remain within the same `workflow_stages` row as the original Stage attempt. Attempt history is represented by the Stage log and the emitted `RunWarning`, not by extra Stage rows.

Usage-limit detection belongs to the runner layer because Claude CLI error shape is runner-specific. Stage orchestration asks the runner layer whether a `RunnerResult` represents usage exhaustion, then decides whether the configured Stage fallback applies. Runner health checks still run first to catch unavailable binaries or broken environments. The primary Claude stage attempt is the source of truth for usage exhaustion: Pawchestrator should try running the Stage and inspect Claude's output after health succeeds.

If fallback also fails, the Stage error leads with the fallback failure and includes the original Claude usage-limit message for context.

Claude-backed stages share usage-limit fallback orchestration through a dedicated stage fallback helper module, separate from runner implementations. Runner code classifies runner-specific exhaustion output; the helper owns fallback runner resolution, warning emission, conservative permission mapping, and attempt-log composition.

**Runner capability model:** Runner config defines maximum capabilities (ceiling). Stage constraints narrow within that ceiling — e.g. grill forces Claude to read-only tools regardless of runner config. If a stage requires a tool not in the runner's `allowed_tools`, Pawchestrator emits a warning at `doctor` and at pipeline start (non-fatal).

**Codex limitation:** Codex CLI has no tool allowlist equivalent (`--allowedTools`). Stage read-only enforcement applies to ClaudeRunner tool allowlists and to Codex sandbox selection where possible. Assigning codex as a primary runner to a read-only stage cannot mirror Claude's per-tool allowlist exactly. Usage-limit fallback must still preserve the stage's permission intent: when Claude was constrained to read-only tools for Scout, Plan, Grill, or CriteriaDedupe, Codex fallback runs with a read-only sandbox for that same Stage. This fallback-specific permission mapping is conservative and is not widened by generic `[stages.X.codex]` primary-runner overrides.

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

`workflow_type` distinguishes run kinds: `"pipeline"` (default, snapshot→PR) and `"grill"` (standalone analysis, no worktree). Pipeline code must assert `workflow_type != "grill"` before creating worktrees or running pipeline-only stages.

Grill run statuses: `pending` → `grill_running` → `grill_waiting` (questions posted, paused for reply) → `grill_running` (resumed) → `grill_complete` | `grill_failed`. `grill_waiting` is excluded from `fail_stale_runs_on_startup` — it survives server restarts. Terminal statuses: `grill_complete`, `grill_failed` (plus pipeline terminals).

`IssueSnapshot.comments` includes `in_reply_to_id: int | null` per comment entry (populated from GitHub API). Used by re-grill context builder to filter reply comments to the questions comment.

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

**Non-code skip:** After implement, `run_pipeline` diffs the worktree branch against the base branch (`git diff --name-only <base>...HEAD`). If every changed file matches a configured non-code glob pattern, verification is skipped — a stub `VerificationReport` with `status = "skipped"` and `skip_reason` (listing the changed files) is written so the PR stage can proceed unchanged. If the diff command fails for any reason, verification runs anyway (fail-safe). Controlled by two `[pipeline]` config keys:

- `verify_non_code_changes` (bool, default `false`) — set `true` to run verification even when only non-code files changed
- `non_code_patterns` (list of globs, default `["*.md", "*.txt", "docs/**", "adr/**"]`) — files matching any pattern are considered non-code

When skipped, `workflow_stages` records a `skipped` status row for the verify stage (not absent, not `complete` — auditable).

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

**Grill comment carve-out:** Grill is the only action that writes LLM-generated text to GitHub comments. This is intentional — the questions are the product, not a summary. A grill comment is posted only when there are unanswerable questions. Zero unanswerable questions = zero comments. One new comment is posted per round (never edited); `comment_id` in the run always points to the most recent questions comment. See ADR 0002, ADR 0008.

**Review issue carve-out:** The `issues` stage creates GitHub issues whose title and body are partially derived from a small-model (`review_issue_format`) call. The model emits structured snippets only (`title`, `problem`, `acceptance_criteria` items); Python assembles the final issue body. The model does not write prose directly to GitHub. Small non-reasoning models (Haiku / GPT-5.4-mini) are used to minimize output token cost. See ADR 0015.

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

**Grill sprint: complete.**

**Next sprint target: Pawchestrator Panel polish + README refresh**

Design decisions (locked via grilling sessions 2026-05-24 and 2026-05-25):

**Panel placement:** Injected below the issue body — after `IssueBody-module__outerContainer__ULNTb` — as a sibling element. Uses GitHub CSS variables and `prc-Button-*` class conventions for native look. `margin-left` is computed at inject time as `innerBox.getBoundingClientRect().left - outerContainer.getBoundingClientRect().left` where `innerBox = [data-testid="issue-body"]`. This aligns the panel with the bordered comment box, not the avatar column edge.

**Always-visible readiness:** Panel renders on every issue page, not just after a run starts. Shows backend connection, repo registration, runner health even before any run exists for the issue.

**Smart expand:** Collapsed (single status bar) when no run exists for this issue. Auto-expands when a run is active or completed. User can manually collapse; preference not persisted.

**Stage timeline:** Horizontal steps (snapshot → scout → plan → implement → verify → pr) with status icons. Repair loop iterations shown as `implement (repair 1/2)`. Collapsible warnings section below timeline for `run_warnings`.

**Two independent sections:** Pipeline section and Grill section rendered separately in the expanded panel. Each tracks its own latest run.

**Buttons move into panel (revised 2026-05-25):** "🐾 Work on this issue" and "🔥 Grill Issue" move from the GitHub issue header into the panel bar (always-visible strip), unifying all Pawchestrator controls into one surface. `injectHeaderActions` is retired. Inline status text divs (`STATUS_ID`, `GRILL_STATUS_ID`) were already retired — all status is in the panel.

**New backend endpoint:** `GET /issue/{owner}/{repo}/{number}/status` — combined payload:
```json
{
  "repo_registered": true,
  "runners": {
    "claude": {"available": true, "version": "1.x.x"},
    "codex": {"available": false, "version": null}
  },
  "pipeline": {
    "run_id": "...", "status": "...", "current_stage": "...",
    "stages": [...], "warnings": [...], "pr_url": "...",
    "created_at": "...", "updated_at": "..."
  },
  "grill": {
    "run_id": "...", "status": "...", "grill_report": {...},
    "created_at": "...", "updated_at": "..."
  }
}
```
`pipeline` and `grill` are null when no run exists for that type. Endpoint is token-authenticated.

**Runner health cache:** 60-second in-memory TTL. Spawns `claude --version` / `codex --version` at most once per minute. Never blocks a panel load.

**Warnings inline:** `run_warnings` rows included in the status endpoint response under each run object. Not a separate fetch.

**README hero (locked 2026-05-25):** caveman-style hero at top — centered emoji + H1 + tagline + badges + nav links + `---` — then install section immediately below. No "At a glance" table before install.
- Hero tagline: *"GitHub issue in. Local agents run. Code comes out."*
- GitHub repo description: *"Local agent orchestration triggered from GitHub issues, right from inside your browser."*

**Click-to-install userscript (locked 2026-05-25):** `@downloadURL` and `@updateURL` added to userscript header pointing to raw GitHub URL. Install badge in README. Prerequisites section links to Tampermonkey for Chrome (Chrome Web Store) and Firefox (Firefox Add-ons).

**Panel visual polish (locked 2026-05-25):** Left accent border on panel, color-coded by run status (idle=gray, running=blue, done=green, failed=red). Brand label ("🐾 Pawchestrator") separated from dynamic status text in panel bar.

**Deferred:**
- Tauri desktop viewer (MVP1 per PRD)
- Workflow YAML engine
- Human gates UI (push + PR approval)
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

---

## PR Review feature (design locked 2026-05-26)

### New workflow types

**ReviewRun** — A run triggered from a GitHub PR page that conducts AI code review. `workflow_type = "review"`. Three stages: `review` (agent analyzes PR), `post` (Pawchestrator submits review to GitHub API — auto), `issues` (creates SuggestedIssues on GitHub — user-triggered, stays pending until clicked or skipped). Identified by `{owner, repo, pr_number}` rather than `issue_number`.

**RepairRun** — A run triggered from a GitHub PR page when the PR has `changes_requested` review state. Reads outstanding review comments + PR diff, dispatches the original implementer agent to fix them on the PR branch, pushes the branch, and re-requests review. `workflow_type = "repair"`. Two stages: `repair` (agent commits fixes locally), `push` (Pawchestrator pushes branch + re-requests review via GitHub API). Mirrors implement→pr pattern.

### Cross-review pattern

**CrossReview** — Config-driven behavior where the review runner is the opposite of the implement runner for Pawchestrator-originated PRs. Codex implements → Claude reviews. Claude implements → Codex reviews. Cross-review only applies when both runners are healthy. Falls back to `default_runner` silently if cross-review is not possible (only one runner available or healthy). Doctor warns (non-blocking) when `cross_review = true` but only one runner is healthy.

**ReviewVerdict** — The agent-determined outcome of a ReviewRun. One of: `REQUEST_CHANGES` (blocking issues: bugs, security risks, wrong behavior), `APPROVE` (no blocking issues — minor items may become SuggestedIssues), `COMMENT` (observations without a formal decision). Agent picks verdict. Auto-approving is a valid outcome; constraining to always REQUEST_CHANGES loses signal.

**SuggestedIssues** — Minor, non-blocking items surfaced during an APPROVE review that are worth tracking but don't block merge. Stored internally in the review artifact as `{hint, file, line}` triples, where `file`+`line` reference an existing `inline_comment`. Human clicks "Create Issues" in the PR panel to trigger the `issues` stage — not auto-created. The stage runs one non-agentic `review_issue_format` small-model call per item (parallel), then creates each GitHub issue sequentially from the returned structured snippets. See ADR 0015.

### Review agent inputs

**ReviewAgent input:** PR diff (`gh pr diff {number}`) + PR description (`gh pr view {number}`). PR description always available and contains plan + verification summary for paw-PRs automatically. No extra lookups needed.

**RepairAgent input:** Review comments (both inline file/line comments and top-level PR comments, fetched from GitHub API) + PR diff. Full comment context ensures agent addresses both specific and architectural feedback.

### Review agent output artifact schema

```json
{
  "inline_comments": [{"file": "...", "line": 42, "body": "..."}],
  "summary": "Overall review vibe — e.g. 'smaller changes requested, logic is sound'",
  "verdict": "REQUEST_CHANGES | APPROVE | COMMENT",
  "suggested_issues": [{"hint": "...", "file": "...", "line": 42}]
}
```

`suggested_issues` only populated when `verdict = "APPROVE"`. Each entry's `file`+`line` must reference an existing `inline_comment`; `normalize_review_artifact` validates this and rejects orphaned entries. Inline comments use file line numbers copied from the prompt's Commentable added lines section; Pawchestrator validates those lines against the PR diff and submits GitHub review comments with `line` + `side = RIGHT`. GitHub review posted as one `POST /pulls/{number}/reviews` call with inline comments + summary body + event field.

### Runner assignment

| Run type | Runner selection |
|---|---|
| ReviewRun — paw PR | Opposite of `implement` stage runner (CrossReview). Falls back to `default_runner`. |
| ReviewRun — human PR | `[review] default_runner` from config |
| RepairRun — paw PR | Same runner that ran `implement` stage |
| RepairRun — human PR | `[review] default_runner` from config |

### Worktree for repair

Fresh `git worktree add` per RepairRun from remote PR branch. Worktree path derived from new repair `run_id`, not original run. No stale-worktree risk. Pawchestrator fetches PR head branch name via GitHub API before checkout.

### New config section

```toml
[review]
default_runner = "claude"   # used for non-paw PRs and when cross_review not possible
cross_review = true         # flip runner for paw PRs; doctor warns if only one runner healthy
```

### New API endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/runs/review/start` | Start ReviewRun. Body: `{owner, repo, pr_number}`. Returns `{run_id}`. |
| `POST` | `/runs/repair/start` | Start RepairRun. Body: `{owner, repo, pr_number}`. Returns `{run_id}`. |
| `GET` | `/prs/{owner}/{repo}/{number}/review-state` | Returns `{state: "changes_requested" \| "approved" \| "open"}`. Called on PR page load for button visibility. |
| `POST` | `/runs/{run_id}/create-issues` | Triggers `issues` stage for a completed ReviewRun (user-confirmed). |
| `GET` | `/runs/{run_id}/status` | Reuse existing endpoint — works for review and repair runs without changes. |

### PR panel behavior (Tampermonkey)

- Panel injected above the Conversation tab content area on PR pages (`/pull/\d+` URL pattern).
- Same panel style as issue panel (left accent border, status colors, collapse/expand).
- Polls status every 3s while any run is active — same `POLL_INTERVAL_MS` constant.
- One active ReviewRun or RepairRun per PR at a time. Button disabled while either is running.
- "Work on Request Changes" button visible only when `GET /prs/.../review-state` returns `changes_requested`. Checked on every PR page load.
- `owner`, `repo`, `pr_number` extracted from `window.location.pathname` — no DOM scraping needed.
- Active run_id for a PR persisted via `GM_setValue` keyed by PR URL path.

### DB additions

`workflow_runs` gains a new nullable `pr_number` column. Pipeline runs populate `issue_number` and leave `pr_number` null. ReviewRun and RepairRun populate `pr_number` and leave `issue_number` null. Kept separate to avoid lookup collision when issue number and PR number happen to be equal.

`workflow_runs.workflow_type` gains new values: `"review"`, `"repair"`. New stages: `review`, `post`, `issues`, `repair`, `push` — added to the known stage name set; same `workflow_stages` table schema otherwise.

---

## Open decisions

- Desktop app framework: decision deferred. Python + userscript is the active mode.
- Skill files format and loading
- GitHub REST API direct PR creation (replacing `gh` dependency)
- Failure repair loop (max 2 attempts)
- SQLite migration strategy
