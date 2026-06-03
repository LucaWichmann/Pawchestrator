# ADR 0022 — Prompt pipeline token optimizations

## Status

Accepted (grilled 2026-06-04)

## Context

Output tokens from LLMs are significantly more expensive than input tokens. After reviewing real run artifacts in `~/.pawchestrator/runs/`, several prompt-building decisions were found to waste output tokens (fields generated but never read downstream) or inflate input tokens unnecessarily (data injected into prompts that already appears elsewhere or is irrelevant to the stage).

---

## Decisions

### Decision 1 — Pass `steps[].notes` through to the implement prompt

`build_plan_prompt` in `plan.py` instructs the Plan agent to emit `notes` per step. Real artifacts confirm the notes contain implementation-critical details: explicit function names, API constraints, what NOT to delete, pre-conditions, selector strings. However `_prompt_implementation_step` in `implement.py` discards `notes` — only `description` and `files_to_modify` pass through.

This is the worst outcome: the Plan agent spends output tokens writing notes, and the implement agent never sees them.

`_prompt_implementation_step` must include `notes` in its returned dict. Cost: marginal extra input tokens for implement (cheap). Benefit: implement agent receives the implementation constraints the Plan agent produced.

**Alternative rejected:** Drop `notes` from the Plan skill entirely. Rejected because real artifacts prove notes carry high-value implementation details, not human commentary.

---

### Decision 2 — `approach_summary` capped at one sentence ≤150 chars

`implement.py` already caps `approach_summary` at `MAX_PROMPT_APPROACH_SUMMARY_CHARS = 150`. The Plan skill instructed "2–3 sentence overview," which produced summaries of 240–340 chars in real artifacts — silently truncated mid-sentence before reaching the implement agent.

The Plan skill is updated to "one terse sentence ≤150 chars." Steps and notes already carry the implementation detail; `approach_summary` is high-level orientation only.

**Alternative rejected:** Raise the cap to ~300 chars. Rejected because steps+notes carry the detail already, and tightening the instruction saves Plan output tokens.

---

### Decision 3 — Drop `IssueSnapshot JSON:` block from implement prompt

`build_implement_prompt` renders the issue body twice:

1. As plain text: `Issue body:\n{snapshot.get("body", "")}`
2. As full JSON blob: `IssueSnapshot JSON:\n{_prompt_json(snapshot)}`

The JSON blob includes: issue body (duplicate), raw comments array (un-truncated), labels, assignees — none of which the implement agent needs. The checkbox criteria are already rendered separately below both sections.

The `IssueSnapshot JSON:` section is dropped from the implement prompt. The implement agent receives: issue title/number/repo, issue body (plain text), implementation plan, and checkbox criteria.

**Alternative rejected:** Project a subset of the snapshot to JSON. Rejected because the useful fields are already rendered in other sections.

---

### Decision 4 — Strip `comments` from Plan's snapshot injection

`build_plan_prompt` passes the full `IssueSnapshot JSON` to the Plan agent, including the raw `comments` array without truncation. Scout already processes those same comments (truncated to 10×400 chars) and distills relevant findings into `ScoutReport`. Plan receives the ScoutReport as context.

The Plan agent reading raw comment threads is redundant — ScoutReport already digests them. A `_prompt_plan_snapshot` helper strips the `comments` field before JSON-serialising the snapshot for the Plan prompt.

**Alternative rejected:** Apply Scout's truncation (10×400 chars) to Plan. Rejected because Plan has ScoutReport context, making comments redundant rather than just noisy.

---

### Decision 5 — Scout defaults to Claude Haiku

Scout is a read-only analysis stage (Glob/Grep/Read only) producing a capped JSON artifact (≤5 findings, ≤5 risks). This is the same class of work as CriteriaDedupe and EpicScout, both of which already default to Haiku.

`ClaudeStageSettings` for the `scout` stage defaults to Haiku. Users who need higher quality on complex repos can override via `[stages.scout.claude] model = "sonnet"` in `config.toml`.

**Risk acknowledged:** Scout findings seed the Plan prompt. Lower model quality on complex repos may produce thinner findings. Accepted because: (a) Scout already runs with `effort = "low"`, (b) the configurable override exists, (c) most issues are routine enough that Haiku handles analysis fine.

**Alternative rejected:** Keep Sonnet as default. Rejected because CriteriaDedupe and EpicScout precedent shows Haiku is appropriate for read-only JSON analysis stages.

---

## Consequences

- `_prompt_implementation_step` in `implement.py` gains `"notes"` key.
- `ImplementationPlan` skill: `approach_summary` description updated to "one terse sentence ≤150 chars."
- `build_implement_prompt` removes the `IssueSnapshot JSON:` section.
- `build_plan_prompt` gains `_prompt_plan_snapshot` helper that strips `comments`.
- `ClaudeStageSettings` default for `scout` stage changed to Haiku.
- `MAX_PROMPT_APPROACH_SUMMARY_CHARS = 150` in `implement.py` remains unchanged — now aligned with skill instruction.
