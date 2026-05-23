# Pawchestrator Product Requirements Document

**Document status:** Draft for agent grilling  
**Intended audience:** Founder/developer, coding agents, design-review agents, future contributors  
**Project type:** Local-first, open-source, GitHub-native agent orchestration platform  
**Primary interaction model:** GitHub issue page augmentation + local backend + local agent runners  
**Preferred project name:** Pawchestrator  
**Date:** 2026-05-23

---

## 0. How to use this PRD for grilling

This PRD is intentionally expansive. It is meant to be handed to an agent and used as a grilling target. The agent should challenge unclear requirements, ask missing product questions, identify technical risks, force trade-off decisions, and convert vague ideas into implementation-ready tickets.

The grilling agent should focus especially on:

1. Whether Pawchestrator is primarily a desktop app, a local daemon, a browser augmentation layer, or all three.
2. Whether the first release should ship the embedded GitHub viewer, the userscript mode, or the CLI/backend first.
3. How Pawchestrator can reliably drive Codex and Claude through subscription-backed local tooling rather than API-only usage.
4. How safe the local browser-to-backend bridge is.
5. Whether the workflow model is too powerful for v1.
6. Whether the project scope is too broad for a usable public alpha.
7. Which features must be cut to create a fast, lovable MVP.

The grilling agent should not simply praise the idea. It should act like a stubborn technical co-founder with a flashlight, a checklist, and absolutely no patience for fog machines.

---

## 1. Executive summary

**Pawchestrator** is a local-first, GitHub-native agent orchestration platform for developers who want to automate software-development workflows using local coding agents and existing monthly subscription tools such as Codex and Claude Code.

The core idea is simple:

```text
Open GitHub issue
↓
Click Pawchestrator-injected button: “Work on this issue”
↓
Pawchestrator reads the issue, comments, repo metadata, and local codebase
↓
Pawchestrator runs configured local agent workflows
↓
Agents scout, grill, plan, implement, verify, commit, and draft PRs
↓
Progress appears inside GitHub and in Pawchestrator’s local UI
```

Pawchestrator should not require a hosted SaaS server, webhooks, port forwarding, tunnels, or a GitHub App backend controlled by the Pawchestrator maintainer. Instead, it runs on the user’s machine and interacts with GitHub through the user’s authenticated session and/or GitHub API credentials.

Pawchestrator has two intended UX modes:

1. **Full Viewer Mode:** Pawchestrator opens a desktop window containing GitHub in an embedded browser/webview. Pawchestrator injects JavaScript, CSS, and HTML into GitHub issue pages to add buttons, status panels, workflow controls, and contextual information.
2. **Backend-only + Userscript Mode:** Pawchestrator runs only the local backend. The user installs a Tampermonkey/userscript that injects Pawchestrator controls into the normal browser’s GitHub pages. The userscript talks to the local Pawchestrator backend over a local WebSocket or HTTP bridge.

The product should feel like **GitHub with an agent cockpit quietly bolted underneath**.

---

## 2. Product thesis

Modern coding agents are becoming powerful enough to complete real engineering tasks, but developer workflows remain awkward:

- Work is tracked in GitHub issues.
- Context lives in issue bodies, comments, linked issues, pull requests, repo docs, and local files.
- Agents run in terminals, IDEs, web apps, or separate tools.
- Handoffs between planning, implementation, verification, and PR creation are often manual.
- Multi-agent workflows are usually built with API-first frameworks, which conflicts with users who want to use their existing monthly subscriptions.
- GitHub Apps and webhooks often imply hosted infrastructure, tunnels, or networking setup.

Pawchestrator’s thesis:

> The best user experience for local agentic development is to make GitHub itself the control surface, while the actual orchestration runs locally on the developer’s machine.

Pawchestrator should not ask users to write Python scripts or define LangGraph graphs just to get value. The user should be able to pick an issue, click a button, and watch a local, inspectable workflow unfold.

---

## 3. Target users

### 3.1 Primary user

A developer, open-source maintainer, indie hacker, or small-team engineer who:

- Uses GitHub issues and pull requests.
- Uses or wants to use coding agents.
- Has local repositories checked out.
- Wants more automation without surrendering their workflow to a cloud SaaS.
- Prefers local tools, local control, and inspectable execution.
- Has access to Codex and/or Claude through monthly plans and wants to use those plans rather than defaulting to LLM API billing.

### 3.2 Secondary user

A maintainer who wants help with:

- Issue triage.
- Asking better clarification questions.
- Drafting implementation plans.
- Generating first-pass PRs.
- Reviewing incoming issues.
- Running repeatable maintenance workflows.

### 3.3 Advanced user

A power user who wants to configure:

- Agent roles.
- Runner mappings.
- Skills.
- Workflows.
- GitHub label conventions.
- Local MCP servers.
- Repository-specific policies.
- Local model fallbacks.
- Approval gates.

---

## 4. Non-goals

Pawchestrator should not try to be everything in v1.

### 4.1 Not a hosted SaaS

The default product must not require a Pawchestrator-owned cloud service. A future optional relay or team sync service may exist, but the core value must work locally.

### 4.2 Not a generic automation platform

Pawchestrator is not trying to replace n8n, Zapier, Temporal, GitHub Actions, LangGraph, or CI/CD. It is focused on developer issue-to-PR workflows.

### 4.3 Not an API billing wrapper

Pawchestrator must not require OpenAI or Anthropic API keys to function. API mode can exist as an advanced optional adapter, but subscription-backed local agent tools are first-class.

### 4.4 Not an IDE replacement

Pawchestrator should integrate with existing editors and local repositories. It is not a full code editor.

### 4.5 Not a blind autonomous merge bot

Merging should require explicit human approval by default. Pawchestrator may create draft PRs and run verifications, but it should not silently merge changes.

### 4.6 Not a prompt-only toy

The product must use durable state, artifacts, local worktrees, logs, and structured outputs where possible. It should not rely on a single giant chat transcript.

---

## 5. Guiding principles

### 5.1 GitHub is the UX

