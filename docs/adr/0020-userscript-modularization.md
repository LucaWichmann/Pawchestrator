# ADR 0020 — Userscript modularization with Vite + TypeScript

## Status

Accepted (grilled 2026-05-29)

## Context

`Pawchestrator.user.js` is a 2,534-line single-file IIFE. All logic — constants, styles, DOM rendering, API calls, polling, navigation hooks — lives in one scope. No module boundaries, no type safety, no linting. Every feature addition grows the file further and makes the codebase harder to navigate and test.

The goal is to split the monolith into a multi-file TypeScript ES module codebase that bundles back to a single `.user.js` for Tampermonkey distribution. No existing users are installed on the old file path, so the output URL can change without breakage.

---

## Decisions

### Decision 1 — Build tooling: Vite + `vite-plugin-monkey`

Vite with `vite-plugin-monkey` is the standard toolchain for Tampermonkey userscript development. It handles:
- Tampermonkey metadata block injection from `vite.config.ts`
- `GM_*` globals declaration (prevents Vite from trying to bundle Tampermonkey APIs)
- IIFE output format required by Tampermonkey
- TypeScript compilation natively (no extra config beyond `tsconfig.json`)

No CI/CD. Build is manual: `npm run build`. Resulting artifact is committed and pushed.

---

### Decision 2 — Output path: `dist/pawchestrator.user.js`

Build output goes to `dist/pawchestrator.user.js`. The old `Pawchestrator.user.js` at the repo root becomes dead and is deleted once `src/` is established.

`dist/` is **not** added to `.gitignore` — the file must be committed and pushed so GitHub's raw URL serves it for Tampermonkey auto-update.

`@downloadURL` and `@updateURL` in `vite.config.ts` point to:
```
https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/dist/pawchestrator.user.js
```

---

### Decision 3 — Language: TypeScript, `strict: false` initially

TypeScript is used from day one. `@types/tampermonkey` provides type declarations for all `GM_*` APIs.

`strict: false` is set in `tsconfig.json` for the initial migration. The primary goal of this sprint is structural refactoring — correct module boundaries. Full strict type safety is a follow-up concern and must be enabled incrementally per module once the structure is stable. A `// TODO: enable strict incrementally` comment is added to `tsconfig.json`.

---

### Decision 4 — Module structure

18 files across a `src/` tree with three subfolders:

```
src/
├── main.ts                  # Entry: injectControls() + installNavigationHooks() + MutationObserver
├── constants.ts             # All const IDs, Sets, intervals, emoji strings
├── styles.ts                # GM_addStyle(...) call — single export
├── router.ts                # parseIssueReference, parsePrReference, isIssuePage, isPrPage
├── api.ts                   # requestJson, GM_xmlhttpRequest wrapper, fetchIssueStatus, getOrAcquireToken
├── state.ts                 # Module-level mutable vars: activePoll, latestIssueStatus, panelExpandedByUser, etc.
├── poll.ts                  # startIssueStatusPolling, stopIssueStatusPolling, startPrStatusPolling, stopPrStatusPolling
├── navigation.ts            # installNavigationHooks, scheduleInjection
├── summarize.ts             # summarizeRun, summarizeError, isRunDone, summarizeGrillCompletion, currentRun
├── panel/
│   ├── common.ts            # setPanelSummary, setPanelStatus, setPanelExpanded, activePanel, findIssueBodyContainer
│   ├── issue.ts             # Issue panel DOM construction and renderStatus
│   ├── pr.ts                # PR panel DOM construction and renderPrStatus
│   └── confirm.ts           # showConfirmDialog
├── render/
│   ├── timeline.ts          # renderTimeline, renderStage, step DOM helpers
│   ├── grill.ts             # renderGrillSection, summarizeGrillCompletion, updateGrillButton
│   ├── epic.ts              # renderEpicSection, epicSubRuns, epicSummaryRun, epicStatus
│   └── plan-approval.ts     # renderPlanApprovalSection (awaiting_plan_approval state)
└── actions/
    ├── run.ts               # startRun, confirmEpicStart, epicFromStartResponse
    ├── grill.ts             # startGrill
    ├── epic-architect.ts    # startEpicArchitect
    └── review.ts            # startReview, startRepair, startCreateIssues
```

**Constraint:** `src/state.ts` has zero imports. It is a pure mutable data store. No module may import `state.ts` in a way that creates a circular dependency.

---

### Decision 5 — Shared state: singleton module

All mutable runtime state (`activePoll`, `activePrPoll`, `latestIssueStatus`, `panelExpandedByUser`, etc.) lives in `src/state.ts` as exported `let` bindings. Modules that need to read or write state import from `src/state.ts` directly.

Dependency injection was considered and rejected. There is always exactly one userscript instance per page; the singleton is correct. Testability is not a goal for the UI layer — no test harness exists for `GM_*` APIs.

---

### Decision 6 — No minification, no source maps

`build.minify: false` in `vite.config.ts`. Userscripts are inspected by users who want to know what runs in their browser. Readable output builds trust. Unminified diffs are also easier to review when the build artifact is committed.

Source maps are disabled. Inline source maps would nearly double the file size. External source maps are not loaded by Tampermonkey. With no minification the bundled output is already readable enough for debugging.

---

### Decision 7 — Dev workflow: `vite build --watch`

`npm run dev` runs `vite build --watch`. The bundle is rebuilt on every file save; the developer reloads the GitHub page to pick up changes.

`vite-plugin-monkey`'s dev server mode (localhost script injection + hot reload) was considered and rejected. The userscript initializes on page load (`injectControls()` runs once at `document-idle`). Hot module replacement provides no benefit when the full page must be reloaded to re-run initialization.

---

### Decision 8 — Linting and formatting

ESLint with `typescript-eslint` + Prettier.

npm scripts:
- `npm run lint` — `eslint src`
- `npm run format` — `prettier --write src`

`dist/pawchestrator.user.js` is excluded from both linters. `node_modules/` is excluded by default.

No pre-commit hook added in this sprint — can be added later via `lint-staged` if desired.

---

### Decision 9 — `.gitignore` additions

```
node_modules/
*.tsbuildinfo
```

`dist/` is intentionally absent from `.gitignore`. Node 20 LTS is pinned in `.nvmrc`.

---

## Consequences

- `Pawchestrator.user.js` at the repo root is deleted. The source of truth is `src/`. The distribution artifact is `dist/pawchestrator.user.js`.
- `@downloadURL` and `@updateURL` change. No existing users are affected.
- **`docs/userscript.md` must be updated** as part of the implementation sprint: update the install link to `dist/pawchestrator.user.js`, update the polling endpoint reference, and add a `## Development` section covering `npm install`, `npm run dev`, `npm run build`, `npm run lint`, `npm run format`.
- **`README.md` install badge URL must be updated** from `Pawchestrator.user.js` to `dist/pawchestrator.user.js` as part of the implementation sprint — not before, since the file does not exist at that path until the first build is committed. The line to update is the `href` on the install badge `<a>` tag in the Quick start section.
- TypeScript compilation is part of the build step. Contributors need Node 20 LTS and `npm install` before editing the userscript.
- Future: enable `strict: true` per module once module structure is stable. Future: add `lint-staged` pre-commit hook.
