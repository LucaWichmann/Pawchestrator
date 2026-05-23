// ==UserScript==
// @name         Pawchestrator
// @namespace    https://github.com/LucaWichmann/Pawchestrator
// @version      0.1.0
// @description  Agent orchestration controls for GitHub issues
// @match        https://github.com/*/*/issues/*
// @run-at       document-idle
// @grant        GM_addStyle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:38472";
  const STATUS_ID = "pawchestrator-status";
  const START_ID = "pawchestrator-start";
  const POLL_INTERVAL_MS = 3000;
  const PAW = "\uD83D\uDC3E";
  const OFFLINE_MESSAGE = "Pawchestrator not running - start with `pawchestrator serve`";

  let activePoll = null;

  GM_addStyle(`
    #${STATUS_ID} {
      align-items: center;
      color: var(--fgColor-default, #24292f);
      display: inline-flex;
      font-size: 12px;
      line-height: 20px;
      margin: 0 8px;
      min-height: 18px;
      max-width: 280px;
      overflow-wrap: anywhere;
    }

    #${START_ID} {
      white-space: nowrap;
    }

    #${START_ID}:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }

    #${STATUS_ID} a {
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

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    return rect.width > 0
      && rect.height > 0
      && window.getComputedStyle(element).visibility !== "hidden";
  }

  function uniqueElements(elements) {
    return [...new Set(elements.filter(Boolean))];
  }

  function findHeaderActions() {
    const selectors = [
      '[data-testid="issue-header"] [data-component="PH_Actions"] .HeaderMenu-module__menuActionsContainer__K0Mga',
      '[data-testid="issue-header"] [data-component="PH_Actions"] [class*="HeaderMenu-module__menuActionsContainer"]',
      '[data-testid="issue-header"] .HeaderMenu-module__menuActionsContainer__K0Mga',
      '[data-testid="issue-header"] [class*="HeaderMenu-module__menuActionsContainer"]',
    ];
    const candidates = uniqueElements(selectors.flatMap((selector) => {
      return Array.from(document.querySelectorAll(selector));
    }));

    return candidates.find(isVisible) || candidates[0] || null;
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

  function requestJson(path, options = {}) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: options.method || "GET",
        url: `${API_BASE}${path}`,
        headers: options.headers || {},
        data: options.body,
        timeout: 5000,
        onload: (response) => {
          if (response.status < 200 || response.status >= 300) {
            reject(new Error(`${options.label || "Request"} failed (${response.status})`));
            return;
          }

          if (!response.responseText) {
            resolve(null);
            return;
          }

          try {
            resolve(JSON.parse(response.responseText));
          } catch (error) {
            reject(new Error(`${options.label || "Request"} returned invalid JSON: ${error.message}`));
          }
        },
        onerror: () => reject(new Error(OFFLINE_MESSAGE)),
        ontimeout: () => reject(new Error(OFFLINE_MESSAGE)),
      });
    });
  }

  async function fetchRun(runId) {
    return requestJson(`/runs/${runId}`, { label: "Status request" });
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
      await requestJson("/health", { label: "Health check" });
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
      const payload = await requestJson("/issue/start", {
        method: "POST",
        label: "Start request",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      pollStatus(payload.run_id);
    } catch (error) {
      setStatus(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function createStartButton() {
    const button = document.createElement("button");
    button.id = START_ID;
    button.type = "button";
    button.dataset.component = "Button";
    button.dataset.testid = "pawchestrator-work-button";
    button.dataset.loading = "false";
    button.dataset.noVisuals = "true";
    button.dataset.size = "medium";
    button.dataset.variant = "default";
    button.className = "prc-Button-ButtonBase-9n-Xk";
    button.addEventListener("click", startRun);

    const content = document.createElement("span");
    content.dataset.component = "buttonContent";
    content.dataset.align = "center";
    content.className = "prc-Button-ButtonContent-Iohp5";

    const label = document.createElement("span");
    label.dataset.component = "text";
    label.className = "prc-Button-Label-FWkx3";
    label.textContent = `${PAW} Work on this issue`;

    content.append(label);
    button.append(content);
    return button;
  }

  function createStatus() {
    const status = document.createElement("div");
    status.id = STATUS_ID;
    status.textContent = "Checking backend...";
    return status;
  }

  function findNewIssueHost(actions) {
    const newIssue = actions.querySelector('a[href$="/issues/new/choose"], a[href*="/issues/new"]');
    if (!newIssue) {
      return null;
    }

    const parent = newIssue.parentElement;
    if (parent && parent.parentElement === actions) {
      return parent;
    }
    return newIssue;
  }

  function injectHeaderAction() {
    const actions = findHeaderActions();
    if (!actions) {
      return;
    }

    const existingButton = document.getElementById(START_ID);
    const existingStatus = document.getElementById(STATUS_ID);
    const button = existingButton || createStartButton();
    const status = existingStatus || createStatus();
    const newIssueHost = findNewIssueHost(actions);
    let changed = !existingButton || !existingStatus;

    if (button.parentElement !== actions) {
      actions.insertBefore(button, newIssueHost);
      changed = true;
    }
    if (status.parentElement !== actions || status.previousElementSibling !== button) {
      actions.insertBefore(status, newIssueHost);
      changed = true;
    }

    button.hidden = false;
    status.hidden = false;

    if (changed) {
      checkBackend();
    }
  }

  injectHeaderAction();

  const observer = new MutationObserver(() => {
    injectHeaderAction();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