The user should interact mostly through GitHub issue pages, PR pages, labels, comments, branches, and reviews.

### 5.2 Local-first by default

The orchestration engine, state database, runner processes, artifacts, logs, and repo operations should run locally.

### 5.3 Subscription-backed runners first

Codex CLI / Codex app / Codex IDE integration and Claude Code CLI / Claude surfaces should be used through the user’s monthly plan where possible. API-key based runners are optional and clearly marked as billable.

### 5.4 Artifacts over chatter

Agents should hand off structured artifacts, not long freeform chat transcripts.

### 5.5 Inspectability beats magic

Every automation should leave visible traces:

- Issue snapshot.
- Agent prompts.
- Artifacts.
- Commands run.
- Files changed.
- Branches created.
- GitHub API actions.
- Verification results.
- Failure reasons.

### 5.6 One issue, one isolated workspace

Each active issue should get an isolated git worktree and branch to avoid collisions.

### 5.7 Human gates are normal

The system should pause for human approval before dangerous actions such as pushing, opening a PR, force operations, running risky shell commands, or posting large public comments.

### 5.8 Boring reliability beats dramatic autonomy

The product should be robust, resumable, and understandable. The grand orchestra is cute; the drummer still needs a metronome.

---

## 6. High-level architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                         GitHub Website                       │
│   Issues · Pull Requests · Comments · Labels · Milestones    │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               │ Injected UI
                               │
┌──────────────────────────────▼───────────────────────────────┐
│                    Pawchestrator Frontend                     │
│                                                              │
│  Mode A: Desktop Viewer with embedded GitHub webview          │
│  Mode B: Tampermonkey/userscript in user's normal browser     │
└──────────────────────────────┬───────────────────────────────┘
                               │ Local bridge
                               │ WebSocket / HTTP / Tauri invoke
┌──────────────────────────────▼───────────────────────────────┐
│                    Pawchestrator Backend                      │
│                                                              │
│  Local API · Workflow Engine · State DB · Artifact Store      │
│  GitHub Client · Runner Manager · Worktree Manager            │
└───────────────┬──────────────────────┬───────────────────────┘
                │                      │
                │                      │
