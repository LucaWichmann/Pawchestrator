// ==UserScript==
// @name         Pawchestrator
// @namespace    https://github.com/LucaWichmann/Pawchestrator
// @version      0.1.0
// @description  Agent orchestration controls for GitHub issues
// @match        https://github.com/*
// @run-at       document-idle
// @grant        GM_addStyle
// @grant        GM_deleteValue
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:38472";
  const STATUS_ID = "pawchestrator-status";
  const GRILL_STATUS_ID = "pawchestrator-grill-status";
  const START_ID = "pawchestrator-start";
  const GRILL_ID = "pawchestrator-grill";
  const POLL_INTERVAL_MS = 3000;
  const REINJECT_DEBOUNCE_MS = 100;
  const TOKEN_KEY = "pawchestrator_token";
  const PAW = "\uD83D\uDC3E";
  const FIRE = "\uD83D\uDD25";
  const OFFLINE_MESSAGE = "Pawchestrator not running - start with `pawchestrator serve`";

  let activePollPipeline = null;
  let activePollGrill = null;
  let activePathname = window.location.pathname;
  let reinjectTimer = null;

  GM_addStyle(`
    #${STATUS_ID},
    #${GRILL_STATUS_ID} {
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

    #${GRILL_STATUS_ID} {
      color: var(--fgColor-muted, #59636e);
    }

    #${START_ID},
    #${GRILL_ID} {
      white-space: nowrap;
    }

    #${START_ID}:disabled,
    #${GRILL_ID}:disabled {
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

  function isIssuePage() {
    const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
    const issueNumber = Number.parseInt(number, 10);
    return Boolean(owner)
      && Boolean(repo)
      && type === "issues"
      && String(issueNumber) === number
      && issueNumber > 0
      && !extra;
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

  function setGrillStatus(message) {
    const status = document.getElementById(GRILL_STATUS_ID);
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

  function summarizeGrillCompletion(run) {
    const report = run.grill_report || run.report || run.artifact || {};
    const criteria = Array.isArray(report.suggested_criteria)
      ? report.suggested_criteria.length
      : null;
    const questions = Array.isArray(report.unanswerable_questions)
      ? report.unanswerable_questions.length
      : null;
    const bodyUpdated = report.body_updated === true;

    if (criteria !== null || questions !== null || bodyUpdated) {
      const parts = [];
      if (bodyUpdated) {
        parts.push("Criteria appended");
      } else if (criteria === 0) {
        parts.push("No new criteria");
      } else if (criteria !== null) {
        parts.push(`${criteria} ${criteria === 1 ? "criterion" : "criteria"} suggested`);
      }

      if (questions !== null) {
        parts.push(questions === 0
          ? "No questions - issue ready"
          : `${questions} ${questions === 1 ? "question" : "questions"} posted`);
      }

      if (parts.length > 0) {
        return parts.join(" · ");
      }
    }

    return "Grill completed";
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

  function renderGrillStatus(run) {
    if (run.status === "grill_complete" || run.status === "completed") {
      setGrillStatus(summarizeGrillCompletion(run));
      return;
    }

    if (run.status === "grill_failed" || run.status === "failed") {
      setGrillStatus(summarizeError(run));
      return;
    }

    const stage = run.current_stage || "grill";
    const stageStatus = (run.status || "pending").replace(/^grill_/, "");
    setGrillStatus(`[${stage}] ${stageStatus}...`);
  }

  function stopPipelinePolling() {
    if (activePollPipeline) {
      window.clearInterval(activePollPipeline);
      activePollPipeline = null;
    }
  }

  function stopGrillPolling() {
    if (activePollGrill) {
      window.clearInterval(activePollGrill);
      activePollGrill = null;
    }
  }

  function rawRequestJson(path, options = {}) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: options.method || "GET",
        url: `${API_BASE}${path}`,
        headers: options.headers || {},
        data: options.body,
        timeout: 5000,
        onload: (response) => {
          if (response.status < 200 || response.status >= 300) {
            const error = new Error(`${options.label || "Request"} failed (${response.status})`);
            error.status = response.status;
            reject(error);
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

  async function getOrAcquireToken(statusSetter = setStatus) {
    const storedToken = await GM_getValue(TOKEN_KEY);
    if (storedToken) {
      return storedToken;
    }

    statusSetter("Pairing - approve in terminal...");
    const response = await rawRequestJson("/pair", {
      method: "POST",
      label: "Pairing request",
    });
    await GM_setValue(TOKEN_KEY, response.token);
    return response.token;
  }

  async function requestJson(path, options = {}) {
    if (path === "/health" || path === "/pair") {
      return rawRequestJson(path, options);
    }

    const statusSetter = options.statusSetter || setStatus;
    const token = await getOrAcquireToken(statusSetter);
    const headers = {
      ...(options.headers || {}),
      "X-Pawchestrator-Token": token,
    };

    try {
      return await rawRequestJson(path, { ...options, headers });
    } catch (error) {
      if (error.status !== 403) {
        throw error;
      }

      await GM_deleteValue(TOKEN_KEY);
      const freshToken = await getOrAcquireToken(statusSetter);
      return rawRequestJson(path, {
        ...options,
        headers: {
          ...(options.headers || {}),
          "X-Pawchestrator-Token": freshToken,
        },
      });
    }
  }

  async function fetchRun(runId) {
    return requestJson(`/runs/${runId}`, { label: "Status request" });
  }

  async function pollOnce(runId) {
    const run = await fetchRun(runId);
    renderStatus(run);
    if (run.status === "completed" || run.status === "failed") {
      stopPipelinePolling();
      const button = document.getElementById(START_ID);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function pollStatus(runId) {
    stopPipelinePolling();
    pollOnce(runId).catch((error) => setStatus(error.message));
    activePollPipeline = window.setInterval(() => {
      pollOnce(runId).catch((error) => {
        stopPipelinePolling();
        setStatus(error.message);
        const button = document.getElementById(START_ID);
        if (button) {
          button.disabled = false;
        }
      });
    }, POLL_INTERVAL_MS);
  }

  async function pollGrillOnce(runId) {
    const run = await fetchRun(runId);
    renderGrillStatus(run);
    if (
      run.status === "grill_complete"
      || run.status === "grill_failed"
      || run.status === "completed"
      || run.status === "failed"
    ) {
      stopGrillPolling();
      const button = document.getElementById(GRILL_ID);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function pollGrillStatus(runId) {
    stopGrillPolling();
    pollGrillOnce(runId).catch((error) => setGrillStatus(error.message));
    activePollGrill = window.setInterval(() => {
      pollGrillOnce(runId).catch((error) => {
        stopGrillPolling();
        setGrillStatus(error.message);
        const button = document.getElementById(GRILL_ID);
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
      await getOrAcquireToken();
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

  async function startGrill() {
    const button = document.getElementById(GRILL_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const issue = parseIssueReference();
      await getOrAcquireToken(setGrillStatus);
      setGrillStatus("[grill] starting...");
      const payload = await requestJson("/issue/grill", {
        method: "POST",
        label: "Grill request",
        statusSetter: setGrillStatus,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      pollGrillStatus(payload.run_id);
    } catch (error) {
      setGrillStatus(error.message);
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

  function createGrillButton() {
    const button = document.createElement("button");
    button.id = GRILL_ID;
    button.type = "button";
    button.dataset.component = "Button";
    button.dataset.testid = "pawchestrator-grill-button";
    button.dataset.loading = "false";
    button.dataset.noVisuals = "true";
    button.dataset.size = "medium";
    button.dataset.variant = "default";
    button.className = "prc-Button-ButtonBase-9n-Xk";
    button.addEventListener("click", startGrill);

    const content = document.createElement("span");
    content.dataset.component = "buttonContent";
    content.dataset.align = "center";
    content.className = "prc-Button-ButtonContent-Iohp5";

    const label = document.createElement("span");
    label.dataset.component = "text";
    label.className = "prc-Button-Label-FWkx3";
    label.textContent = `${FIRE} Grill Issue`;

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

  function createGrillStatus() {
    const status = document.createElement("div");
    status.id = GRILL_STATUS_ID;
    status.textContent = "";
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

  function removeHeaderAction() {
    [START_ID, STATUS_ID, GRILL_ID, GRILL_STATUS_ID].forEach((id) => {
      const element = document.getElementById(id);
      if (element) {
        element.remove();
      }
    });
  }

  function injectHeaderAction() {
    if (!isIssuePage()) {
      removeHeaderAction();
      return;
    }

    const actions = findHeaderActions();
    if (!actions) {
      return;
    }

    const existingButton = document.getElementById(START_ID);
    const existingStatus = document.getElementById(STATUS_ID);
    const existingGrillButton = document.getElementById(GRILL_ID);
    const existingGrillStatus = document.getElementById(GRILL_STATUS_ID);
    const button = existingButton && document.contains(existingButton) ? existingButton : createStartButton();
    const status = existingStatus && document.contains(existingStatus) ? existingStatus : createStatus();
    const grillButton = existingGrillButton && document.contains(existingGrillButton) ? existingGrillButton : createGrillButton();
    const grillStatus = existingGrillStatus && document.contains(existingGrillStatus) ? existingGrillStatus : createGrillStatus();
    const newIssueHost = findNewIssueHost(actions);
    let changed = !existingButton
      || !existingStatus
      || !existingGrillButton
      || !existingGrillStatus
      || button.parentElement !== actions
      || status.parentElement !== actions
      || grillButton.parentElement !== actions
      || grillStatus.parentElement !== actions;

    if (button.parentElement !== actions) {
      actions.insertBefore(button, newIssueHost);
      changed = true;
    }
    if (status.parentElement !== actions || status.previousElementSibling !== button) {
      actions.insertBefore(status, newIssueHost);
      changed = true;
    }
    if (grillButton.parentElement !== actions || grillButton.previousElementSibling !== status) {
      actions.insertBefore(grillButton, newIssueHost);
      changed = true;
    }
    if (grillStatus.parentElement !== actions || grillStatus.previousElementSibling !== grillButton) {
      actions.insertBefore(grillStatus, newIssueHost);
      changed = true;
    }

    button.hidden = false;
    status.hidden = false;
    grillButton.hidden = false;
    grillStatus.hidden = false;

    if (changed) {
      checkBackend();
    }
  }

  function scheduleHeaderInjection() {
    const pathnameChanged = activePathname !== window.location.pathname;
    if (pathnameChanged) {
      activePathname = window.location.pathname;
    }

    if (reinjectTimer) {
      window.clearTimeout(reinjectTimer);
    }

    reinjectTimer = window.setTimeout(() => {
      reinjectTimer = null;
      injectHeaderAction();
    }, pathnameChanged ? 0 : REINJECT_DEBOUNCE_MS);
  }

  function installNavigationHooks() {
    ["pushState", "replaceState"].forEach((method) => {
      const original = history[method];
      history[method] = function (...args) {
        const result = original.apply(this, args);
        scheduleHeaderInjection();
        return result;
      };
    });

    ["turbo:load", "turbo:render", "popstate"].forEach((eventName) => {
      window.addEventListener(eventName, scheduleHeaderInjection);
    });
  }

  injectHeaderAction();
  installNavigationHooks();

  const observer = new MutationObserver(() => {
    scheduleHeaderInjection();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
