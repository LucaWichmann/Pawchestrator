# Userscript pairing

The browser flow uses `dist/pawchestrator.user.js` to add controls to GitHub issue pages and call the local backend.

## Setup

1. Start the backend:

   ```powershell
   uv run pawchestrator serve
   ```

2. Register the local repository clone:

   ```powershell
   uv run pawchestrator repo add C:\src\REPO
   ```

3. Install [`dist/pawchestrator.user.js`](https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/dist/pawchestrator.user.js) in Tampermonkey.

## First run

1. Open a GitHub issue page and click the `Work on this issue` button in the issue header.
2. The userscript calls `POST /pair`.
3. The backend prompts in the terminal — press Enter to approve or Ctrl+C to deny.
4. Pawchestrator stores the token in Tampermonkey and sends it on later requests as `X-Pawchestrator-Token`.
5. The userscript calls `POST /issue/start` and polls `GET /issue/{owner}/{repo}/{number}/status` for progress updates.

## Development

### Prerequisites

Node 20 LTS is required. The version is pinned in `.nvmrc` — run `nvm use` if you use nvm, or install Node 20 manually.

### Source layout

The userscript source lives in `src/` as TypeScript modules, built with Vite and [vite-plugin-monkey](https://github.com/lisonge/vite-plugin-monkey):

```
src/
├── main.ts          entry point
├── api.ts           GM_xmlhttpRequest wrapper, token acquisition
├── constants.ts     IDs, sets, intervals, emoji
├── controls.ts      control injection and event binding
├── navigation.ts    GitHub SPA navigation hooks
├── poll.ts          issue/PR status polling
├── router.ts        page type detection, reference parsing
├── state.ts         mutable singleton (activePoll, latestIssueStatus, …)
├── styles.ts        GM_addStyle CSS
├── summarize.ts     run/error summarization
├── panel/           issue, PR, and confirm-dialog DOM
├── render/          timeline, grill, epic, plan-approval renderers
└── actions/         run, grill, epic-architect, review action handlers
```

### Commands

Install the toolchain:

```powershell
npm install
```

Watch and rebuild on save (reload the GitHub page after each rebuild — no HMR):

```powershell
npm run dev
```

Produce the committed install artifact:

```powershell
npm run build
```

Check and format source:

```powershell
npm run lint
npm run format
```

### Distribution artifact

`npm run build` writes `dist/pawchestrator.user.js`. This file is committed to the repo — the Tampermonkey `@updateURL` and `@downloadURL` metadata point at it on the `main` branch. After making changes, run `npm run build` and commit `dist/pawchestrator.user.js` alongside the source diff.

See [ADR 0020](adr/0020-userscript-modularization.md) for the full build rationale.

## Notes

- `/health` stays open for offline checks. All other authenticated browser calls require the pairing token.
- To revoke all browser tokens: `uv run pawchestrator sessions clear`.
