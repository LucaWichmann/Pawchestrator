// ==UserScript==
// @name         Pawchestrator
// @namespace    https://github.com/LucaWichmann/Pawchestrator
// @version      0.1.0
// @description  Agent orchestration controls for GitHub issues
// @match        https://github.com/*/*/issues/*
// @run-at       document-idle
// @grant        GM_addStyle
// ==/UserScript==

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:38472";
  const PANEL_ID = "pawchestrator-panel";
  const STATUS_ID = "pawchestrator-status";
  const START_ID = "pawchestrator-start";
  const POLL_INTERVAL_MS = 3000;
  const PAW = "\uD83D\uDC3E";
  const OFFLINE_MESSAGE = "Pawchestrator not running \u2014 start with `pawchestrator serve`";

  let activePoll = null;

  GM_addStyle(`
    #${PANEL_ID} {
      border-top: 1px solid var(--borderColor-default, #d0d7de);
      margin-top: 16px;
      padding-top: 16px;
      color: var(--fgColor-default, #24292f);
      font-size: 12px;
    }

    #${PANEL_ID} strong {
      display: block;
      font-size: 14px;
      margin-bottom: 8px;
    }

    #${STATUS_ID} {
      margin-bottom: 8px;
      min-height: 18px;
      overflow-wrap: anywhere;
    }

    #${START_ID} {
      align-items: center;
      background-color: var(--button-primary-bgColor-rest, #1f883d);
      border: 1px solid var(--button-primary-borderColor-rest, rgba(31, 35, 40, 0.15));
      border-radius: 6px;
      color: var(--button-primary-fgColor-rest, #ffffff);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-weight: 600;
      justify-content: center;
      line-height: 20px;
      padding: 5px 12px;
      text-align: center;
      width: 100%;
    }

    #${START_ID}:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }

    #${PANEL_ID} a {
      color: var(--fgColor-accent, #0969da);
    }
  `);

  function parseIssueReference() {
    const [, owner, repo, type, number] = window.location.pathname.split("/");
    if (!owner || !repo || type !== "issues" || !number) {
      throw new Error("Not a GitHub issue page");
    }

    const issueNumber = Number.parseInt(number, 10);
    if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
      throw new Error("Invalid GitHub issue number");
    }

    return { owner, repo, number: issueNumber };
  }

  function findSidebar() {
    return document.querySelector('[data-testid="sidebar"]')
      || document.querySelector(".Layout-sidebar");
  }

  function setStatus(message) {
    const status = document.getElementById(STATUS_ID);
    if (status) {
      status.textContent = message;
    }
  }

  function setStatusLink(message, href) {
    const status = document.getElementById(STATUS_ID);
    if (!status) {
      return;
    }

    status.textContent = "";
    status.append(document.createTextNode(`${message} `));
    const link = document.createElement("a");
    link.href = href;
    link.textContent = href;
    status.append(link);
  }

  function summarizeError(run) {
    const failedStage = (run.stages || []).find((stage) => stage.status === "failed");
    if (!failedStage) {
      return "Run failed";
    }

    const stageName = failedStage.stage_name || failedStage.name || run.current_stage || "unknown";
    const error = failedStage.error ? `: ${failedStage.error}` : "";
    return `[${stageName}] failed${error}`;
  }

  function renderStatus(run) {
    if (run.status === "completed") {
      if (run.pr_url) {
        setStatusLink("Draft PR ready:", run.pr_url);
      } else {
        setStatus("Run completed");
      }
      return;
    }

    if (run.status === "failed") {
      setStatus(summarizeError(run));
      return;
    }

    const stage = run.current_stage || "queued";
    const stageStatus = run.status || "pending";
    setStatus(`[${stage}] ${stageStatus}...`);
  }

  function stopPolling() {
    if (activePoll) {
      window.clearInterval(activePoll);
      activePoll = null;
    }
  }

  async function fetchRun(runId) {
    const response = await fetch(`${API_BASE}/runs/${runId}`);
    if (!response.ok) {
      throw new Error(`Status request failed (${response.status})`);
    }
    return response.json();
  }

  async function pollOnce(runId) {
    const run = await fetchRun(runId);
    renderStatus(run);
    if (run.status === "completed" || run.status === "failed") {
      stopPolling();
      const button = document.getElementById(START_ID);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function pollStatus(runId) {
    stopPolling();
    pollOnce(runId).catch((error) => setStatus(error.message));
    activePoll = window.setInterval(() => {
      pollOnce(runId).catch((error) => {
        stopPolling();
        setStatus(error.message);
        const button = document.getElementById(START_ID);
        if (button) {
          button.disabled = false;
        }
      });
    }, POLL_INTERVAL_MS);
  }

  async function checkBackend() {
    try {
      const response = await fetch(`${API_BASE}/health`);
      if (!response.ok) {
        throw new Error(`Health check failed (${response.status})`);
      }
      setStatus("Ready");
    } catch (_error) {
      setStatus(OFFLINE_MESSAGE);
    }
  }

  async function startRun() {
    const button = document.getElementById(START_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const issue = parseIssueReference();
      setStatus("[snapshot] starting...");
      const response = await fetch(`${API_BASE}/issue/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });

      if (!response.ok) {
        throw new Error(`Start request failed (${response.status})`);
      }

      const payload = await response.json();
      pollStatus(payload.run_id);
    } catch (error) {
      setStatus(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function injectPanel() {
    if (document.getElementById(PANEL_ID)) {
      return;
    }

    const sidebar = findSidebar();
    if (!sidebar) {
      return;
    }

    const panel = document.createElement("div");
    panel.id = PANEL_ID;

    const title = document.createElement("strong");
    title.textContent = `${PAW} Pawchestrator`;

    const status = document.createElement("div");
    status.id = STATUS_ID;
    status.textContent = "Checking backend...";

    const button = document.createElement("button");
    button.id = START_ID;
    button.type = "button";
    button.textContent = `${PAW} Work on this issue`;
    button.addEventListener("click", startRun);

    panel.append(title, status, button);
    sidebar.append(panel);
    checkBackend();
  }

  injectPanel();

  const observer = new MutationObserver(() => {
    injectPanel();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
