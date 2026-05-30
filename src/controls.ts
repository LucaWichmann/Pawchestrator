import { startEpicArchitect as startEpicArchitectAction } from "./actions/epic-architect";
import { startGrill as startGrillAction } from "./actions/grill";
import { startRun as startRunAction } from "./actions/run";
import {
  startCreateIssues as startCreateIssuesAction,
  startRepair as startRepairAction,
  startReview as startReviewAction,
} from "./actions/review";
import { PANEL_ID, PR_PANEL_ID } from "./constants";
import { injectIssuePanel } from "./panel/issue";
import { injectPrPanel, renderPrStatus } from "./panel/pr";
import {
  disconnectGrillReplyObserver,
  renderStatus,
  startIssueStatusPolling,
  startPrStatusPolling,
  stopIssueStatusPolling,
  stopPrStatusPolling,
} from "./poll";
import { updateGrillButton } from "./render/grill";
import { isIssuePage, isPrPage } from "./router";
import { state } from "./state";

function isIssueOpen() {
  const el = document.querySelector<HTMLElement>('[data-testid="header-state"]');
  return el?.dataset.status === "issueOpened";
}

function isPrMerged() {
  return Boolean(document.querySelector('[data-status="pullMerged"]'));
}

function startRun() {
  return startRunAction({ renderStatus, startIssueStatusPolling });
}

function startGrill() {
  return startGrillAction({ startIssueStatusPolling, updateGrillButton });
}

function startEpicArchitect() {
  return startEpicArchitectAction({ renderStatus, startIssueStatusPolling });
}

function startReview() {
  return startReviewAction({ renderPrStatus, startPrStatusPolling });
}

function startRepair() {
  return startRepairAction({ renderPrStatus, startPrStatusPolling });
}

function createIssues() {
  return startCreateIssuesAction({ renderPrStatus, startPrStatusPolling });
}

function removeInjectedControls() {
  document.getElementById(PANEL_ID)?.remove();
  document.getElementById(PR_PANEL_ID)?.remove();
  state.lastPipelineExpansionKey = null;
  disconnectGrillReplyObserver();
  stopIssueStatusPolling();
  stopPrStatusPolling();
}

function injectIssueControls() {
  if (!isIssuePage()) {
    removeInjectedControls();
    return;
  }

  const panelReady = injectIssuePanel({ startRun, startGrill, startEpicArchitect, isIssueOpen });
  if (panelReady && !state.activePoll) {
    startIssueStatusPolling();
  }
}

function injectPrControls() {
  if (!isPrPage()) {
    return false;
  }

  document.getElementById(PANEL_ID)?.remove();
  stopIssueStatusPolling();
  const panelReady = injectPrPanel({ startReview, startRepair, createIssues, isPrMerged });
  if (panelReady && !state.activePrPoll) {
    startPrStatusPolling();
  }
  return panelReady;
}

export function injectControls() {
  if (isPrPage()) {
    injectPrControls();
    return;
  }

  document.getElementById(PR_PANEL_ID)?.remove();
  stopPrStatusPolling();
  injectIssueControls();
}
