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
  const PANEL_ID = "pawchestrator-panel";
  const START_ID = "pawchestrator-start";
  const GRILL_ID = "pawchestrator-grill";
  const POLL_INTERVAL_MS = 3000;
  const REINJECT_DEBOUNCE_MS = 100;
  const TOKEN_KEY = "pawchestrator_token";
  const PIPELINE_STAGES = ["snapshot", "scout", "plan", "implement", "verify", "pr"];
  const PAW = "\uD83D\uDC3E";
  const FIRE = "\uD83D\uDD25";
  const WARNING = "\u26A0";
  const OFFLINE_MESSAGE = "Pawchestrator not running \u2014 start with `pawchestrator serve`";
  const RUN_DONE = new Set(["completed", "failed", "grill_complete", "grill_failed"]);
  const PIPELINE_ACTIVE = new Set([
    "snapshot_running",
    "snapshot_complete",
    "scout_running",
    "scout_complete",
    "plan_running",
    "plan_complete",
    "implement_running",
    "implement_complete",
    "verify_running",
    "verify_complete",
    "verify_skipped",
    "pr_running",
    "pr_complete",
    "completed",
  ]);
  const STAGE_DONE = new Set(["complete", "completed", "skipped"]);

  let activePoll = null;
  let activePathname = window.location.pathname;
  let panelExpandedByUser = null;
  let lastPipelineExpansionKey = null;
  let reinjectTimer = null;

  GM_addStyle(`
    #${START_ID},
    #${GRILL_ID} {
      white-space: nowrap;
    }

    #${START_ID}:disabled,
    #${GRILL_ID}:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }

    #${PANEL_ID} {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font-size: 13px;
      line-height: 20px;
      margin: 8px 0 16px;
    }

    #${PANEL_ID} .pawchestrator-panel-bar {
      align-items: center;
      display: flex;
      gap: 8px;
      min-height: 38px;
      padding: 8px 12px;
    }

    #${PANEL_ID} .pawchestrator-panel-toggle {
      align-items: center;
      background: transparent;
      border: 0;
      color: var(--fgColor-muted, #59636e);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      height: 24px;
      justify-content: center;
      padding: 0;
      width: 24px;
    }

    #${PANEL_ID} .pawchestrator-panel-summary {
      flex: 1;
      min-width: 0;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-panel-body {
      border-top: 1px solid var(--borderColor-default, #d0d7de);
      display: none;
      padding: 10px 12px 12px;
    }

    #${PANEL_ID}[data-expanded="true"] .pawchestrator-panel-body {
      display: block;
    }

    #${PANEL_ID} .pawchestrator-readiness-row {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
    }

    #${PANEL_ID} .pawchestrator-readiness-item {
      color: var(--fgColor-muted, #59636e);
      white-space: nowrap;
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="true"] {
      color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="false"] {
      color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-run-line {
      color: var(--fgColor-muted, #59636e);
      margin-top: 8px;
    }

    #${PANEL_ID} .pawchestrator-pipeline {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-pipeline-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} .pawchestrator-timeline {
      align-items: flex-start;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(6, minmax(72px, 1fr));
      overflow-x: auto;
      padding-bottom: 2px;
    }

    #${PANEL_ID} .pawchestrator-step {
      color: var(--fgColor-muted, #59636e);
      min-width: 72px;
      position: relative;
    }

    #${PANEL_ID} .pawchestrator-step:not(:last-child)::after {
      background: var(--borderColor-muted, #d8dee4);
      content: "";
      height: 1px;
      left: 23px;
      position: absolute;
      right: -9px;
      top: 8px;
    }

    #${PANEL_ID} .pawchestrator-step[data-active="true"] {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-step-label {
      display: block;
      font-size: 12px;
      line-height: 16px;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-step-indicator {
      align-items: center;
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-muted, #d8dee4);
      border-radius: 50%;
      display: inline-flex;
      font-size: 11px;
      height: 18px;
      justify-content: center;
      position: relative;
      width: 18px;
      z-index: 1;
    }

    #${PANEL_ID} .pawchestrator-step[data-status="pending"] .pawchestrator-step-indicator {
      color: var(--fgColor-muted, #59636e);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="running"] .pawchestrator-step-indicator {
      animation: pawchestrator-spin 0.8s linear infinite;
      border-color: var(--fgColor-accent, #0969da);
      border-right-color: transparent;
      color: transparent;
    }

    #${PANEL_ID} .pawchestrator-step[data-status="done"] .pawchestrator-step-indicator {
      background: var(--bgColor-success-emphasis, #1a7f37);
      border-color: var(--bgColor-success-emphasis, #1a7f37);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="failed"] .pawchestrator-step-indicator {
      background: var(--bgColor-danger-emphasis, #cf222e);
      border-color: var(--bgColor-danger-emphasis, #cf222e);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-warnings {
      margin-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-warnings summary {
      color: var(--fgColor-attention, #9a6700);
      cursor: pointer;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-warnings-list {
      color: var(--fgColor-muted, #59636e);
      margin: 6px 0 0;
      padding-left: 18px;
    }

    @keyframes pawchestrator-spin {
      to {
        transform: rotate(360deg);
      }
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

  function findIssueBodyContainer() {
    const selectors = [
      ".IssueBody-module__outerContainer__ULNTb",
      '[class*="IssueBody-module__outerContainer"]',
    ];
    return selectors
      .map((selector) => document.querySelector(selector))
      .find(Boolean) || null;
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

  function setPanelSummary(message) {
    const panel = document.getElementById(PANEL_ID);
    const summary = panel && panel.querySelector(".pawchestrator-panel-summary");
    if (summary) {
      summary.textContent = `${PAW} Pawchestrator \u00B7 ${message}`;
    }
  }

  function setPanelExpanded(expanded) {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) {
      return;
    }
    panel.dataset.expanded = String(expanded);
    const toggle = panel.querySelector(".pawchestrator-panel-toggle");
    if (toggle) {
      toggle.textContent = expanded ? "\u25BE" : "\u25B8";
      toggle.setAttribute("aria-expanded", String(expanded));
    }
  }

  function shouldAutoExpand(status) {
    return Boolean(status && (status.pipeline || status.grill));
  }

  function isPipelineVisible(pipeline) {
    return Boolean(pipeline && (PIPELINE_ACTIVE.has(pipeline.status) || pipeline.status === "completed" || pipeline.status === "failed"));
  }

  function maybeAutoExpandForPipeline(status) {
    const pipeline = status && status.pipeline;
    if (!pipeline) {
      lastPipelineExpansionKey = null;
      return;
    }

    const key = `${pipeline.run_id || ""}:${pipeline.status || ""}:${pipeline.current_stage || ""}`;
    const shouldExpand = isPipelineVisible(pipeline) && key !== lastPipelineExpansionKey;
    lastPipelineExpansionKey = key;
    if (shouldExpand) {
      setPanelExpanded(true);
    }
  }

  function currentRun(status) {
    if (!status) {
      return null;
    }
    const runs = [status.pipeline, status.grill].filter(Boolean);
    return runs.find((run) => !RUN_DONE.has(run.status)) || runs[0] || null;
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
        return parts.join(" \u00B7 ");
      }
    }

    return "Grill completed";
  }

  function summarizeRun(run) {
    if (!run) {
      return "Ready";
    }
    if (run.status === "completed") {
      return run.pr_url ? "Draft PR ready" : "Run completed";
    }
    if (run.status === "failed") {
      return summarizeError(run);
    }
    if (run.status === "grill_complete") {
      return summarizeGrillCompletion(run);
    }
    if (run.status === "grill_failed") {
      return summarizeError(run);
    }

    const stage = run.current_stage || (run.workflow_type === "grill" ? "grill" : "queued");
    const stageStatus = (run.status || "pending").replace(/^grill_/, "");
    return `[${stage}] ${stageStatus}...`;
  }

  function renderReadinessItem(parent, label, ready) {
    const item = document.createElement("span");
    item.className = "pawchestrator-readiness-item";
    item.dataset.ready = String(Boolean(ready));
    item.textContent = `${ready ? "\u2713" : "\u00D7"} ${label}`;
    parent.append(item);
  }

  function stageName(stage) {
    return String(stage?.stage_name || stage?.name || "");
  }

  function stageStatus(stage) {
    return String(stage?.status || "pending").replace(/^[^_]+_/, "");
  }

  function normalizeStepStatus(stage, isAfterActive) {
    if (isAfterActive || !stage) {
      return "pending";
    }

    const status = stageStatus(stage);
    if (status === "running") {
      return "running";
    }
    if (status === "failed") {
      return "failed";
    }
    if (STAGE_DONE.has(status)) {
      return "done";
    }
    return "pending";
  }

  function collapseStages(stages) {
    const rows = Array.isArray(stages) ? stages : [];
    const byName = new Map();
    PIPELINE_STAGES.forEach((name) => byName.set(name, []));
    rows.forEach((stage) => {
      const name = stageName(stage);
      if (byName.has(name)) {
        byName.get(name).push(stage);
      }
    });

    const repairCount = Math.max(0, (byName.get("implement") || []).length - 1);
    const failedVerifyCount = (byName.get("verify") || [])
      .filter((stage) => stageStatus(stage) === "failed")
      .length;
    const repairTotal = Math.max(repairCount, failedVerifyCount);

    return PIPELINE_STAGES.map((name) => {
      const matching = byName.get(name) || [];
      const stage = matching[matching.length - 1] || { stage_name: name, status: "pending" };
      const label = name === "implement" && repairCount > 0
        ? `${name} (repair ${repairCount}/${repairTotal || repairCount})`
        : name;
      return { name, label, stage };
    });
  }

  function activeStageIndex(pipeline, steps) {
    const failedIndex = steps.findIndex((step) => stageStatus(step.stage) === "failed");
    if (failedIndex >= 0) {
      return failedIndex;
    }

    const current = String(pipeline.current_stage || "");
    const currentIndex = steps.findIndex((step) => step.name === current);
    if (currentIndex >= 0) {
      return currentIndex;
    }

    const runningIndex = steps.findIndex((step) => stageStatus(step.stage) === "running");
    if (runningIndex >= 0) {
      return runningIndex;
    }

    if (pipeline.status === "completed") {
      return PIPELINE_STAGES.length - 1;
    }

    return -1;
  }

  function renderPipeline(parent, pipeline) {
    if (!pipeline) {
      return;
    }

    const section = document.createElement("section");
    section.className = "pawchestrator-pipeline";

    const title = document.createElement("div");
    title.className = "pawchestrator-pipeline-title";
    title.textContent = "Pipeline";
    if (pipeline.status === "completed" && pipeline.pr_url) {
      title.append(document.createTextNode(" \u00B7 "));
      const link = document.createElement("a");
      link.href = pipeline.pr_url;
      link.textContent = "PR";
      title.append(link);
    }
    section.append(title);

    const steps = collapseStages(pipeline.stages);
    const activeIndex = activeStageIndex(pipeline, steps);
    const timeline = document.createElement("div");
    timeline.className = "pawchestrator-timeline";
    steps.forEach((step, index) => {
      const status = normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
      const item = document.createElement("div");
      item.className = "pawchestrator-step";
      item.dataset.status = status;
      item.dataset.active = String(index === activeIndex && pipeline.status !== "completed");

      const indicator = document.createElement("span");
      indicator.className = "pawchestrator-step-indicator";
      indicator.textContent = status === "done" ? "\u2713" : status === "failed" ? "\u00D7" : "\u2022";

      const label = document.createElement("span");
      label.className = "pawchestrator-step-label";
      label.textContent = step.label;

      item.append(indicator, label);
      timeline.append(item);
    });
    section.append(timeline);

    const warnings = Array.isArray(pipeline.warnings) ? pipeline.warnings : [];
    if (warnings.length > 0) {
      const details = document.createElement("details");
      details.className = "pawchestrator-warnings";
      const summary = document.createElement("summary");
      summary.textContent = `${WARNING} Warnings`;
      details.append(summary);

      const list = document.createElement("ul");
      list.className = "pawchestrator-warnings-list";
      warnings.forEach((warning) => {
        const item = document.createElement("li");
        const stage = warning.stage_name ? `[${warning.stage_name}] ` : "";
        const code = warning.code ? `${warning.code}: ` : "";
        item.textContent = `${stage}${code}${warning.message || "Warning"}`;
        list.append(item);
      });
      details.append(list);
      section.append(details);
    }

    parent.append(section);
  }

  function renderStatus(status) {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) {
      return;
    }

    const run = currentRun(status);
    setPanelSummary(summarizeRun(run));

    if (panelExpandedByUser === null) {
      setPanelExpanded(shouldAutoExpand(status));
    }
    maybeAutoExpandForPipeline(status);

    const body = panel.querySelector(".pawchestrator-panel-body");
    if (!body) {
      return;
    }
    body.textContent = "";

    const readiness = document.createElement("div");
    readiness.className = "pawchestrator-readiness-row";
    renderReadinessItem(readiness, "Backend connected", status.backend_connected);
    renderReadinessItem(readiness, "Repo registered", status.repo_registered);
    renderReadinessItem(readiness, "Claude available", status.runners?.claude?.available);
    renderReadinessItem(readiness, "Codex available", status.runners?.codex?.available);
    body.append(readiness);

    if (run) {
      const line = document.createElement("div");
      line.className = "pawchestrator-run-line";
      line.textContent = `${run.workflow_type || "pipeline"}: ${summarizeRun(run)}`;
      if (run.status === "completed" && run.pr_url) {
        line.append(document.createTextNode(" "));
        const link = document.createElement("a");
        link.href = run.pr_url;
        link.textContent = run.pr_url;
        line.append(link);
      }
      body.append(line);
    }

    renderPipeline(body, status.pipeline);
  }

  function renderOffline() {
    setPanelSummary(OFFLINE_MESSAGE);
    const panel = document.getElementById(PANEL_ID);
    const body = panel && panel.querySelector(".pawchestrator-panel-body");
    if (!body) {
      return;
    }
    body.textContent = "";
    const readiness = document.createElement("div");
    readiness.className = "pawchestrator-readiness-row";
    renderReadinessItem(readiness, "Backend connected", false);
    renderReadinessItem(readiness, "Repo registered", false);
    renderReadinessItem(readiness, "Claude available", false);
    renderReadinessItem(readiness, "Codex available", false);
    body.append(readiness);
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

  async function getOrAcquireToken(statusSetter = setPanelSummary) {
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

    const statusSetter = options.statusSetter || setPanelSummary;
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

  async function fetchIssueStatus(issue = parseIssueReference()) {
    return requestJson(`/issue/${issue.owner}/${issue.repo}/${issue.number}/status`, {
      label: "Issue status request",
    });
  }

  async function pollIssueStatusOnce() {
    const issue = parseIssueReference();
    const status = await fetchIssueStatus(issue);
    renderStatus(status);
    const run = currentRun(status);
    const running = run && !RUN_DONE.has(run.status);
    document.getElementById(START_ID)?.toggleAttribute("disabled", Boolean(status.pipeline && !RUN_DONE.has(status.pipeline.status)));
    document.getElementById(GRILL_ID)?.toggleAttribute("disabled", Boolean(status.grill && !RUN_DONE.has(status.grill.status)));
    return running;
  }

  function startIssueStatusPolling() {
    stopIssueStatusPolling();
    pollIssueStatusOnce().catch(() => renderOffline());
    activePoll = window.setInterval(() => {
      pollIssueStatusOnce().catch(() => {
        renderOffline();
        document.getElementById(START_ID)?.removeAttribute("disabled");
        document.getElementById(GRILL_ID)?.removeAttribute("disabled");
      });
    }, POLL_INTERVAL_MS);
  }

  function stopIssueStatusPolling() {
    if (activePoll) {
      window.clearInterval(activePoll);
      activePoll = null;
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
      setPanelSummary("[snapshot] starting...");
      panelExpandedByUser = true;
      setPanelExpanded(true);
      await requestJson("/issue/start", {
        method: "POST",
        label: "Start request",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      startIssueStatusPolling();
    } catch (error) {
      setPanelSummary(error.message);
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
      await getOrAcquireToken(setPanelSummary);
      setPanelSummary("[grill] starting...");
      panelExpandedByUser = true;
      setPanelExpanded(true);
      await requestJson("/issue/grill", {
        method: "POST",
        label: "Grill request",
        statusSetter: setPanelSummary,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      startIssueStatusPolling();
    } catch (error) {
      setPanelSummary(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function createButton(id, testid, labelText, onClick) {
    const button = document.createElement("button");
    button.id = id;
    button.type = "button";
    button.dataset.component = "Button";
    button.dataset.testid = testid;
    button.dataset.loading = "false";
    button.dataset.noVisuals = "true";
    button.dataset.size = "medium";
    button.dataset.variant = "default";
    button.className = "prc-Button-ButtonBase-9n-Xk";
    button.addEventListener("click", onClick);

    const content = document.createElement("span");
    content.dataset.component = "buttonContent";
    content.dataset.align = "center";
    content.className = "prc-Button-ButtonContent-Iohp5";

    const label = document.createElement("span");
    label.dataset.component = "text";
    label.className = "prc-Button-Label-FWkx3";
    label.textContent = labelText;

    content.append(label);
    button.append(content);
    return button;
  }

  function createStartButton() {
    return createButton(START_ID, "pawchestrator-work-button", `${PAW} Work on this issue`, startRun);
  }

  function createGrillButton() {
    return createButton(GRILL_ID, "pawchestrator-grill-button", `${FIRE} Grill Issue`, startGrill);
  }

  function createPanel() {
    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.dataset.expanded = "false";

    const bar = document.createElement("div");
    bar.className = "pawchestrator-panel-bar";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "pawchestrator-panel-toggle prc-Button-ButtonBase-9n-Xk";
    toggle.setAttribute("aria-label", "Toggle Pawchestrator panel");
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "\u25B8";
    toggle.addEventListener("click", () => {
      const expanded = panel.dataset.expanded !== "true";
      panelExpandedByUser = expanded;
      setPanelExpanded(expanded);
    });

    const summary = document.createElement("div");
    summary.className = "pawchestrator-panel-summary";
    summary.textContent = `${PAW} Pawchestrator \u00B7 Checking backend...`;

    const body = document.createElement("div");
    body.className = "pawchestrator-panel-body";

    bar.append(toggle, summary);
    panel.append(bar, body);
    return panel;
  }

  function removeInjectedControls() {
    [START_ID, GRILL_ID, PANEL_ID].forEach((id) => {
      const element = document.getElementById(id);
      if (element) {
        element.remove();
      }
    });
    lastPipelineExpansionKey = null;
    stopIssueStatusPolling();
  }

  function injectHeaderActions() {
    const actions = findHeaderActions();
    if (!actions) {
      return false;
    }

    const existingButton = document.getElementById(START_ID);
    const existingGrillButton = document.getElementById(GRILL_ID);
    const button = existingButton && document.contains(existingButton) ? existingButton : createStartButton();
    const grillButton = existingGrillButton && document.contains(existingGrillButton) ? existingGrillButton : createGrillButton();
    const newIssueHost = findNewIssueHost(actions);

    if (button.parentElement !== actions) {
      actions.insertBefore(button, newIssueHost);
    }
    if (grillButton.parentElement !== actions || grillButton.previousElementSibling !== button) {
      actions.insertBefore(grillButton, newIssueHost);
    }

    button.hidden = false;
    grillButton.hidden = false;
    return true;
  }

  function injectIssuePanel() {
    const issueBody = findIssueBodyContainer();
    if (!issueBody || !issueBody.parentElement) {
      return false;
    }

    const existingPanel = document.getElementById(PANEL_ID);
    const panel = existingPanel && document.contains(existingPanel) ? existingPanel : createPanel();
    if (panel.previousElementSibling !== issueBody) {
      issueBody.after(panel);
    }
    return true;
  }

  function injectIssueControls() {
    if (!isIssuePage()) {
      removeInjectedControls();
      return;
    }

    const headerReady = injectHeaderActions();
    const panelReady = injectIssuePanel();
    if (headerReady && panelReady && !activePoll) {
      startIssueStatusPolling();
    }
  }

  function scheduleInjection() {
    const pathnameChanged = activePathname !== window.location.pathname;
    if (pathnameChanged) {
      activePathname = window.location.pathname;
      panelExpandedByUser = null;
      lastPipelineExpansionKey = null;
      stopIssueStatusPolling();
    }

    if (reinjectTimer) {
      window.clearTimeout(reinjectTimer);
    }

    reinjectTimer = window.setTimeout(() => {
      reinjectTimer = null;
      injectIssueControls();
    }, pathnameChanged ? 0 : REINJECT_DEBOUNCE_MS);
  }

  function installNavigationHooks() {
    ["pushState", "replaceState"].forEach((method) => {
      const original = history[method];
      history[method] = function (...args) {
        const result = original.apply(this, args);
        scheduleInjection();
        return result;
      };
    });

    ["turbo:load", "turbo:render", "popstate"].forEach((eventName) => {
      window.addEventListener(eventName, scheduleInjection);
    });
  }

  injectIssueControls();
  installNavigationHooks();

  const observer = new MutationObserver(() => {
    scheduleInjection();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
