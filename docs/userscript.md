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
5. The userscript calls `POST /issue/start` and polls `GET /runs/{run_id}/status` for progress updates.

## Development

Install the Node toolchain with Node 20 LTS:

```powershell
npm install
```

Rebuild the userscript on source changes:

```powershell
npm run dev
```

Create the committed Tampermonkey artifact:

```powershell
npm run build
```

Check and format the userscript source:

```powershell
npm run lint
npm run format
```

## Notes

- `/health` stays open for offline checks. All other authenticated browser calls require the pairing token.
- To revoke all browser tokens: `uv run pawchestrator sessions clear`.
