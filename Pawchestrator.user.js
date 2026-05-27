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
// @downloadURL  https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/Pawchestrator.user.js
// @updateURL    https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/Pawchestrator.user.js
// ==/UserScript==

(function () {
  "use strict";

  const API_BASE = "http://127.0.0.1:38472";
  const PANEL_ID = "pawchestrator-panel";
  const PR_PANEL_ID = "pawchestrator-pr-panel";
  const START_ID = "pawchestrator-start";
  const GRILL_ID = "pawchestrator-grill";
  const PR_REVIEW_ID = "pawchestrator-review";
  const PR_REPAIR_ID = "pawchestrator-repair";
  const CREATE_ISSUES_ID = "pawchestrator-create-issues";
  const CONFIRM_OVERLAY_ID = "pawchestrator-confirm-overlay";
  const POLL_INTERVAL_MS = 3000;
  const REINJECT_DEBOUNCE_MS = 100;
  const TOKEN_KEY = "pawchestrator_token";
  const PIPELINE_STAGES = ["snapshot", "scout", "plan", "implement", "verify", "pr"];
  const REVIEW_STAGES = ["review", "post", "issues"];
  const REPAIR_STAGES = ["repair", "push"];
  const PAW = "\uD83D\uDC3E";
  const FIRE = "\uD83D\uDD25";
  const WARNING = "\u26A0";
  const OFFLINE_MESSAGE = "Pawchestrator not running \u2014 start with `pawchestrator serve`";
  const GRILL_WAITING_STATUS = "grill_waiting";
  const GRILL_LABEL = `${FIRE} Grill Issue`;
  const REGRILL_LABEL = `${FIRE} Re-grill`;
  const REGRILL_CONFIRM_MESSAGE =
    "Grill is still waiting for answers on this issue. Are you sure you want to re-grill?";
  const PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE =
    "Grill is still waiting for answers on this issue. Are you sure you want to start agentic work?";
  const RUN_DONE = new Set([
    "completed",
    "failed",
    "grill_complete",
    "grill_failed",
    "epic_complete",
    "epic_failed",
    "post_complete",
    "post_failed",
    "issues_complete",
    "issues_failed",
    "issues_skipped",
    "review_failed",
    "repair_complete",
    "repair_failed",
    "push_complete",
    "push_failed",
  ]);
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
  const GRILL_REPLY_TOOLTIP =
    "Replying to Pawchestrator questions \u2014 submitting will continue the grilling session.";

  let activePoll = null;
  let activePrPoll = null;
  let activePathname = window.location.pathname;
  let panelExpandedByUser = null;
  let lastPipelineExpansionKey = null;
  let reinjectTimer = null;
  let grillReplyObserverState = null;
  let latestIssueStatus = null;
  let latestPrRun = null;
  let latestPrReviewState = null;

  GM_addStyle(`
    #${START_ID},
    #${GRILL_ID},
    #${PR_REVIEW_ID},
    #${PR_REPAIR_ID},
    #${CREATE_ISSUES_ID} {
      white-space: nowrap;
    }

    #${START_ID}:disabled,
    #${GRILL_ID}:disabled,
    #${PR_REVIEW_ID}:disabled,
    #${PR_REPAIR_ID}:disabled,
    #${CREATE_ISSUES_ID}:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }

    #${PANEL_ID},
    #${PR_PANEL_ID} {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-left: 4px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font-size: 13px;
      line-height: 20px;
      margin: 8px 0 16px;
    }

    #${PANEL_ID}[data-status="idle"],
    #${PANEL_ID}[data-status="offline"],
    #${PR_PANEL_ID}[data-status="idle"],
    #${PR_PANEL_ID}[data-status="offline"] {
      border-left-color: var(--borderColor-default, #d0d7de);
    }

    #${PANEL_ID}[data-status="running"],
    #${PR_PANEL_ID}[data-status="running"] {
      border-left-color: var(--fgColor-accent, #0969da);
    }

    #${PANEL_ID}[data-status="done"],
    #${PR_PANEL_ID}[data-status="done"] {
      border-left-color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID}[data-status="failed"],
    #${PR_PANEL_ID}[data-status="failed"] {
      border-left-color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-panel-bar,
    #${PR_PANEL_ID} .pawchestrator-panel-bar {
      align-items: center;
      display: flex;
      gap: 8px;
      min-height: 38px;
      padding: 8px 12px;
    }

    #${PANEL_ID} .pawchestrator-panel-toggle,
    #${PR_PANEL_ID} .pawchestrator-panel-toggle {
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

    #${PANEL_ID} .pawchestrator-panel-summary,
    #${PR_PANEL_ID} .pawchestrator-panel-summary {
      align-items: center;
      display: flex;
      flex: 1;
      gap: 6px;
      min-width: 0;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-panel-brand-name,
    #${PR_PANEL_ID} .pawchestrator-panel-brand-name {
      flex: 0 0 auto;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-panel-status-text,
    #${PR_PANEL_ID} .pawchestrator-panel-status-text {
      min-width: 0;
    }

    #${PANEL_ID} .pawchestrator-panel-body,
    #${PR_PANEL_ID} .pawchestrator-panel-body {
      border-top: 1px solid var(--borderColor-default, #d0d7de);
      display: none;
      padding: 10px 12px 12px;
    }

    #${PANEL_ID}[data-expanded="true"] .pawchestrator-panel-body,
    #${PR_PANEL_ID}[data-expanded="true"] .pawchestrator-panel-body {
      display: block;
    }

    #${PANEL_ID} .pawchestrator-readiness-row,
    #${PR_PANEL_ID} .pawchestrator-readiness-row {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
    }

    #${PANEL_ID} .pawchestrator-readiness-item,
    #${PR_PANEL_ID} .pawchestrator-readiness-item {
      color: var(--fgColor-muted, #59636e);
      white-space: nowrap;
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="true"],
    #${PR_PANEL_ID} .pawchestrator-readiness-item[data-ready="true"] {
      color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="false"],
    #${PR_PANEL_ID} .pawchestrator-readiness-item[data-ready="false"] {
      color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-run-line,
    #${PR_PANEL_ID} .pawchestrator-run-line {
      color: var(--fgColor-muted, #59636e);
      margin-top: 8px;
    }

    #${PANEL_ID} .pawchestrator-pipeline,
    #${PR_PANEL_ID} .pawchestrator-pipeline {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-section {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-epic-section {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-pipeline-title,
    #${PR_PANEL_ID} .pawchestrator-pipeline-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} .pawchestrator-grill-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 6px;
    }

    #${PANEL_ID} .pawchestrator-epic-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} .pawchestrator-epic-runs {
      display: grid;
      gap: 10px;
    }

    #${PANEL_ID} .pawchestrator-epic-run {
      display: grid;
      gap: 6px;
    }

    #${PANEL_ID} .pawchestrator-epic-run-title {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-grill-details {
      color: var(--fgColor-muted, #59636e);
      display: grid;
      gap: 4px;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-active="true"]::before {
      animation: pawchestrator-spin 0.8s linear infinite;
      border: 1px solid var(--fgColor-accent, #0969da);
      border-radius: 50%;
      border-right-color: transparent;
      content: "";
      display: inline-block;
      height: 10px;
      margin-right: 6px;
      vertical-align: -1px;
      width: 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-status="grill_waiting"] {
      background: var(--bgColor-attention-muted, #fff8c5);
      border: 1px solid var(--borderColor-attention-muted, #d4a72c);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
      grid-column: 1 / -1;
      padding: 8px 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-status="grill_waiting"]::before {
      content: "\\26A0";
      display: inline-block;
      margin-right: 6px;
    }

    #${PANEL_ID} .pawchestrator-grill-error {
      color: var(--fgColor-danger, #cf222e);
      grid-column: 1 / -1;
    }

    #${PANEL_ID} .pawchestrator-timeline,
    #${PR_PANEL_ID} .pawchestrator-timeline {
      align-items: flex-start;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(6, minmax(72px, 1fr));
      overflow-x: auto;
      padding-bottom: 2px;
    }

    #${PANEL_ID} .pawchestrator-step,
    #${PR_PANEL_ID} .pawchestrator-step {
      color: var(--fgColor-muted, #59636e);
      min-width: 72px;
      position: relative;
    }

    #${PANEL_ID} .pawchestrator-step:not(:last-child)::after,
    #${PR_PANEL_ID} .pawchestrator-step:not(:last-child)::after {
      background: var(--borderColor-muted, #d8dee4);
      content: "";
      height: 1px;
      left: 23px;
      position: absolute;
      right: -9px;
      top: 8px;
    }

    #${PANEL_ID} .pawchestrator-step[data-active="true"],
    #${PR_PANEL_ID} .pawchestrator-step[data-active="true"] {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-step-label,
    #${PR_PANEL_ID} .pawchestrator-step-label {
      display: block;
      font-size: 12px;
      line-height: 16px;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step-indicator {
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

    #${PANEL_ID} .pawchestrator-step[data-status="pending"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="pending"] .pawchestrator-step-indicator {
      color: var(--fgColor-muted, #59636e);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="running"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="running"] .pawchestrator-step-indicator {
      animation: pawchestrator-spin 0.8s linear infinite;
      border-color: var(--fgColor-accent, #0969da);
      border-right-color: transparent;
      color: transparent;
    }

    #${PANEL_ID} .pawchestrator-step[data-status="done"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="done"] .pawchestrator-step-indicator {
      background: var(--bgColor-success-emphasis, #1a7f37);
      border-color: var(--bgColor-success-emphasis, #1a7f37);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="failed"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="failed"] .pawchestrator-step-indicator {
      background: var(--bgColor-danger-emphasis, #cf222e);
      border-color: var(--bgColor-danger-emphasis, #cf222e);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-warnings,
    #${PR_PANEL_ID} .pawchestrator-warnings {
      margin-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-warnings summary,
    #${PR_PANEL_ID} .pawchestrator-warnings summary {
      color: var(--fgColor-attention, #9a6700);
      cursor: pointer;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-warnings-list,
    #${PR_PANEL_ID} .pawchestrator-warnings-list {
      color: var(--fgColor-muted, #59636e);
      margin: 6px 0 0;
      padding-left: 18px;
    }

    @keyframes pawchestrator-spin {
      to {
        transform: rotate(360deg);
      }
    }

    #${PANEL_ID} a,
    #${PR_PANEL_ID} a {
      color: var(--fgColor-accent, #0969da);
    }

    #${CONFIRM_OVERLAY_ID} {
      align-items: flex-start;
      background: rgba(31, 35, 40, 0.45);
      bottom: 0;
      display: flex;
      justify-content: center;
      left: 0;
      padding: 12vh 16px 16px;
      position: fixed;
      right: 0;
      top: 0;
      z-index: 99999;
    }

    .pawchestrator-confirm-dialog {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      box-shadow: var(--shadow-floating-large, 0 8px 24px rgba(140, 149, 159, 0.2));
      color: var(--fgColor-default, #24292f);
      font-size: 14px;
      line-height: 20px;
      max-width: 440px;
      overflow: hidden;
      width: min(440px, 100%);
    }

    .pawchestrator-confirm-header {
      align-items: center;
      background: var(--bgColor-muted, #f6f8fa);
      border-bottom: 1px solid var(--borderColor-default, #d0d7de);
      display: flex;
      font-weight: 600;
      min-height: 40px;
      padding: 8px 16px;
    }

    .pawchestrator-confirm-body {
      padding: 16px;
    }

    .pawchestrator-confirm-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      padding: 0 16px 16px;
    }

    .pawchestrator-confirm-actions .pawchestrator-confirm-danger {
      background: var(--button-danger-bgColor-rest, #cf222e);
      border-color: var(--button-danger-borderColor-rest, rgba(31, 35, 40, 0.15));
      color: var(--button-danger-fgColor-rest, #ffffff);
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

  function isIssueOpen() {
    const el = document.querySelector('[data-testid="header-state"]');
    return el?.dataset.status === "issueOpened";
  }

  function isPrMerged() {
    return Boolean(document.querySelector('[data-status="pullMerged"]'));
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

  function parsePrReference() {
    const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
    if (!owner || !repo || type !== "pull" || extra) {
      throw new Error("Not a GitHub pull request page");
    }

    const prNumber = Number.parseInt(number, 10);
    if (!Number.isInteger(prNumber) || String(prNumber) !== number || prNumber <= 0) {
      throw new Error("Invalid GitHub pull request number");
    }

    return { owner, repo, pr_number: prNumber };
  }

  function isPrPage() {
    try {
      parsePrReference();
      return true;
    } catch {
      return false;
    }
  }

  function activePanel() {
    return document.getElementById(PANEL_ID) || document.getElementById(PR_PANEL_ID);
  }

  function prRunKey() {
    return `pawchestrator_pr_run:${window.location.pathname}`;
  }

  function findPrConversationContainer() {
    const selectors = [
      "#discussion_bucket",
      "#partial-discussion-header",
      '[data-testid="issue-viewer-issue-container"]',
      ".js-discussion",
    ];
    return selectors
      .map((selector) => document.querySelector(selector))
      .find(Boolean) || null;
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

  function setPanelSummary(message) {
    const panel = activePanel();
    const status = panel && panel.querySelector(".pawchestrator-panel-status-text");
    if (status) {
      status.textContent = message;
    }
  }

  function setPanelStatus(state) {
    const panel = activePanel();
    if (panel) {
      panel.dataset.status = state;
    }
  }

  function setPanelExpanded(expanded) {
    const panel = activePanel();
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
    return Boolean(
      status && (
        status.pipeline ||
        status.grill ||
        epicSubRuns(status.epic).some((run) => run.status === "running" || /_running$/.test(run.status || ""))
      )
    );
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
    const runs = [epicSummaryRun(status.epic), status.pipeline, status.grill].filter(Boolean);
    return runs.find((run) => !isRunDone(run)) || runs[0] || null;
  }

  function isRunDone(run) {
    const status = typeof run === "string" ? run : run?.status;
    return Boolean(status && (RUN_DONE.has(status) || /_failed$/.test(status)));
  }

  function isEpicDone(epic) {
    return Boolean(epic && isRunDone(epic.status || epicStatus(epic)));
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
    if (run.workflow_type === "epic") {
      return `Epic ${run.status || "running"}`;
    }

    const stage = run.current_stage || (run.workflow_type === "grill" ? "grill" : "queued");
    const stageStatus = (run.status || "pending").replace(/^grill_/, "");
    return `[${stage}] ${stageStatus}...`;
  }

  function panelStatusForRun(run) {
    if (!run) {
      return "idle";
    }
    if (run.status === "epic_complete") {
      return "done";
    }
    if (run.status === "epic_failed") {
      return "failed";
    }
    if (
      run.status === "completed" ||
      run.status === "grill_complete" ||
      run.status === "post_complete" ||
      run.status === "issues_complete" ||
      run.status === "issues_skipped" ||
      run.status === "repair_complete" ||
      run.status === "push_complete"
    ) {
      return "done";
    }
    if (
      run.status === "failed" ||
      run.status === "grill_failed" ||
      run.status === "review_failed" ||
      run.status === "post_failed" ||
      run.status === "issues_failed" ||
      run.status === "repair_failed" ||
      run.status === "push_failed"
    ) {
      return "failed";
    }
    return "running";
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

  function collapseNamedStages(stages, names) {
    const rows = Array.isArray(stages) ? stages : [];
    return names.map((name) => ({
      name,
      label: name,
      stage: rows.find((stage) => stageName(stage) === name) || { stage_name: name, status: "pending" },
    }));
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

  function activeNamedStageIndex(run, steps) {
    const failedIndex = steps.findIndex((step) => stageStatus(step.stage) === "failed");
    if (failedIndex >= 0) {
      return failedIndex;
    }

    const current = String(run.current_stage || "");
    const currentIndex = steps.findIndex((step) => step.name === current);
    if (currentIndex >= 0) {
      return currentIndex;
    }

    const runningIndex = steps.findIndex((step) => stageStatus(step.stage) === "running");
    if (runningIndex >= 0) {
      return runningIndex;
    }

    return -1;
  }

  function renderPipelineTimeline(parent, pipeline, options = {}) {
    const steps = collapseStages(pipeline.stages);
    const activeIndex = activeStageIndex(pipeline, steps);
    const timeline = document.createElement("div");
    timeline.className = "pawchestrator-timeline";
    steps.forEach((step, index) => {
      const status = normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
      const item = document.createElement("div");
      item.className = "pawchestrator-step";
      item.dataset.status = status;
      item.dataset.active = String(
        !options.suppressActive && index === activeIndex && pipeline.status !== "completed"
      );

      const indicator = document.createElement("span");
      indicator.className = "pawchestrator-step-indicator";
      indicator.textContent = status === "done" ? "\u2713" : status === "failed" ? "\u00D7" : "\u2022";

      const label = document.createElement("span");
      label.className = "pawchestrator-step-label";
      label.textContent = step.label;

      item.append(indicator, label);
      timeline.append(item);
    });
    parent.append(timeline);
  }

  function renderReviewTimeline(parent, run) {
    const steps = collapseNamedStages(
      run.stages,
      run.workflow_type === "repair" ? REPAIR_STAGES : REVIEW_STAGES,
    );
    const activeIndex = activeNamedStageIndex(run, steps);
    const timeline = document.createElement("div");
    timeline.className = "pawchestrator-timeline";
    steps.forEach((step, index) => {
      const status = normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
      const item = document.createElement("div");
      item.className = "pawchestrator-step";
      item.dataset.status = status;
      item.dataset.active = String(index === activeIndex && !isRunDone(run));

      const indicator = document.createElement("span");
      indicator.className = "pawchestrator-step-indicator";
      indicator.textContent = status === "done" ? "\u2713" : status === "failed" ? "\u00D7" : "\u2022";

      const label = document.createElement("span");
      label.className = "pawchestrator-step-label";
      label.textContent = step.label;

      item.append(indicator, label);
      timeline.append(item);
    });
    parent.append(timeline);
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

    renderPipelineTimeline(section, pipeline);

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

  function epicSubRuns(epic) {
    return Array.isArray(epic?.sub_runs) ? epic.sub_runs : [];
  }

  function epicStatus(epic) {
    if (epic?.status === "epic_complete") {
      return "completed";
    }
    if (epic?.status === "epic_failed") {
      return "failed";
    }
    const runs = epicSubRuns(epic);
    if (runs.some((run) => run.status === "failed" || /_failed$/.test(run.status || ""))) {
      return "failed";
    }
    if (runs.length > 0 && runs.every((run) => run.status === "completed")) {
      return "completed";
    }
    return "running";
  }

  function epicSummaryRun(epic) {
    if (!epic) {
      return null;
    }
    return {
      workflow_type: "epic",
      status: epic.status || epicStatus(epic),
      current_stage: "epic",
      pr_url: epic.pr_url,
    };
  }

  function renderEpicSection(parent, epic) {
    if (!epic) {
      return;
    }

    const section = document.createElement("section");
    section.className = "pawchestrator-epic-section";

    const title = document.createElement("div");
    title.className = "pawchestrator-epic-title";
    title.textContent = `Epic: ${epicStatus(epic)}`;
    if (epic.pr_url) {
      title.append(document.createTextNode(" \u00B7 "));
      const link = document.createElement("a");
      link.href = epic.pr_url;
      link.textContent = "PR";
      title.append(link);
    }
    section.append(title);

    const list = document.createElement("div");
    list.className = "pawchestrator-epic-runs";
    const epicDone = isEpicDone(epic);
    epicSubRuns(epic).forEach((subRun) => {
      const row = document.createElement("div");
      row.className = "pawchestrator-epic-run";

      const rowTitle = document.createElement("div");
      rowTitle.className = "pawchestrator-epic-run-title";
      const titleText = subRun.title ? ` ${subRun.title}` : "";
      rowTitle.textContent = `#${subRun.issue_number}${titleText}`;

      row.append(rowTitle);
      renderPipelineTimeline(row, subRun, { suppressActive: epicDone });
      list.append(row);
    });
    section.append(list);
    parent.append(section);
  }

  function grillReport(grill) {
    return grill.grill_report || grill.report || grill.artifact || {};
  }

  function countGrillValue(grill, report, countKey, listKey) {
    if (Number.isFinite(grill[countKey])) {
      return grill[countKey];
    }
    if (Number.isFinite(report[countKey])) {
      return report[countKey];
    }
    return Array.isArray(report[listKey]) ? report[listKey].length : 0;
  }

  function grillBodyUpdated(grill, report) {
    return grill.body_updated === true || report.body_updated === true;
  }

  function grillTimestamp(grill) {
    return grill.updated_at || grill.completed_at || grill.started_at || "";
  }

  function formatGrillTimestamp(value) {
    if (!value) {
      return "unknown";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return date.toLocaleString();
  }

  function isGrillActive(grill) {
    return Boolean(grill && !isRunDone(grill));
  }

  function commentElementId(commentId) {
    return `issuecomment-${commentId}`;
  }

  function findGrillReplyForm(commentElement) {
    if (!commentElement) {
      return null;
    }
    return Array.from(commentElement.querySelectorAll("form")).find((form) =>
      form.querySelector("textarea, [contenteditable='true']") && findGrillReplySubmit(form)
    ) || null;
  }

  function buttonText(button) {
    return (button?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function setButtonText(button, text) {
    const label = button.querySelector("[data-component='text'], .Button-label, span");
    if (label) {
      label.textContent = text;
    } else {
      button.textContent = text;
    }
  }

  function createDialogButton(labelText, variant, onClick) {
    const button = createButton("", "", labelText, onClick);
    button.removeAttribute("id");
    delete button.dataset.testid;
    if (variant === "danger") {
      button.classList.add("pawchestrator-confirm-danger");
    }
    return button;
  }

  function showConfirmDialog(message, options = {}) {
    document.getElementById(CONFIRM_OVERLAY_ID)?.remove();

    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.id = CONFIRM_OVERLAY_ID;
      overlay.setAttribute("role", "presentation");

      const dialog = document.createElement("div");
      dialog.className = "pawchestrator-confirm-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", "pawchestrator-confirm-title");
      dialog.setAttribute("aria-describedby", "pawchestrator-confirm-message");

      const header = document.createElement("div");
      header.id = "pawchestrator-confirm-title";
      header.className = "pawchestrator-confirm-header";
      header.textContent = options.title || "Confirm action";

      const body = document.createElement("div");
      body.id = "pawchestrator-confirm-message";
      body.className = "pawchestrator-confirm-body";
      body.textContent = message;

      const actions = document.createElement("div");
      actions.className = "pawchestrator-confirm-actions";

      let settled = false;
      const close = (confirmed) => {
        if (settled) {
          return;
        }
        settled = true;
        document.removeEventListener("keydown", onKeydown);
        overlay.remove();
        resolve(confirmed);
      };
      const onKeydown = (event) => {
        if (event.key === "Escape") {
          close(false);
        }
      };

      const noButton = createDialogButton(options.cancelLabel || "No", "default", () => close(false));
      const yesButton = createDialogButton(options.confirmLabel || "Yes", "danger", () => close(true));
      actions.append(noButton, yesButton);

      dialog.append(header, body, actions);
      overlay.append(dialog);
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) {
          close(false);
        }
      });
      document.addEventListener("keydown", onKeydown);
      document.documentElement.append(overlay);
      noButton.focus();
    });
  }

  function findGrillReplySubmit(form) {
    return Array.from(form.querySelectorAll("button, input[type='submit']")).find((button) => {
      if (button.disabled) {
        return false;
      }
      const type = (button.getAttribute("type") || "submit").toLowerCase();
      if (type !== "submit") {
        return false;
      }
      return (
        buttonText(button) === "Comment" ||
        buttonText(button) === "Answer Questions" ||
        button.value === "Comment" ||
        button.value === "Answer Questions"
      );
    }) || null;
  }

  function decorateGrillReplyForm(form) {
    const submit = findGrillReplySubmit(form);
    if (!submit) {
      return;
    }
    if (submit.tagName === "INPUT") {
      submit.value = "Answer Questions";
    } else {
      setButtonText(submit, "Answer Questions");
    }
    submit.title = GRILL_REPLY_TOOLTIP;
    submit.setAttribute("aria-label", GRILL_REPLY_TOOLTIP);
  }

  async function continueGrillFromReply() {
    const issue = parseIssueReference();
    await requestJson("/issue/grill", {
      method: "POST",
      label: "Grill reply request",
      statusSetter: setPanelSummary,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(issue),
    });
    startIssueStatusPolling();
  }

  function disconnectGrillReplyObserver() {
    if (grillReplyObserverState?.observer) {
      grillReplyObserverState.observer.disconnect();
    }
    grillReplyObserverState = null;
  }

  function evaluateGrillReplyForm() {
    const state = grillReplyObserverState;
    if (!state || !document.contains(state.commentElement)) {
      return;
    }

    const form = findGrillReplyForm(state.commentElement);
    if (form) {
      state.formSeen = true;
      decorateGrillReplyForm(form);
      return;
    }

    if (state.formSeen && !state.posted) {
      state.posted = true;
      continueGrillFromReply().catch((error) => setPanelSummary(error.message));
    }
  }

  function attachGrillReplyObserver(grill) {
    const commentId = grill?.github_comment_id;
    if (!commentId) {
      disconnectGrillReplyObserver();
      return;
    }

    const commentElement = document.getElementById(commentElementId(commentId));
    if (!commentElement) {
      disconnectGrillReplyObserver();
      return;
    }

    if (
      grillReplyObserverState?.commentId === String(commentId) &&
      grillReplyObserverState.commentElement === commentElement
    ) {
      evaluateGrillReplyForm();
      return;
    }

    disconnectGrillReplyObserver();
    const observer = new MutationObserver(evaluateGrillReplyForm);
    grillReplyObserverState = {
      commentId: String(commentId),
      commentElement,
      observer,
      formSeen: false,
      posted: false,
    };
    observer.observe(commentElement, { childList: true, subtree: true });
    evaluateGrillReplyForm();
  }

  function renderGrillDetail(parent, label, value) {
    const item = document.createElement("div");
    item.textContent = `${label}: ${value}`;
    parent.append(item);
  }

  function renderGrillSection(parent, grill) {
    if (!grill) {
      return;
    }

    const section = document.createElement("section");
    section.className = "pawchestrator-grill-section";

    const title = document.createElement("div");
    title.className = "pawchestrator-grill-title";
    title.textContent = "Grill";
    section.append(title);

    const details = document.createElement("div");
    details.className = "pawchestrator-grill-details";

    const active = isGrillActive(grill);
    const status = document.createElement("div");
    status.className = "pawchestrator-grill-status";
    status.dataset.active = String(active);
    status.dataset.status = grill.status || "unknown";
    if (grill.status === GRILL_WAITING_STATUS) {
      status.dataset.active = "false";
      status.textContent = "Waiting for your reply. Reply to the questions comment on GitHub to continue.";
    } else {
      status.textContent = active ? "[grill] running..." : `Status: ${grill.status || "unknown"}`;
    }
    details.append(status);

    const report = grillReport(grill);
    renderGrillDetail(details, "Criteria suggested", countGrillValue(grill, report, "criteria_count", "suggested_criteria"));
    renderGrillDetail(details, "Questions posted", countGrillValue(grill, report, "questions_posted_count", "unanswerable_questions"));
    renderGrillDetail(details, "Issue body updated", grillBodyUpdated(grill, report) ? "yes" : "no");
    renderGrillDetail(details, "Last grill run", formatGrillTimestamp(grillTimestamp(grill)));

    if (grill.status === "grill_failed" || grill.status === "failed") {
      const error = document.createElement("div");
      error.className = "pawchestrator-grill-error";
      error.textContent = summarizeError(grill);
      details.append(error);
    }

    section.append(details);
    parent.append(section);
  }

  function renderStatus(status) {
    latestIssueStatus = status;
    const panel = document.getElementById(PANEL_ID);
    if (!panel) {
      return;
    }
    updateGrillButton(status.grill);

    const run = currentRun(status);
    setPanelSummary(summarizeRun(run));
    setPanelStatus(panelStatusForRun(run));

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
    renderEpicSection(body, status.epic);
    renderGrillSection(body, status.grill);
    if (status.grill?.status === "grill_waiting") {
      attachGrillReplyObserver(status.grill);
    } else {
      disconnectGrillReplyObserver();
    }
  }

  function renderOffline() {
    latestIssueStatus = null;
    disconnectGrillReplyObserver();
    updateGrillButton(null);
    setPanelSummary(OFFLINE_MESSAGE);
    setPanelStatus("offline");
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

  async function fetchPrRun(runId) {
    return requestJson(`/runs/${runId}/status`, {
      label: "PR review status request",
    });
  }

  async function fetchPrStatus(pr = parsePrReference()) {
    return requestJson(`/pr/${pr.owner}/${pr.repo}/${pr.pr_number}/status`, {
      label: "PR status request",
    });
  }

  async function fetchPrReviewState(pr = parsePrReference()) {
    return requestJson(`/prs/${pr.owner}/${pr.repo}/${pr.pr_number}/review-state`, {
      label: "PR review state request",
    });
  }

  function reviewHasSuggestedIssues(run) {
    const suggestedIssues = run?.review_report?.suggested_issues;
    return Array.isArray(suggestedIssues)
      && suggestedIssues.length > 0
      && _stageStatusFromRun(run, "issues") === "pending";
  }

  function _stageStatusFromRun(run, stageName) {
    const stages = Array.isArray(run?.stages) ? run.stages : [];
    const stage = stages.find((item) => item.stage_name === stageName);
    return stage?.status || null;
  }

  function isPrRunActive(run) {
    return Boolean(run && !isRunDone(run));
  }

  function summarizePrRun(run) {
    if (!run) {
      if (latestPrReviewState === "changes_requested") {
        return "Changes requested";
      }
      return "Ready for review";
    }
    if (run.workflow_type === "repair") {
      return isPrRunActive(run) ? "[repair] running..." : `Repair ${run.status || "complete"}`;
    }
    if (run.status === "post_complete" && reviewHasSuggestedIssues(run)) {
      return "Review complete - suggested issues ready";
    }
    if (run.status === "post_complete" || run.status === "issues_complete" || run.status === "issues_skipped") {
      return "Review complete";
    }
    if (run.status === "review_failed" || run.status === "post_failed" || run.status === "issues_failed") {
      return summarizeError(run);
    }
    const stage = run.current_stage || "review";
    const status = (run.status || "pending").replace(/^(review|post|issues)_/, "");
    return `[${stage}] ${status}...`;
  }

  function renderPrStatus(run) {
    latestPrRun = run;
    setPanelSummary(summarizePrRun(run));
    setPanelStatus(panelStatusForRun(run));
    setPanelExpanded(Boolean(run));
    updatePrActionButtons(run);

    const body = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-body");
    if (!body) {
      return;
    }
    body.textContent = "";

    if (!run) {
      const line = document.createElement("div");
      line.className = "pawchestrator-run-line";
      line.textContent = "No active review run for this PR.";
      body.append(line);
      return;
    }

    const line = document.createElement("div");
    line.className = "pawchestrator-run-line";
    line.textContent = `${run.workflow_type || "review"}: ${summarizePrRun(run)}`;
    body.append(line);

    const section = document.createElement("section");
    section.className = "pawchestrator-pipeline";
    const title = document.createElement("div");
    title.className = "pawchestrator-pipeline-title";
    title.textContent = run.workflow_type === "repair" ? "Repair" : "Review";
    section.append(title);
    renderReviewTimeline(section, run);

    if (reviewHasSuggestedIssues(run)) {
      const issuesLine = document.createElement("div");
      issuesLine.className = "pawchestrator-run-line";
      issuesLine.textContent = "issues: pending";
      issuesLine.append(document.createTextNode(" "));
      issuesLine.append(createButton(CREATE_ISSUES_ID, "pawchestrator-create-issues-button", "Create Issues", createIssues));
      section.append(issuesLine);
    }

    body.append(section);
  }

  function renderPrOffline() {
    latestPrRun = null;
    latestPrReviewState = null;
    setPanelSummary(OFFLINE_MESSAGE);
    setPanelStatus("offline");
    const body = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-body");
    if (body) {
      body.textContent = "";
    }
    updatePrActionButtons();
    document.getElementById(PR_REPAIR_ID)?.remove();
  }

  function renderPrReviewState(reviewState) {
    latestPrReviewState = reviewState;
    updatePrActionButtons(latestPrRun);
    if (!latestPrRun && reviewState === "changes_requested") {
      setPanelSummary("Changes requested");
    }
  }

  async function pollPrStatusOnce() {
    const reviewStatePromise = fetchPrReviewState();
    const storedRunId = await GM_getValue(prRunKey());
    if (storedRunId) {
      const storedRun = await fetchPrRun(storedRunId);
      if (isPrRunActive(storedRun)) {
        renderPrReviewState((await reviewStatePromise).state);
        renderPrStatus(storedRun);
        return true;
      }

      const [status, reviewState] = await Promise.all([fetchPrStatus(), reviewStatePromise]);
      renderPrReviewState(reviewState.state);
      const activeRun = [status.repair, status.review].filter(Boolean).find(isPrRunActive);
      const run = activeRun || storedRun;
      if (activeRun?.id && activeRun.id !== storedRunId) {
        await GM_setValue(prRunKey(), activeRun.id);
      }
      renderPrStatus(run);
      return isPrRunActive(run);
    }

    const [status, reviewState] = await Promise.all([fetchPrStatus(), reviewStatePromise]);
    renderPrReviewState(reviewState.state);
    const run = [status.repair, status.review].filter(Boolean).find(isPrRunActive)
      || status.review
      || status.repair
      || null;
    if (run?.id) {
      await GM_setValue(prRunKey(), run.id);
    }
    renderPrStatus(run);
    if (!run) {
      renderPrStatus(null);
      return false;
    }
    return isPrRunActive(run);
  }

  function startPrStatusPolling() {
    stopPrStatusPolling();
    pollPrStatusOnce().catch(() => renderPrOffline());
    activePrPoll = window.setInterval(() => {
      pollPrStatusOnce().then((running) => {
        if (!running && activePrPoll) {
          stopPrStatusPolling();
        }
      }).catch(() => renderPrOffline());
    }, POLL_INTERVAL_MS);
  }

  function stopPrStatusPolling() {
    if (activePrPoll) {
      window.clearInterval(activePrPoll);
      activePrPoll = null;
    }
  }

  async function pollIssueStatusOnce() {
    const issue = parseIssueReference();
    const status = await fetchIssueStatus(issue);
    renderStatus(status);
    const run = currentRun(status);
    const running = run && !isRunDone(run);
    const issueOpen = isIssueOpen();
    const anyActive = Boolean(
      (status.pipeline && !isRunDone(status.pipeline)) ||
      (status.grill && !isRunDone(status.grill)) ||
      (status.epic && !isEpicDone(status.epic)) ||
      (!isEpicDone(status.epic) && epicSubRuns(status.epic).some((run) => !isRunDone(run)))
    );
    const shouldDisable = !issueOpen || anyActive;
    const closedTitle = !issueOpen ? "Issue is closed" : "";
    for (const id of [START_ID, GRILL_ID]) {
      const btn = document.getElementById(id);
      if (!btn) continue;
      btn.toggleAttribute("disabled", shouldDisable);
      btn.title = closedTitle;
    }
    return running;
  }

  function startIssueStatusPolling() {
    stopIssueStatusPolling();
    pollIssueStatusOnce().catch(() => renderOffline());
    activePoll = window.setInterval(() => {
      pollIssueStatusOnce().catch(() => {
        renderOffline();
        if (isIssueOpen()) {
          document.getElementById(START_ID)?.removeAttribute("disabled");
          document.getElementById(GRILL_ID)?.removeAttribute("disabled");
        }
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
      const status = await fetchIssueStatus(issue);
      if (status.epic_confirm && !confirmEpicStart(status.epic)) {
        if (button) {
          button.disabled = false;
        }
        return;
      }
      if (status.grill?.status === GRILL_WAITING_STATUS) {
        const confirmed = await showConfirmDialog(PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE, {
          title: "Start agentic work?",
          confirmLabel: "Yes",
          cancelLabel: "No",
        });
        if (!confirmed) {
          if (button) {
            button.disabled = false;
          }
          return;
        }
      }
      setPanelSummary("[snapshot] starting...");
      panelExpandedByUser = true;
      setPanelExpanded(true);
      const response = await requestJson("/issue/start", {
        method: "POST",
        label: "Start request",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      if (response?.type === "epic") {
        renderStatus({
          ...status,
          pipeline: null,
          epic: epicFromStartResponse(response),
        });
      }
      startIssueStatusPolling();
    } catch (error) {
      setPanelSummary(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  function confirmEpicStart(epic) {
    const runs = epicSubRuns(epic);
    const lines = runs.map((run) => {
      const title = run.title ? ` ${run.title}` : "";
      return `#${run.issue_number}${title}`;
    });
    const list = lines.length > 0 ? `\n\n${lines.join("\n")}` : "";
    return window.confirm(`Work on this epic issue and its sub-issues?${list}`);
  }

  function epicFromStartResponse(response) {
    return {
      run_id: response.run_id,
      group_id: response.group_id,
      status: "epic_running",
      mode: response.mode,
      branch: response.branch,
      pr_url: response.pr_url,
      sub_runs: (response.sub_runs || []).map((run) => ({
        issue_number: run.issue_number,
        run_id: run.run_id,
        title: run.title,
        status: "pending",
        current_stage: null,
        workflow_type: "pipeline",
        stages: PIPELINE_STAGES.map((stage_name) => ({ stage_name, status: "pending" })),
        warnings: [],
      })),
    };
  }

  async function startGrill() {
    const button = document.getElementById(GRILL_ID);
    try {
      const issue = parseIssueReference();
      await getOrAcquireToken(setPanelSummary);
      const status = latestIssueStatus || await fetchIssueStatus(issue);
      latestIssueStatus = status;
      updateGrillButton(status.grill);
      if (status.grill?.status === GRILL_WAITING_STATUS) {
        const confirmed = await showConfirmDialog(REGRILL_CONFIRM_MESSAGE, {
          title: "Re-grill issue?",
          confirmLabel: "Yes",
          cancelLabel: "No",
        });
        if (!confirmed) {
          return;
        }
      }
      if (button) {
        button.disabled = true;
      }
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

  async function startReview() {
    const button = document.getElementById(PR_REVIEW_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const pr = parsePrReference();
      await getOrAcquireToken(setPanelSummary);
      setPanelSummary("[review] starting...");
      panelExpandedByUser = true;
      setPanelExpanded(true);
      const response = await requestJson("/runs/review/start", {
        method: "POST",
        label: "Review start request",
        statusSetter: setPanelSummary,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pr),
      });
      await GM_setValue(prRunKey(), response.run_id);
      renderPrStatus({
        run_id: response.run_id,
        workflow_type: "review",
        status: "review_running",
        current_stage: "review",
        stages: REVIEW_STAGES.map((stage_name) => ({
          stage_name,
          status: stage_name === "review" ? "running" : "pending",
        })),
      });
      startPrStatusPolling();
    } catch (error) {
      setPanelSummary(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  async function startRepair() {
    const button = document.getElementById(PR_REPAIR_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const pr = parsePrReference();
      await getOrAcquireToken(setPanelSummary);
      setPanelSummary("[repair] starting...");
      panelExpandedByUser = true;
      setPanelExpanded(true);
      const response = await requestJson("/runs/repair/start", {
        method: "POST",
        label: "Repair start request",
        statusSetter: setPanelSummary,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pr),
      });
      await GM_setValue(prRunKey(), response.run_id);
      renderPrStatus({
        run_id: response.run_id,
        workflow_type: "repair",
        status: "repair_running",
        current_stage: "repair",
        stages: REPAIR_STAGES.map((stage_name) => ({
          stage_name,
          status: stage_name === "repair" ? "running" : "pending",
        })),
      });
      startPrStatusPolling();
    } catch (error) {
      setPanelSummary(error.message);
      if (button) {
        button.disabled = false;
      }
    }
  }

  async function createIssues() {
    const runId = latestPrRun?.id || latestPrRun?.run_id || await GM_getValue(prRunKey());
    const button = document.getElementById(CREATE_ISSUES_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      if (!runId) {
        throw new Error("No review run found for this PR");
      }
      setPanelSummary("[issues] creating...");
      const run = await requestJson(`/runs/${runId}/create-issues`, {
        method: "POST",
        label: "Create issues request",
        statusSetter: setPanelSummary,
      });
      renderPrStatus(run);
      startPrStatusPolling();
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

  function createReviewButton() {
    return createButton(PR_REVIEW_ID, "pawchestrator-review-button", `${PAW} Review with Pawchestrator`, startReview);
  }

  function createRepairButton() {
    return createButton(PR_REPAIR_ID, "pawchestrator-repair-button", `${PAW} Work on Request Changes`, startRepair);
  }

  function updatePrActionButtons(run = latestPrRun) {
    const active = isPrRunActive(run);
    const merged = isPrMerged();
    const disableMessage = "Pull request is merged";
    const reviewButton = document.getElementById(PR_REVIEW_ID);
    if (reviewButton) {
      reviewButton.toggleAttribute("disabled", active || merged);
      if (merged) {
        reviewButton.title = disableMessage;
      } else {
        reviewButton.removeAttribute("title");
      }
    }

    let repairButton = document.getElementById(PR_REPAIR_ID);
    if (latestPrReviewState !== "changes_requested") {
      repairButton?.remove();
      return;
    }

    if (!repairButton) {
      repairButton = createRepairButton();
      const bar = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-bar");
      const review = document.getElementById(PR_REVIEW_ID);
      if (bar) {
        if (review?.nextSibling) {
          bar.insertBefore(repairButton, review.nextSibling);
        } else {
          bar.append(repairButton);
        }
      }
    }
    repairButton.toggleAttribute("disabled", active || merged);
    if (merged) {
      repairButton.title = disableMessage;
    } else {
      repairButton.removeAttribute("title");
    }
  }

  function grillButtonLabel(grill) {
    return grill?.status === GRILL_WAITING_STATUS ? REGRILL_LABEL : GRILL_LABEL;
  }

  function updateGrillButton(grill) {
    const button = document.getElementById(GRILL_ID);
    if (button) {
      setButtonText(button, grillButtonLabel(grill));
    }
  }

  function createGrillButton(grill) {
    return createButton(GRILL_ID, "pawchestrator-grill-button", grillButtonLabel(grill), startGrill);
  }

  function createPanel() {
    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.dataset.expanded = "false";
    panel.dataset.status = "idle";

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

    const brand = document.createElement("span");
    brand.className = "pawchestrator-panel-brand-name";
    brand.textContent = `${PAW} Pawchestrator`;

    const separator = document.createElement("span");
    separator.setAttribute("aria-hidden", "true");
    separator.textContent = "\u00B7";

    const status = document.createElement("span");
    status.className = "pawchestrator-panel-status-text";
    status.textContent = "Checking backend...";

    summary.append(brand, separator, status);

    const body = document.createElement("div");
    body.className = "pawchestrator-panel-body";

    bar.append(toggle, summary, createStartButton(), createGrillButton());
    panel.append(bar, body);
    return panel;
  }

  function createPrPanel() {
    const panel = document.createElement("div");
    panel.id = PR_PANEL_ID;
    panel.dataset.expanded = "false";
    panel.dataset.status = "idle";

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

    const brand = document.createElement("span");
    brand.className = "pawchestrator-panel-brand-name";
    brand.textContent = `${PAW} Pawchestrator`;

    const separator = document.createElement("span");
    separator.setAttribute("aria-hidden", "true");
    separator.textContent = "\u00B7";

    const status = document.createElement("span");
    status.className = "pawchestrator-panel-status-text";
    status.textContent = "Checking review status...";

    summary.append(brand, separator, status);

    const body = document.createElement("div");
    body.className = "pawchestrator-panel-body";

    bar.append(toggle, summary, createReviewButton());
    panel.append(bar, body);
    return panel;
  }

  function removeInjectedControls() {
    document.getElementById(PANEL_ID)?.remove();
    document.getElementById(PR_PANEL_ID)?.remove();
    lastPipelineExpansionKey = null;
    disconnectGrillReplyObserver();
    stopIssueStatusPolling();
    stopPrStatusPolling();
  }

  function injectIssuePanel() {
    const issueBody = findIssueBodyContainer();
    if (!issueBody || !issueBody.parentElement) {
      return false;
    }

    const existingPanel = document.getElementById(PANEL_ID);
    const panel = existingPanel && document.contains(existingPanel) ? existingPanel : createPanel();
    const innerBox = issueBody.querySelector('[data-testid="issue-body"]');
    const panelOffset = innerBox
      ? innerBox.getBoundingClientRect().left - issueBody.getBoundingClientRect().left
      : 0;
    panel.style.marginLeft = `${panelOffset}px`;
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

    const panelReady = injectIssuePanel();
    if (panelReady && !activePoll) {
      startIssueStatusPolling();
    }
  }

  function injectPrPanel() {
    const container = findPrConversationContainer();
    if (!container || !container.parentElement) {
      return false;
    }

    const existingPanel = document.getElementById(PR_PANEL_ID);
    const panel = existingPanel && document.contains(existingPanel) ? existingPanel : createPrPanel();
    panel.style.marginLeft = "";
    if (panel.nextElementSibling !== container) {
      container.before(panel);
    }
    return true;
  }

  function injectPrControls() {
    if (!isPrPage()) {
      return false;
    }

    document.getElementById(PANEL_ID)?.remove();
    stopIssueStatusPolling();
    const panelReady = injectPrPanel();
    if (panelReady && !activePrPoll) {
      startPrStatusPolling();
    }
    return panelReady;
  }

  function injectControls() {
    if (isPrPage()) {
      injectPrControls();
      return;
    }

    document.getElementById(PR_PANEL_ID)?.remove();
    stopPrStatusPolling();
    injectIssueControls();
  }

  function scheduleInjection() {
    const pathnameChanged = activePathname !== window.location.pathname;
    if (pathnameChanged) {
      activePathname = window.location.pathname;
      panelExpandedByUser = null;
      lastPipelineExpansionKey = null;
      stopIssueStatusPolling();
      stopPrStatusPolling();
    }

    if (reinjectTimer) {
      window.clearTimeout(reinjectTimer);
    }

    reinjectTimer = window.setTimeout(() => {
      reinjectTimer = null;
      injectControls();
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

  injectControls();
  installNavigationHooks();

  const observer = new MutationObserver(() => {
    scheduleInjection();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