┌───────────────▼──────────────┐ ┌─────▼───────────────────────┐
│       Local Agent Runners     │ │      Local Developer Tools   │
│                               │ │                              │
│  Codex CLI / Codex tools      │ │  git · gh · build tools       │
│  Claude Code CLI              │ │  test runners · linters       │
│  Local model runners          │ │  IDE hooks · MCP servers       │
│  Optional API runners         │ │                              │
└───────────────────────────────┘ └──────────────────────────────┘
```

---

## 7. Product modes

## 7.1 Full Viewer Mode

Full Viewer Mode is the default experience.

The user runs:

```bash
pawchestrator open
```

Pawchestrator opens a desktop window that contains:

- A GitHub webview.
- Pawchestrator sidebar.
- Injected GitHub buttons.
- Local run timeline.
- Settings/doctor panel.
- Runner status indicators.
- Artifact viewer.
- Worktree and branch viewer.

### 7.1.1 Why this mode exists

This mode avoids:

- Port forwarding.
- Webhook tunnels.
- GitHub App hosting.
- Browser extension stores.
- External servers.

It gives Pawchestrator direct control over the page augmentation layer and a polished product experience.

### 7.1.2 Embedded browser requirements

The embedded browser must:

- Load GitHub normally.
- Allow user login to GitHub.
- Persist cookies/session storage securely.
- Inject scripts/styles into GitHub pages.
- Provide a safe bridge between web content and Pawchestrator backend.
- Avoid exposing raw shell execution to injected scripts.

### 7.1.3 Recommended stack

Recommended stack:

- Tauri v2 desktop shell.
- Rust backend/core.
- TypeScript frontend.
- Native webview per OS.
- SQLite local state.
- Tokio async runtime.
- Axum or equivalent local HTTP/WebSocket server.

Potential alternatives:

- Electron: easier browser-extension-like behavior but heavier.
- Neutralino: lightweight but less mature for complex integrations.
- Avalonia: good native UI, but embedded browser and JS injection path must be verified.

The current preferred candidate is **Tauri** because it is cross-platform, lightweight, and fits a local Rust backend architecture.

## 7.2 Backend-only + userscript mode

The user runs:

```bash
pawchestrator serve
```

Then installs a userscript for Tampermonkey or compatible managers.

The userscript:

- Runs on `github.com/*` issue and PR pages.
- Injects Pawchestrator controls into GitHub.
- Connects to `ws://127.0.0.1:<port>` or `http://127.0.0.1:<port>`.
- Displays connection status.
- Sends issue actions to the local backend.
- Renders run status and artifacts.

### 7.2.1 Why this mode exists

Some developers prefer their normal browser with their normal profile, extensions, password manager, and tabs. Userscript mode lets them keep that while still using Pawchestrator.

### 7.2.2 Pairing requirement

The userscript must not be able to control Pawchestrator just because it runs on a GitHub page.

Required pairing flow:

1. User starts local backend.
2. Backend generates a random pairing token.
3. User opens Pawchestrator pairing page.
4. Userscript receives or asks for pairing token.
5. Backend stores approved browser session.
6. Backend rejects unpaired origins/sessions.

## 7.3 CLI-only mode

CLI-only mode is for debugging and power users:

```bash
pawchestrator issue start https://github.com/Owner/Repo/issues/42
pawchestrator run show <run-id>
pawchestrator doctor
```

CLI-only mode should not be the primary UX, but it is essential for testing, CI-like scripts, and agent debugging.

---

## 8. Installation and distribution

## 8.1 Installation goal

The ideal first-run should be:

```bash
uvx pawchestrator install
pawchestrator open
```

Or:

```bash
curl -fsSL https://pawchestrator.dev/install | sh
pawchestrator open
```

For Windows:

```powershell
irm https://pawchestrator.dev/install.ps1 | iex
pawchestrator open
```

The install command should hide OS-specific details where possible.

## 8.2 Supported platforms

Pawchestrator should target:

- Windows 10/11.
- macOS latest supported stable releases.
- Linux developer distributions such as Ubuntu, Debian, Fedora, Arch.

## 8.3 Packaging strategy

Recommended staged strategy:

### Stage 1: Developer alpha

- `uvx pawchestrator` bootstrapper.
- Downloads native backend/viewer from GitHub Releases.
- Supports manual install if bootstrap fails.

### Stage 2: Public alpha

- GitHub Releases with signed artifacts.
- Windows installer.
- macOS `.dmg` or `.pkg`.
- Linux AppImage and/or `.deb`/`.rpm`.
- Backend-only archive.

### Stage 3: Mature distribution

- Homebrew tap.
- Winget.
- Scoop.
- AUR package.
- Debian/RPM repo if justified.

## 8.4 Installer responsibilities

The installer should:

- Install Pawchestrator CLI.
- Install or download desktop viewer if selected.
- Configure app data directories.
- Offer backend-only mode.
- Offer userscript installation instructions.
- Run `pawchestrator doctor`.
- Detect required tools.
- Create shell PATH entries if needed.
- Avoid silently modifying global system settings.

## 8.5 `doctor` command

`pawchestrator doctor` should check:

```text
Git                      required
GitHub auth              required
Local repo access        required per repo
Codex CLI                optional but recommended
Codex auth               optional but recommended
Claude Code CLI          optional but recommended
Claude auth              optional but recommended
gh CLI                   optional
Node/npm                 optional, for Codex install path if needed
Rust/Tauri deps          development only
SQLite DB                required
Local backend port       required
Userscript pairing       mode-dependent
```

Doctor output example:

```text
Pawchestrator Doctor

Core
  ✓ Pawchestrator backend found
  ✓ SQLite database writable
  ✓ Local API bound to 127.0.0.1 only

GitHub
  ✓ Authenticated as LucaWichmann
  ✓ Repo access: LucaWichmann/Pawlisher
  ! Missing permission: write labels on LucaWichmann/SomeRepo

Runners
  ✓ Codex CLI found
  ✓ Codex signed in with ChatGPT account
  ✓ Claude Code found
  ! ANTHROPIC_API_KEY is set and may force API billing

Browser Integration
  ✓ Viewer mode available
  ! Userscript not paired
```

---

## 9. GitHub integration

## 9.1 Core GitHub stance

Pawchestrator should treat GitHub as the canonical collaboration surface but not depend on GitHub webhooks or a hosted GitHub App for v1.

Primary mechanisms:

1. User browsing GitHub in Pawchestrator Viewer or browser userscript.
2. Injected UI sends explicit local commands.
3. Local backend uses GitHub API to fetch canonical issue/PR data and perform authorized operations.
4. Local git performs branch/worktree/commit/push operations.

## 9.2 Authentication options

Preferred order:

1. GitHub OAuth device flow or equivalent local-friendly auth.
2. Reuse `gh` CLI auth token if available and user approves.
3. Fine-grained personal access token.
4. GitHub App local setup, future advanced mode.

## 9.3 Required GitHub operations

Pawchestrator must support:

- Get issue.
- List issue comments.
- Create issue comments.
- Update Pawchestrator-owned comments.
- Add/remove labels.
- Assign/unassign user.
- Read PR data.
- Create draft PR.
- Update PR body.
- Optionally request review.
- Optionally update project/milestone in future.

## 9.4 Labels

Recommended default labels:

```text
pawchestrator:run
pawchestrator:running
pawchestrator:scouting
pawchestrator:needs-info
pawchestrator:planning
pawchestrator:implementing
pawchestrator:verifying
pawchestrator:blocked
pawchestrator:failed
pawchestrator:pr-ready
pawchestrator:done
pawchestrator:paused
pawchestrator:human-review
```

Only one stage label should be active at a time.

## 9.5 Issue claiming

When a user starts a workflow, Pawchestrator should:

- Assign the current user if configured and permitted.
- Add `pawchestrator:running`.
- Add the current stage label.
- Optionally post a start comment.
- Store run ID locally.

Start comment example:

```md
🐾 Pawchestrator started local run `RunId`.

Workflow: `IssueImplementation`
Branch: `paw/issue-42-add-runner-adapter`
Mode: local
Runner plan:
- Scout: Claude Code subscription
- Plan: Claude Code subscription
- Implement: Codex CLI ChatGPT account
- Verify: Codex CLI + shell commands

This run is local to the maintainer's machine. Public progress comments are summarized, not full internal prompts.
```

## 9.6 GitHub comments policy

Pawchestrator should not spam issue threads.

Recommended policy:

- One start comment.
- One editable status comment per run.
- One final summary comment.
- Grill questions only after user approval.
- Full internal artifacts remain local by default.

---

## 10. GitHub page injection

## 10.1 Injection goals

The injected UI should make Pawchestrator feel native to GitHub.

Injected controls should appear on:

- Issue pages.
- Pull request pages.
- Issue lists.
- Repository pages.
- Future: project boards and milestones.

## 10.2 Issue page controls

Primary issue actions:

```text
🐾 Work on this issue
🔎 Scout
🔥 Grill issue
🧭 Plan implementation
🛠 Implement
✅ Verify
📦 Create draft PR
⏸ Pause run
↻ Retry failed step
🧾 Open run artifacts
```

## 10.3 Readiness panel

A Pawchestrator readiness card should show:

```text
Pawchestrator

Status: Ready / Needs Info / Blocked / Running
Risk: Low / Medium / High
Workflow: IssueImplementation
Repo found locally: Yes
Worktree: Not created / Created
Codex: Ready / Missing / API mode / Subscription mode
Claude: Ready / Missing / API mode / Subscription mode
Recommended action: Grill Issue / Start Scout / Work on Issue
```

## 10.4 Grill Issue UX

The user clicks **Grill Issue**.

Pawchestrator runs a lightweight analysis and shows:

```md
## Suggested clarification questions

1. Should this feature apply to all repositories or only the active repository?
2. Should Pawchestrator create a draft PR automatically after verification succeeds?
3. Which commands define “verified” for this repo?
4. Should generated commits be squashed before PR creation?

## Missing acceptance criteria

- No expected UI behavior specified.
- No failure behavior specified.
- No test requirements specified.
```

User options:

- Copy to clipboard.
- Edit locally.
- Post as GitHub comment.
- Convert to checklist.
- Start planning anyway.

## 10.5 Triage UX

Triage output:

```text
Suggested labels:
- kind:feature
- area:workflow-engine
- risk:medium
- pawchestrator:needs-info

Suggested workflow:
- IssueImplementationWithHumanGate

Readiness:
- Not ready for implementation
- Needs acceptance criteria
```

User decides whether to apply labels.

---

## 11. Local backend

## 11.1 Backend responsibilities

The backend owns:

- Local HTTP/WebSocket server.
- Tauri command bridge integration.
- GitHub API client.
- GitHub auth and token storage.
- Workflow engine.
- Runner manager.
- Process supervision.
- Artifact store.
- SQLite state DB.
- Worktree manager.
- Git operations.
- GitHub comment/label/PR operations.
- Log streaming.
- Settings and repo policies.
- Security boundaries.

## 11.2 Local API

Potential endpoints:

```text
GET  /health
GET  /version
GET  /config
POST /config/update
POST /github/auth/start
POST /github/auth/poll
GET  /github/viewer/session
POST /userscript/pair
GET  /repos
POST /repos/add
POST /issue/snapshot
POST /issue/grill
POST /issue/triage
POST /issue/start
POST /workflow/run
GET  /runs
GET  /runs/{runId}
GET  /runs/{runId}/artifacts
GET  /runs/{runId}/events
POST /runs/{runId}/pause
POST /runs/{runId}/resume
POST /runs/{runId}/cancel
POST /runs/{runId}/retry
WS   /events
```

## 11.3 Local API security

Backend must:

- Bind to `127.0.0.1` by default.
- Never bind to `0.0.0.0` unless the user explicitly enables team/network mode.
- Require a session token for browser/userscript operations.
- Verify local origin where possible.
- Avoid CORS wildcards except during development.
- Not expose arbitrary shell execution to frontend scripts.
- Only expose named actions such as `startIssue`, `grillIssue`, `pauseRun`.
- Rate-limit local API calls.
- Log rejected suspicious calls.

## 11.4 Persistence

Use SQLite for local state.

Tables:

```sql
workflow_runs
workflow_stages
runner_invocations
artifacts
github_repos
github_issues
worktrees
settings
auth_accounts
local_sessions
logs
```

Data directories:

```text
~/.pawchestrator/
  config.toml
  database.sqlite
  auth/
  logs/
  runs/
    {runId}/
      issue.snapshot.json
      scout.report.json
      grill.questions.json
      plan.json
      implementation.report.json
      verification.report.json
      pr.draft.json
      prompts/
      stdout/
      stderr/
      diffs/
  worktrees/
```

---

## 12. Workflow engine

## 12.1 Why custom engine instead of LangGraph-first

Pawchestrator is primarily process orchestration, not API prompt orchestration.

It must coordinate:

- Local CLIs.
- Git.
- GitHub API calls.
- Worktrees.
- Shell commands.
- Human gates.
- Desktop UI.
- Userscript bridge.
- Durable artifacts.

A custom lightweight workflow engine fits better than forcing users to write Python DAGs or requiring API-first model calls.

LangGraph ideas are useful, but LangGraph itself should not be the core user-facing abstraction. An adapter can be added later.

## 12.2 Workflow format

Workflows should be declarative YAML/TOML.

Example:

```yaml
name: IssueImplementation
version: 1

trigger:
  manual_button: work_on_issue

claim:
  assign_current_user: true
  labels_add:
    - pawchestrator:running

worktree:
  strategy: per_issue
  branch_template: "paw/issue-{issue.number}-{issue.slug}"

permissions:
  require_approval_before_push: true
  require_approval_before_pr: true
  allow_merge: false

steps:
  - id: snapshot
    action: github.snapshot_issue
    output: IssueSnapshot

  - id: scout
    runner: claude
    skill: RepoScout
    input:
      - IssueSnapshot
    output: ScoutReport

  - id: grill
    runner: claude
    skill: IssueGrill
    when: "scout.readiness != 'ready'"
    human_gate: true
    output: GrillQuestions

  - id: plan
    runner: claude
    skill: ImplementationPlan
    input:
      - IssueSnapshot
      - ScoutReport
    output: ImplementationPlan

  - id: implement
    runner: codex
    skill: WorkOnIssue
    input:
      - IssueSnapshot
      - ImplementationPlan
    output: ImplementationReport

  - id: verify
    runner: codex
    skill: Verify
    input:
      - ImplementationReport
    output: VerificationReport

  - id: create_pr
    action: github.create_pr
    when: "verify.status == 'passed'"
    draft: true
    human_gate: true
```

## 12.3 Engine concepts

Definitions:

- **Workflow:** Declarative stage graph.
- **Run:** One execution instance against an issue/PR/task.
- **Stage:** A unit in a workflow.
- **Task:** Concrete work given to a runner/action.
- **Runner:** Agent or local command executor.
- **Action:** Built-in deterministic operation, like `github.create_pr`.
- **Artifact:** Typed JSON/Markdown/file output.
- **Gate:** Human approval checkpoint.
- **Transition:** Movement from one stage to another.

## 12.4 Required engine features

MVP engine must support:

- Sequential stages.
- Conditional stages.
- Human gates.
- Retry failed stage.
- Pause/resume.
- Cancel.
- Durable state.
- Artifact references.
- Runner selection.
- Stage labels.
- Per-stage timeouts.
- Basic failure classification.

Future engine should support:

- Parallel stages.
- Fallback runners.
- Max-cost policies.
- Multi-repo workflows.
- Issue dependency graphs.
- Scheduled workflows.
- Team mode.

---

## 13. Agent runner abstraction

## 13.1 Runner goals

The runner layer isolates Pawchestrator from provider-specific CLI details.

All runners expose a common interface:

```ts
interface Runner {
  id: string;
  kind: "process" | "api" | "local_model" | "shell" | "mcp";
  capabilities: RunnerCapabilities;
  checkHealth(): Promise<RunnerHealth>;
  runTask(task: RunnerTask): Promise<RunnerResult>;
}
```

## 13.2 Runner types

MVP:

```text
CodexRunner
ClaudeRunner
ShellRunner
GitRunner
GitHubRunner
```

Later:

```text
OllamaRunner
LMStudioRunner
McpRunner
OpenAiApiRunner
AnthropicApiRunner
CustomCommandRunner
BrowserAutomationRunner
```

## 13.3 Runner mode labels

The UI must clearly label runner billing/access mode:

```text
Codex CLI · ChatGPT account
Claude Code · Subscription
OpenAI API · billable
Anthropic API · billable
Ollama · local
Shell · local
GitHub API · user token
```

## 13.4 CodexRunner requirements

CodexRunner should:

- Detect Codex CLI binary.
- Check Codex health.
- Detect whether authentication appears available.
- Prefer ChatGPT account/subscription access.
- Avoid requiring API key.
- Support non-interactive or scriptable invocation modes where available.
- Run in a specific working directory/worktree.
- Pass prompts through files to avoid shell escaping issues.
- Capture stdout/stderr.
- Capture changed files and git diff after run.
- Enforce command timeout.
- Support approval mode/profile configuration.

Default config:

```toml
[runners.codex]
enabled = true
binary = "codex"
auth_preference = "chatgpt_account"
allow_api_key = false
working_directory_mode = "worktree"
```

## 13.5 ClaudeRunner requirements

ClaudeRunner should:

- Detect Claude Code CLI binary.
- Check Claude health.
- Prefer subscription-backed use.
- Warn if `ANTHROPIC_API_KEY` is set and could cause API-billed behavior.
- Support print mode / JSON output where available.
- Support structured JSON schema output where available.
- Run in a specific worktree.
- Capture stdout/stderr.
- Capture artifacts.
- Respect permission modes.

Default config:

```toml
[runners.claude]
enabled = true
binary = "claude"
auth_preference = "subscription"
allow_api_key = false
default_args = ["-p", "--output-format", "json"]
```

## 13.6 API runner policy

API runners are optional advanced features.

If enabled, UI must show:

```text
This runner uses API billing. It may incur separate provider charges.
```

API mode must never become the silent default.

---

## 14. Skills and prompts

## 14.1 Skill philosophy

Skills should be small, composable, and provider-portable.

A skill is not a whole agent. It is a reusable instruction pack for a specific task.

Examples:

```text
RepoScout
IssueGrill
IssueTriage
ImplementationPlan
WorkOnIssue
Verify
PullRequestWriter
CommitDiscipline
FailureRecovery
GitHubOperations
CavemanRenderer
WenyanRendererExperimental
```

## 14.2 Canonical skill layout

```text
skills/
  RepoScout/
    Skill.md
    Inputs.schema.json
    Outputs.schema.json
    Examples.md
    Notes.md
```

## 14.3 Provider adapters

Pawchestrator should be able to export/adapt canonical skills into provider-specific formats:

```text
exports/
  codex/
  claude/
```

The canonical source of truth remains Pawchestrator’s skill format.

## 14.4 Skill loading

Runners should receive only the skills relevant to the current task.

Bad:

```text
Give every agent every instruction, every skill, every repo policy, and the entire lore book.
```

Good:

```text
Scout receives RepoScout + GitHubOperations + repo AGENTS.md summary.
Implementer receives WorkOnIssue + ImplementationPlan + relevant repo policies.
Verifier receives Verify + command list + expected artifacts.
```

## 14.5 Caveman/Wenyan policy

Caveman and Wenyan can be used as optional compression/rendering strategies, but they should not be the canonical protocol.

Canonical inter-agent handoff should be typed JSON artifacts.

Use:

```json
{
  "status": "ready",
  "risk": "medium",
  "next_stage": "implement"
}
```

Not:

```text
Many cryptic words, cute but fragile.
```

Caveman ultra can be used for concise user-facing status if enabled.

Wenyan can be experimental and benchmarked.

---

## 15. Artifact system

## 15.1 Artifact philosophy

Agents should not primarily hand off long conversational prose. They should write typed artifacts that can be inspected, validated, replayed, and passed into the next stage.

## 15.2 Required artifacts

MVP artifact schemas:

```text
IssueSnapshot
RepoSnapshot
ScoutReport
GrillQuestions
TriageReport
ImplementationPlan
TaskPrompt
RunnerInvocation
RunnerResult
ImplementationReport
VerificationReport
PullRequestDraft
RunSummary
FailureReport
```

## 15.3 Example IssueSnapshot

```json
{
  "schema": "pawchestrator.issue_snapshot.v1",
  "owner": "LucaWichmann",
  "repo": "Pawlisher",
  "number": 42,
  "title": "Add handler memoization",
  "body": "...",
  "labels": ["kind:feature"],
  "assignees": [],
  "comments": [
    {
      "author": "LucaWichmann",
      "body": "Need to respect width specialization.",
      "created_at": "2026-05-23T10:00:00Z"
    }
  ],
  "source_url": "https://github.com/LucaWichmann/Pawlisher/issues/42"
}
```

## 15.4 Example ScoutReport

```json
{
  "schema": "pawchestrator.scout_report.v1",
  "status": "success",
  "readiness": "ready",
  "risk": "medium",
  "files_examined": [
    "Src/Pawlisher/HandlerOracle.cpp",
    "Src/Pawlisher/Pir/PirInstruction.h"
  ],
  "findings": [
    {
      "kind": "implementation_hint",
      "text": "Handler memoization must include VM instance and width specialization."
    }
  ],
  "risks": [
    {
      "level": "medium",
      "text": "Polymorphic handlers may make naive handler-address cache invalid."
    }
  ],
  "next_recommended_stage": "plan"
}
```

## 15.5 Example VerificationReport

```json
{
  "schema": "pawchestrator.verification_report.v1",
  "status": "failed",
  "commands": [
    {
      "command": "cmake --build build",
      "exit_code": 0
    },
    {
      "command": "ctest --test-dir build",
      "exit_code": 8,
      "summary": "2 tests failed"
    }
  ],
  "recommended_next_stage": "fix"
}
```

---

## 16. Git and worktree management

## 16.1 Worktree strategy

Default:

```text
One issue = one branch = one worktree
```

Example:

```text
~/.pawchestrator/worktrees/LucaWichmann/Pawlisher/issue-42/
```

## 16.2 Branch naming

Default:

```text
paw/issue-{number}-{slug}
```

Example:

```text
paw/issue-42-handler-memoization
```

## 16.3 Commit policy

Commits should be granular.

Good:

```text
feat(pir): add handler memoization key
feat(oracle): cache specialized handler reports
test(oracle): cover width-specialized cache entries
```

Bad:

```text
agent changes
fix stuff
huge implementation
```

## 16.4 Pull request policy

Default PRs should be draft PRs.

PR body should include:

```md
## Summary

## Linked issue
Fixes #42

## What Pawchestrator did

## Verification

## Human review notes

## Local artifacts
Artifacts are stored locally under run ID `...` and were not posted publicly.
```

---

## 17. Verification model

## 17.1 Repository commands

Each repo can define commands:

```toml
[commands]
setup = ""
build = "cmake --build build"
test = "ctest --test-dir build"
lint = ""
format_check = ""
```

## 17.2 Verification stages

Verifier should run:

- Build.
- Tests.
- Lint, if configured.
- Formatting check, if configured.
- Optional smoke command.
- Git diff review.

## 17.3 Failure repair loop

If verification fails:

1. Store failure report.
2. Ask user whether to run repair.
3. Run implementer/fixer with failure report.
4. Re-run verification.
5. Limit repair attempts.

Default max repair attempts:

```text
2
```

---

## 18. Permissions and human gates

## 18.1 Permission levels

Pawchestrator should define permission levels:

```text
ReadOnly
CanCreateWorktree
CanEditFiles
CanRunSafeCommands
CanRunAllCommands
CanCommit
CanPush
CanCreatePr
CanMerge
```

Default:

```text
CanCreateWorktree: yes
CanEditFiles: yes
CanRunSafeCommands: yes
CanCommit: yes
CanPush: ask
CanCreatePr: ask
CanMerge: no
```

## 18.2 Dangerous operations

Always require approval for:

- Force push.
- Merge PR.
- Delete remote branch.
- Delete files outside repo/worktree.
- Modify global git config.
- Read credential files.
- Publish packages.
- Deploy to production.
- Run commands outside approved repo directories.
- Access SSH keys.
- Modify Pawchestrator auth store.

## 18.3 UI approval prompt

Example:

```text
Pawchestrator wants to push branch:

Repo: LucaWichmann/Pawlisher
Branch: paw/issue-42-handler-memoization
Commits: 3
Files changed: 8

[View diff] [Allow once] [Deny] [Always allow for this repo]
```

---

## 19. Security and privacy

## 19.1 Threat model

Pawchestrator is powerful because it can:

- Read local repositories.
- Run local commands.
- Modify files.
- Commit and push code.
- Post to GitHub.
- Interact with local agent CLIs.

Therefore it must be designed like a local automation tool with serious security boundaries.

## 19.2 Local bridge risks

The browser/userscript bridge is a key risk.

Mitigations:

- Bind backend to localhost only.
- Pair userscript with random secret.
- Rotate secrets.
- Check Origin/Host headers where feasible.
- Never expose arbitrary shell endpoint.
- Allow only named commands.
- Require approval for dangerous actions.
- Show active browser sessions.
- Let user revoke sessions.

## 19.3 Token storage

Store tokens in OS keychain where possible.

Fallback encrypted file only if necessary, with explicit user warning.

Never write tokens to logs.

## 19.4 Artifact privacy

Internal artifacts can contain sensitive details.

Default:

- Full artifacts stay local.
- Public GitHub comments get summaries only.
- User decides whether to post grill questions/plans.
- Prompts are not posted publicly by default.

## 19.5 Secret scanning

Before creating PR comments or summaries, scan for obvious secrets:

- API keys.
- Tokens.
- Private keys.
- `.env` content.
- Credentials.

Block or warn before posting.

---

## 20. Desktop viewer UI

## 20.1 Main layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Pawchestrator                                               │
├───────────────┬─────────────────────────────────────────────┤
│ Sidebar       │ Embedded GitHub                             │
│               │                                             │
│ Runs          │ GitHub issue page                           │
│ Repos         │ + Pawchestrator injected buttons            │
│ Agents        │ + readiness panel                           │
│ Worktrees     │ + run status                                │
│ Settings      │                                             │
├───────────────┴─────────────────────────────────────────────┤
│ Status: GitHub ✓ Codex ✓ Claude ✓ Backend ✓                 │
└─────────────────────────────────────────────────────────────┘
```

## 20.2 Sidebar sections

- Active runs.
- Recent runs.
- Repositories.
- Runner health.
- Worktrees.
- Settings.
- Doctor.
- Logs.

## 20.3 Run detail page

Show:

- Issue link.
- Workflow name.
- Stage timeline.
- Current stage.
- Runner used.
- Artifacts.
- Stdout/stderr.
- Diff view.
- Approval prompts.
- Retry buttons.

## 20.4 Runner status

Status pills:

```text
GitHub: Connected
Codex: Ready · ChatGPT account
Claude: Ready · Subscription
Shell: Ready
Worktree: Clean
```

Warnings:

```text
Claude API key detected. This may cause API-billed usage.
Codex not signed in.
Repo not found locally.
Userscript unpaired.
```

---

## 21. Userscript design

## 21.1 Userscript header sketch

```js
// ==UserScript==
// @name         Pawchestrator GitHub Integration
// @namespace    https://github.com/Pawchestrator
// @version      0.1.0
// @description  Adds Pawchestrator controls to GitHub issue and PR pages
// @match        https://github.com/*/*/issues/*
// @match        https://github.com/*/*/pull/*
// @run-at       document-idle
// @grant        GM_addStyle
// ==/UserScript==
```

## 21.2 Userscript responsibilities

- Detect issue/PR URL.
- Inject minimal CSS.
- Inject action buttons.
- Connect to backend.
- Show backend connection status.
- Send commands with issue identity.
- Render run progress.
- Avoid scraping full issue contents as canonical source.

Canonical issue data should come from GitHub API via the backend.

## 21.3 Userscript connection states

```text
Disconnected
Backend not running
Pairing required
Connected
Run active
Error
```

---

## 22. Configuration

## 22.1 Global config example

```toml
[app]
mode = "viewer"
theme = "system"
telemetry = false

[backend]
host = "127.0.0.1"
port = 38472
require_pairing_token = true

[github]
auth = "device_flow"
use_gh_cli_token_if_available = true

[runners.codex]
enabled = true
binary = "codex"
auth_preference = "chatgpt_account"
allow_api_key = false

[runners.claude]
enabled = true
binary = "claude"
auth_preference = "subscription"
allow_api_key = false
warn_if_api_key_env_present = true

[worktrees]
strategy = "per_issue"
root = "~/.pawchestrator/worktrees"

[workflow]
default = "IssueImplementation"
```

## 22.2 Repo config example

```toml
[repo]
name = "Pawlisher"
owner = "LucaWichmann"

[policy]
auto_assign = true
auto_create_pr = false
draft_pr_by_default = true
require_human_approval_before_push = true
require_human_approval_before_pr = true
allow_merge = false

[commands]
build = "cmake --build build"
test = "ctest --test-dir build"
lint = ""

[labels]
running = "pawchestrator:running"
blocked = "pawchestrator:blocked"
done = "pawchestrator:done"
```

---

## 23. MVP roadmap

## 23.1 MVP 0: Core backend proof

Goal: prove issue-to-local-agent-to-PR works without UI magic.

Features:

- CLI install/dev run.
- GitHub auth.
- Read issue by URL.
- Fetch comments.
- Create worktree.
- CodexRunner prototype.
- ClaudeRunner prototype.
- Workflow engine minimal sequential stages.
- Artifact storage.
- Create draft PR.
- Doctor command.

## 23.2 MVP 1: Desktop viewer

Goal: make it feel like a product.

Features:

- Tauri app shell.
- Embedded GitHub webview.
- Injected issue buttons.
- Work on Issue action.
- Run timeline.
- Runner status.
- Settings page.

## 23.3 MVP 2: Userscript mode

Goal: support users who prefer normal browser.

Features:

- Backend-only install.
- Tampermonkey script.
- Local pairing.
- GitHub issue injection.
- Run status rendering.

## 23.4 MVP 3: Public alpha

Goal: open-source release usable by early adopters.

Features:

- Signed releases.
- Documentation.
- Example workflows.
- Example skills.
- Contribution guide.
- Security policy.
- Known limitations.
- Agent grilling templates.

---

## 24. Open-source repository layout

```text
Pawchestrator/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── AGENTS.md
├── docs/
│   ├── Architecture.md
│   ├── Security.md
│   ├── Workflows.md
│   ├── Runners.md
│   ├── UserscriptMode.md
│   └── Development.md
├── apps/
│   ├── Desktop/
│   └── Userscript/
├── crates/
│   ├── PawchestratorCore/
│   ├── PawchestratorBackend/
│   ├── PawchestratorGithub/
│   ├── PawchestratorWorkflow/
│   ├── PawchestratorRunners/
│   ├── PawchestratorArtifacts/
│   └── PawchestratorSecurity/
├── cli/
├── workflows/
│   ├── IssueImplementation.yml
│   ├── IssueTriage.yml
│   ├── PullRequestReview.yml
│   └── ReleasePrep.yml
├── skills/
│   ├── RepoScout/
│   ├── IssueGrill/
│   ├── IssueTriage/
│   ├── ImplementationPlan/
│   ├── WorkOnIssue/
│   ├── Verify/
│   └── PullRequestWriter/
├── schemas/
├── examples/
└── tests/
```

Coding style preference:

- Project/file names: `UpperCaseAtStart`.
- No underscores in project/file names where possible.
- Modern C++ style is relevant for Paw ecosystem projects, but Pawchestrator itself may be Rust/TypeScript.
- Public naming should stay consistent and readable.

---

## 25. Technical risks

## 25.1 Provider CLI instability

Codex and Claude CLI behavior may change.

Mitigation:

- Isolate provider specifics in runner adapters.
- Do not parse human prose if artifact files are available.
- Version runner adapters.
- Build doctor checks.
- Support graceful degradation.

## 25.2 Subscription access uncertainty

Monthly subscription access and CLI automation behavior may change.

Mitigation:

- Make subscription-backed runners first-class but not assumed eternal.
- Add local runner and API runner adapters.
- Clearly label billing modes.
- Avoid depending on undocumented web UI scraping.

## 25.3 GitHub DOM fragility

Injected UI can break if GitHub changes markup.

Mitigation:

- Use minimal DOM assumptions.
- Detect owner/repo/issue from URL.
- Fetch canonical data via GitHub API.
- Keep injected UI self-contained.
- Add selector tests.

## 25.4 Local bridge security

A userscript communicating with localhost can be dangerous.

Mitigation:

- Pairing token.
- Localhost only.
- Named actions only.
- No raw shell endpoint.
- Human approval gates.

## 25.5 Scope creep

The project can easily become too large.

Mitigation:

- Ship backend proof first.
- Ship one happy path.
- Avoid marketplace/team/cloud features until core loop works.

## 25.6 Cross-platform packaging

Desktop packaging across Windows/macOS/Linux is non-trivial.

Mitigation:

- Keep backend-only mode working.
- Use GitHub Releases early.
- Document OS prerequisites.
- Test on all target OSes.

---

## 26. Open questions for grilling

The grilling agent should ask the founder to answer these.

### 26.1 Product scope

1. What is the absolute minimum lovable v1?
2. Should v1 start as CLI/backend proof, desktop viewer, or userscript?
3. Is the embedded GitHub viewer required for first public release?
4. Is Tampermonkey mode required for first public release?
5. Is the project for solo developers first or teams first?

### 26.2 Runner behavior

1. Which Codex surface is the primary target: CLI, IDE, desktop app, or ChatGPT web Codex?
2. Which Claude surface is the primary target: Claude Code CLI, desktop, IDE, or browser?
3. How should Pawchestrator detect subscription-backed vs API-billed execution?
4. Should Pawchestrator refuse to run if it detects API-key billing unless user explicitly opts in?
5. What is the fallback if Codex/Claude CLIs are not scriptable enough?

### 26.3 GitHub auth

1. Should Pawchestrator use GitHub device flow, `gh` auth token, PATs, or multiple options?
2. Should Pawchestrator ever create a GitHub App locally?
3. What permissions are acceptable for v1?
4. Should Pawchestrator support private repositories in v1?
5. How should it handle organizations with SSO restrictions?

### 26.4 Security

1. How strict should local socket pairing be?
2. Should userscript mode be disabled by default until explicitly enabled?
3. What operations require approval?
4. How are secrets redacted from artifacts and comments?
5. How can users inspect exactly what will be posted to GitHub?

### 26.5 Workflow design

1. Is YAML powerful enough, or should there be a UI workflow builder?
2. Should workflows support loops in v1?
3. Should failed verification auto-trigger repair?
4. Should “grill issue” be mandatory before implementation when readiness is low?
5. Should workflows be repo-local or globally installed?

### 26.6 UI design

1. Where exactly should injected buttons appear on GitHub issue pages?
2. Should Pawchestrator use a side panel, floating panel, or inline card?
3. How much progress should be posted to GitHub versus kept local?
4. Should the desktop app support normal browser tabs or only GitHub?
5. How should it handle GitHub login/cookies in embedded webview?

### 26.7 Open-source release

1. What license?
2. What contribution rules?
3. How much of the runner integration can be public without violating provider expectations?
4. What security disclaimers are required?
5. What should be explicitly out-of-scope for contributors?

---

## 27. Success criteria

## 27.1 Alpha success

Pawchestrator alpha is successful if:

- A developer can install it with one command.
- A developer can open GitHub in Pawchestrator.
- The issue page shows Pawchestrator controls.
- Clicking Work on Issue starts a local run.
- Pawchestrator reads issue details and comments.
- Pawchestrator creates a worktree and branch.
- Pawchestrator runs at least one Codex or Claude runner.
- Pawchestrator generates artifacts.
- Pawchestrator commits changes.
- Pawchestrator verifies changes.
- Pawchestrator creates a draft PR or prepares one for approval.
- A failed run is understandable and resumable.

## 27.2 Product success

The product is successful if users say:

```text
I still work in GitHub, but now issues have an automation cockpit.
```

and not:

```text
I had to learn a weird workflow framework before anything worked.
```

---

## 28. Initial implementation tickets

### Ticket 1: Repository scaffold

Create the initial repository structure with docs, crates/apps directories, linting, and basic development instructions.

### Ticket 2: Local backend skeleton

Implement local backend with `/health`, config loading, SQLite initialization, and local-only bind.

### Ticket 3: GitHub auth prototype

Implement GitHub auth using the chosen method and fetch authenticated user.

### Ticket 4: Issue snapshot

Given a GitHub issue URL, fetch issue metadata and comments into `IssueSnapshot` artifact.

### Ticket 5: Worktree manager

Create isolated worktree and branch for an issue.

### Ticket 6: Runner interface

Define runner traits/interfaces and implement ShellRunner.

### Ticket 7: CodexRunner prototype

Detect Codex CLI, run a controlled prompt in a worktree, capture output and diff.

### Ticket 8: ClaudeRunner prototype

Detect Claude Code CLI, run a controlled prompt in a worktree, capture JSON output where possible.

### Ticket 9: Minimal workflow engine

Run sequential stages: snapshot → scout → plan → implement → verify.

### Ticket 10: Artifact viewer CLI

Inspect artifacts and logs for a run.

### Ticket 11: Desktop viewer shell

Create Tauri app shell with embedded GitHub view and backend status.

### Ticket 12: GitHub issue injection

Inject Work on Issue button into GitHub issue page in viewer mode.

### Ticket 13: Userscript prototype

Create Tampermonkey script that injects controls and connects to local backend.

### Ticket 14: PR creation

Create draft PR from branch with generated summary.

### Ticket 15: Safety gates

Add approval before push and PR creation.

---

## 29. Final product summary

Pawchestrator is a local-first GitHub augmentation and agent orchestration tool. It runs on the user’s machine, augments GitHub issue pages with workflow controls, and coordinates local agents such as Codex and Claude Code through subscription-backed tooling where possible.

Its core promise:

> Pick an issue on GitHub. Click a Pawchestrator button. Let local agents scout, grill, plan, implement, verify, and prepare a PR, while every step remains inspectable and under user control.

The product should be powerful, but not spooky. Cute, but not flimsy. Local-first, but not painful. Agentic, but not agent soup.

Pawchestrator should become the little conductor standing on the GitHub issue page, baton in paw, making the whole local agent orchestra play in time.

---

## 30. Source notes used while drafting

These sources were consulted to ground implementation assumptions:

- OpenAI Codex CLI documentation: local terminal use, repository inspection/editing/command execution, and ChatGPT account or API-key authentication.
- Claude Code documentation: terminal/IDE/desktop/browser availability, CLI options, JSON/stream JSON output, structured output flags, and permission modes.
- Tauri documentation: desktop prerequisites and WebView2 usage on Windows.
- Tampermonkey documentation: userscript headers and APIs such as `@match`, `@run-at`, and `GM_addStyle`.
- MDN WebSocket documentation: browser WebSocket constructor behavior.
- GitHub OAuth and REST documentation: device flow, issue data, issue comments, labels/assignees, and pull request creation.

