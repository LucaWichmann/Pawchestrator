import { PANEL_ID, PIPELINE_ACTIVE, PR_PANEL_ID } from "../constants";
import { state } from "../state";

export function activePanel() {
  return document.getElementById(PANEL_ID) || document.getElementById(PR_PANEL_ID);
}

export function setPanelSummary(message) {
  const panel = activePanel();
  const status = panel && panel.querySelector(".pawchestrator-panel-status-text");
  if (status) {
    status.textContent = message;
  }
}

export function setPanelStatus(nextState) {
  const panel = activePanel();
  if (panel) {
    panel.dataset.status = nextState;
  }
}

export function setPanelExpanded(expanded) {
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

export function shouldAutoExpand(status) {
  return Boolean(
    status &&
    (status.pipeline ||
      status.grill ||
      status.epic_architect ||
      (Array.isArray(status.epic?.sub_runs) ? status.epic.sub_runs : []).some(
        (run) => run.status === "running" || /_running$/.test(run.status || ""),
      )),
  );
}

function isPipelineVisible(pipeline) {
  return Boolean(
    pipeline &&
    (PIPELINE_ACTIVE.has(pipeline.status) ||
      pipeline.status === "completed" ||
      pipeline.status === "failed"),
  );
}

export function maybeAutoExpandForPipeline(status) {
  const pipeline = status && status.pipeline;
  if (!pipeline) {
    state.lastPipelineExpansionKey = null;
    return;
  }

  const key = `${pipeline.run_id || ""}:${pipeline.status || ""}:${pipeline.current_stage || ""}`;
  const shouldExpand = isPipelineVisible(pipeline) && key !== state.lastPipelineExpansionKey;
  state.lastPipelineExpansionKey = key;
  if (shouldExpand) {
    setPanelExpanded(true);
  }
}

export function findIssueBodyContainer() {
  const selectors = [
    ".IssueBody-module__outerContainer__ULNTb",
    '[class*="IssueBody-module__outerContainer"]',
  ];
  return selectors.map((selector) => document.querySelector(selector)).find(Boolean) || null;
}

export function findPrConversationContainer() {
  const selectors = [
    "#discussion_bucket",
    "#partial-discussion-header",
    '[data-testid="issue-viewer-issue-container"]',
    ".js-discussion",
  ];
  return selectors.map((selector) => document.querySelector(selector)).find(Boolean) || null;
}
