import {
  CONSTRUCTION,
  EPIC_ARCHITECT_ID,
  EPIC_ARCHITECT_STAGES,
  GRILL_ID,
  OFFLINE_MESSAGE,
  PANEL_ID,
  PAW,
  START_ID,
  WARNING,
} from "../constants";
import { state } from "../state";
import { currentRun, isRunDone, summarizeError, summarizeRun } from "../summarize";
import { epicSubRuns, renderEpicSection } from "../render/epic";
import { grillButtonLabel, renderGrillSection, updateGrillButton } from "../render/grill";
import { renderNamedTimeline, renderTimeline } from "../render/timeline";
import {
  createButton,
  findIssueBodyContainer,
  maybeAutoExpandForPipeline,
  setPanelExpanded,
  setPanelStatus,
  setPanelSummary,
  shouldAutoExpand,
} from "./common";

let issuePanelHandlers = {
  startRun: () => {},
  startGrill: () => {},
  startEpicArchitect: () => {},
  isIssueOpen: () => true,
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

function renderReadinessItem(parent, label, ready) {
  const item = document.createElement("span");
  item.className = "pawchestrator-readiness-item";
  item.dataset.ready = String(Boolean(ready));
  item.textContent = `${ready ? "\u2713" : "\u00D7"} ${label}`;
  parent.append(item);
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

  renderTimeline(section, pipeline);

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

function epicArchitectCreatedIssues(run) {
  return Array.isArray(run?.created_sub_issues) ? run.created_sub_issues : [];
}

function issueAlreadyHasSubIssues(status) {
  if (epicArchitectCreatedIssues(status?.epic_architect).length > 0) {
    return true;
  }
  const summary = status?.issue?.sub_issues_summary || status?.sub_issues_summary;
  return Number(summary?.total || summary?.completed || summary?.percent_completed) > 0;
}

function epicArchitectTimelineRun(run) {
  const stages = Array.isArray(run?.stages) ? [...run.stages] : [];
  const created = epicArchitectCreatedIssues(run);
  if (
    created.length > 0 ||
    run?.status === "completed" ||
    run?.status === "epic_architect_complete"
  ) {
    stages.push({ stage_name: "creating", status: "complete" });
  }
  return {
    ...run,
    current_stage:
      created.length > 0 || run?.status === "completed" ? "creating" : run?.current_stage,
    stages,
  };
}

function renderCreatedSubIssueLinks(parent, created) {
  const list = document.createElement("ul");
  list.className = "pawchestrator-epic-architect-created";
  created.forEach((issue) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = issue.url;
    link.textContent = `#${issue.number}${issue.title ? ` ${issue.title}` : ""}`;
    item.append(link);
    list.append(item);
  });
  parent.append(list);
}

function renderEpicArchitectSection(parent, run) {
  if (!run) {
    return;
  }

  const section = document.createElement("section");
  section.className = "pawchestrator-epic-architect-section";

  const title = document.createElement("div");
  title.className = "pawchestrator-epic-architect-title";
  title.textContent = "EpicArchitect";
  section.append(title);

  renderNamedTimeline(section, epicArchitectTimelineRun(run), EPIC_ARCHITECT_STAGES, {
    markComplete: run.status === "completed" || run.status === "epic_architect_complete",
  });

  const created = epicArchitectCreatedIssues(run);
  if (
    run.epic_analysis &&
    (run.status === "completed" || run.status === "epic_architect_complete")
  ) {
    const analysis = document.createElement("div");
    analysis.className = "pawchestrator-epic-architect-analysis";
    analysis.textContent = run.epic_analysis;
    section.append(analysis);
  }
  if (
    created.length > 0 &&
    (run.status === "completed" || run.status === "epic_architect_complete")
  ) {
    renderCreatedSubIssueLinks(section, created);
  }
  if (isRunDone(run) && !(run.status === "completed" || run.status === "epic_architect_complete")) {
    const error = document.createElement("div");
    error.className = "pawchestrator-epic-architect-error";
    error.textContent = summarizeError(run);
    section.append(error);
    if (created.length > 0) {
      const partial = document.createElement("div");
      partial.className = "pawchestrator-epic-architect-partial";
      partial.textContent = `Created before failure: ${created.map((issue) => `#${issue.number}`).join(", ")}`;
      section.append(partial);
    }
  }

  parent.append(section);
}

function createGrillButton(grill) {
  return createButton(
    GRILL_ID,
    "pawchestrator-grill-button",
    grillButtonLabel(grill),
    issuePanelHandlers.startGrill,
  );
}

function createEpicArchitectButton() {
  return createButton(
    EPIC_ARCHITECT_ID,
    "pawchestrator-epic-architect-button",
    `${CONSTRUCTION} Turn into Epic`,
    issuePanelHandlers.startEpicArchitect,
  );
}

function updateEpicArchitectButton(status = state.latestIssueStatus) {
  const bar = document.getElementById(PANEL_ID)?.querySelector(".pawchestrator-panel-bar");
  let button = document.getElementById(EPIC_ARCHITECT_ID);
  if (!bar) {
    return;
  }
  if (issueAlreadyHasSubIssues(status)) {
    button?.remove();
    return;
  }
  if (!button) {
    button = createEpicArchitectButton();
    const grill = document.getElementById(GRILL_ID);
    if (grill?.nextSibling) {
      bar.insertBefore(button, grill.nextSibling);
    } else {
      bar.append(button);
    }
  }
  const run = status?.epic_architect;
  button.toggleAttribute(
    "disabled",
    Boolean(run && !isRunDone(run)) || !issuePanelHandlers.isIssueOpen(),
  );
  button.title = issuePanelHandlers.isIssueOpen() ? "" : "Issue is closed";
}

function createStartButton() {
  return createButton(
    START_ID,
    "pawchestrator-work-button",
    `${PAW} Work on this issue`,
    issuePanelHandlers.startRun,
  );
}

export function buildIssuePanel(handlers = {}) {
  issuePanelHandlers = { ...issuePanelHandlers, ...handlers };
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
  status.textContent = "Checking backend...";

  summary.append(brand, separator, status);

  const body = document.createElement("div");
  body.className = "pawchestrator-panel-body";

  bar.append(
    toggle,
    summary,
    createStartButton(),
    createGrillButton(),
    createEpicArchitectButton(),
  );
  panel.append(bar, body);
  return panel;
}

export function injectIssuePanel(handlers = {}) {
  issuePanelHandlers = { ...issuePanelHandlers, ...handlers };
  const issueBody = findIssueBodyContainer();
  if (!issueBody || !issueBody.parentElement) {
    return false;
  }

  const existingPanel = document.getElementById(PANEL_ID);
  const panel =
    existingPanel && document.contains(existingPanel) ? existingPanel : buildIssuePanel();
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

export function renderStatus(status, callbacks = {}) {
  state.latestIssueStatus = status;
  const panel = document.getElementById(PANEL_ID);
  if (!panel) {
    return;
  }
  updateGrillButton(status.grill);
  updateEpicArchitectButton(status);

  const run = currentRun(status);
  setPanelSummary(summarizeRun(run));
  setPanelStatus(panelStatusForRun(run));

  if (state.panelExpandedByUser === null) {
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

  renderGrillSection(body, status.grill);
  renderEpicArchitectSection(body, status.epic_architect);
  renderPipeline(body, status.pipeline);
  if (status.pipeline?.status === "awaiting_plan_approval" && status.plan_approval_plan) {
    callbacks.renderPlanApprovalSubView?.(status.plan_approval_plan, status.pipeline.run_id);
  }
  renderEpicSection(body, status.epic);
  if (status.grill?.status === "grill_waiting") {
    callbacks.attachGrillReplyObserver?.(status.grill);
  } else {
    callbacks.disconnectGrillReplyObserver?.();
  }
}

export function renderOffline(callbacks = {}) {
  state.latestIssueStatus = null;
  callbacks.disconnectGrillReplyObserver?.();
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
