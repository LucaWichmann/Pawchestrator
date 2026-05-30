import {
  CREATE_ISSUES_ID,
  OFFLINE_MESSAGE,
  PAW,
  PR_PANEL_ID,
  PR_REPAIR_ID,
  PR_REVIEW_ID,
  REPAIR_STAGES,
  REVIEW_STAGES,
  STAGE_DONE,
} from "../constants";
import { state } from "../state";
import { isRunDone, summarizeError } from "../summarize";
import {
  createButton,
  findPrConversationContainer,
  setPanelExpanded,
  setPanelStatus,
  setPanelSummary,
} from "./common";

let prPanelHandlers = {
  startReview: () => {},
  startRepair: () => {},
  createIssues: () => {},
  isPrMerged: () => false,
};

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
    run.status === "push_complete" ||
    run.status === "epic_architect_complete"
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
    run.status === "push_failed" ||
    run.status === "epic_architect_failed"
  ) {
    return "failed";
  }
  return "running";
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

function collapseNamedStages(stages, names) {
  const rows = Array.isArray(stages) ? stages : [];
  return names.map((name) => ({
    name,
    label: name,
    stage: rows.find((stage) => stageName(stage) === name) || {
      stage_name: name,
      status: "pending",
    },
  }));
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

function renderNamedTimeline(parent, run, stageNames, options = {}) {
  const steps = collapseNamedStages(run.stages, stageNames);
  const activeIndex = activeNamedStageIndex(run, steps);
  const timeline = document.createElement("div");
  timeline.className = "pawchestrator-timeline";
  timeline.style.gridTemplateColumns = `repeat(${stageNames.length}, minmax(72px, 1fr))`;
  steps.forEach((step, index) => {
    const doneByRun = options.markComplete && index <= activeIndex;
    const status = doneByRun
      ? "done"
      : normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
    const item = document.createElement("div");
    item.className = "pawchestrator-step";
    item.dataset.status = status;
    item.dataset.active = String(
      !options.suppressActive && index === activeIndex && !isRunDone(run),
    );

    const indicator = document.createElement("span");
    indicator.className = "pawchestrator-step-indicator";
    indicator.textContent =
      status === "done" ? "\u2713" : status === "failed" ? "\u00D7" : "\u2022";

    const label = document.createElement("span");
    label.className = "pawchestrator-step-label";
    label.textContent = step.label;

    item.append(indicator, label);
    timeline.append(item);
  });
  parent.append(timeline);
}

function renderReviewTimeline(parent, run) {
  renderNamedTimeline(parent, run, run.workflow_type === "repair" ? REPAIR_STAGES : REVIEW_STAGES);
}

function reviewHasSuggestedIssues(run) {
  const suggestedIssues = run?.review_report?.suggested_issues;
  return (
    Array.isArray(suggestedIssues) &&
    suggestedIssues.length > 0 &&
    _stageStatusFromRun(run, "issues") === "pending"
  );
}

function _stageStatusFromRun(run, stageName) {
  const stages = Array.isArray(run?.stages) ? run.stages : [];
  const stage = stages.find((item) => item.stage_name === stageName);
  return stage?.status || null;
}

export function isPrRunActive(run) {
  return Boolean(run && !isRunDone(run));
}

function summarizePrRun(run) {
  if (!run) {
    if (state.latestPrReviewState === "changes_requested") {
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
  if (
    run.status === "post_complete" ||
    run.status === "issues_complete" ||
    run.status === "issues_skipped"
  ) {
    return "Review complete";
  }
  if (
    run.status === "review_failed" ||
    run.status === "post_failed" ||
    run.status === "issues_failed"
  ) {
    return summarizeError(run);
  }
  const stage = run.current_stage || "review";
  const status = (run.status || "pending").replace(/^(review|post|issues)_/, "");
  return `[${stage}] ${status}...`;
}

export function renderPrStatus(run) {
  state.latestPrRun = run;
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
    issuesLine.append(
      createButton(
        CREATE_ISSUES_ID,
        "pawchestrator-create-issues-button",
        "Create Issues",
        prPanelHandlers.createIssues,
      ),
    );
    section.append(issuesLine);
  }

  body.append(section);
}

export function renderPrOffline() {
  state.latestPrRun = null;
  state.latestPrReviewState = null;
  setPanelSummary(OFFLINE_MESSAGE);
  setPanelStatus("offline");
  const body = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-body");
  if (body) {
    body.textContent = "";
  }
  updatePrActionButtons();
  document.getElementById(PR_REPAIR_ID)?.remove();
}

export function renderPrReviewState(reviewState) {
  state.latestPrReviewState = reviewState;
  updatePrActionButtons(state.latestPrRun);
  if (!state.latestPrRun && reviewState === "changes_requested") {
    setPanelSummary("Changes requested");
  }
}

export function updatePrActionButtons(run = state.latestPrRun) {
  const active = isPrRunActive(run);
  const merged = prPanelHandlers.isPrMerged();
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
  if (state.latestPrReviewState !== "changes_requested") {
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

function createReviewButton() {
  return createButton(
    PR_REVIEW_ID,
    "pawchestrator-review-button",
    `${PAW} Review with Pawchestrator`,
    prPanelHandlers.startReview,
  );
}

function createRepairButton() {
  return createButton(
    PR_REPAIR_ID,
    "pawchestrator-repair-button",
    `${PAW} Work on Request Changes`,
    prPanelHandlers.startRepair,
  );
}

export function buildPrPanel(handlers = {}) {
  prPanelHandlers = { ...prPanelHandlers, ...handlers };
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
    state.panelExpandedByUser = expanded;
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

export function injectPrPanel(handlers = {}) {
  prPanelHandlers = { ...prPanelHandlers, ...handlers };
  const container = findPrConversationContainer();
  if (!container || !container.parentElement) {
    return false;
  }

  const existingPanel = document.getElementById(PR_PANEL_ID);
  const panel = existingPanel && document.contains(existingPanel) ? existingPanel : buildPrPanel();
  panel.style.marginLeft = "";
  if (panel.nextElementSibling !== container) {
    container.before(panel);
  }
  return true;
}
