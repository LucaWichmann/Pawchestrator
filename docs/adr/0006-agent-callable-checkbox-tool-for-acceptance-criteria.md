# ADR 0006: Agent-callable Bash tool for acceptance criteria checkbox checking

**Status:** Accepted  
**Date:** 2026-05-25

## Context

GitHub issues often contain acceptance criteria as markdown checkboxes (`- [ ]`). When Pawchestrator creates a PR, unchecked boxes signal to reviewers that the issue is unfinished — even if the implementation is complete. Pawchestrator needs a way to check these off when criteria are met.

Three approaches were considered:

1. **All-or-nothing on verify pass** — if verify passes, check all in-scope boxes. Simple, but semantically wrong: verify passing does not mean every acceptance criterion was individually addressed.

2. **Post-verify LLM evaluation** — after verify passes, run a terse LLM prompt: checkbox text + diff summary → boolean per item. Accurate but costs output tokens (expensive per CONTEXT.md token-efficiency principle) and adds a stage.

3. **Agent-callable Bash CLI tool** — the implement agent calls `pawchestrator checkbox check <owner>/<repo>/<number> <index>` during implementation, as it addresses each criterion. Zero separate LLM inference. The agent's judgment is exercised in the moment, not in a retrospective eval pass.

An MCP tool (C1) was also considered for ClaudeRunner but rejected: it would pollute the global `~/.claude/settings.json`, would be unavailable to CodexRunner (which has no tool allowlist equivalent), and adds config complexity. A Bash CLI command is runner-agnostic.

## Decision

Use a Bash CLI command (`pawchestrator checkbox check <owner>/<repo>/<number> <index>`) that the implement agent calls per criterion as it works. Pawchestrator fetches the latest issue body and PATCHes the updated Markdown without ETag optimistic locking, because GitHub does not support conditional requests for the issue body PATCH endpoint.

**Scope:** Only checkboxes under configured headings (CheckboxHeadings) are in scope. All others are ignored. Default headings: `Acceptance Criteria`, `AC`, `Definition of Done`, `DoD`, `Checklist`, `Requirements`, `Tasks` (case-insensitive). Configurable via `[checkboxes] headings = [...]` in `config.toml`.

**Indexing:** Checkboxes are indexed 0-based within in-scope items only (scoped index). The index passed to the CLI maps directly to the list shown in the implement prompt — no body-wide counting, no ambiguity.

**Snapshot integration:** `fetch_snapshot` parses the issue body and populates `IssueSnapshot.checkboxes: list[{index, text}]` at snapshot time. Parsing happens once; the structured list flows through artifacts. The implement prompt includes the list with indices and the CLI invocation template.

**Fallback:** If the agent never calls the tool, checkboxes remain unchecked. This is intentional — unchecked boxes after a PR signal that the agent did not explicitly confirm each criterion. No auto-check on verify pass. No fallback LLM eval.

**Issue identification in CLI:** The command includes `<owner>/<repo>/<number>` explicitly, not a run ID. This future-proofs the tool for concurrent multi-issue runs where a run ID alone would be ambiguous.

## Consequences

- Implement prompt grows by one line per in-scope checkbox. No cap — input tokens are cheap.
- Checkboxes are checked off live as the agent works, visible in real-time on GitHub.
- Concurrent issue body edits are last-writer-wins for checkbox updates.
- If an issue has no matching headings, the feature is a no-op — no changes, no warnings.
- Unchecked boxes after a PR are an honest signal, not a bug.
- CodexRunner and ClaudeRunner both benefit equally via Bash access.
