import {
  EPIC_ARCHITECT_ID,
  GRILL_ID,
  GRILL_REPLY_TOOLTIP,
  POLL_INTERVAL_MS,
  START_ID,
} from "./constants";
import {
  fetchIssueStatus,
  fetchPrReviewState,
  fetchPrRun,
  fetchPrStatus,
  openRunStream,
  requestJson,
} from "./api";
import { setButtonText, setPanelSummary } from "./panel/common";
import { parseIssueReference, parsePrReference } from "./router";
import {
  renderOffline as renderIssueOffline,
  renderStatus as renderIssuePanelStatus,
} from "./panel/issue";
import { isPrRunActive, renderPrOffline, renderPrReviewState, renderPrStatus } from "./panel/pr";
import { epicSubRuns } from "./render/epic";
import { updateGrillButton } from "./render/grill";
import { renderPlanApprovalSubView, resetPlanAttemptForRun } from "./render/plan-approval";
import { currentRun, isEpicDone, isRunDone } from "./summarize";
import { state } from "./state";

const RUN_LOG_LIMIT = 200;

function isIssueOpen() {
  const el = document.querySelector('[data-testid="header-state"]');
  return el?.dataset.status === "issueOpened";
}

function prRunKey() {
  return `pawchestrator_pr_run:${window.location.pathname}`;
}

function commentElementId(commentId) {
  return `issuecomment-${commentId}`;
}

function findGrillReplyForm(commentElement) {
  if (!commentElement) {
    return null;
  }
  return (
    Array.from(commentElement.querySelectorAll("form")).find(
      (form) =>
        form.querySelector("textarea, [contenteditable='true']") && findGrillReplySubmit(form),
    ) || null
  );
}

function buttonText(button: HTMLButtonElement | HTMLInputElement) {
  return (button?.textContent || "").replace(/\s+/g, " ").trim();
}

function findGrillReplySubmit(form): HTMLButtonElement | HTMLInputElement | null {
  return (
    Array.from(
      form.querySelectorAll<HTMLButtonElement | HTMLInputElement>("button, input[type='submit']"),
    ).find((button) => {
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
    }) || null
  );
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

export function disconnectGrillReplyObserver() {
  if (state.grillReplyObserverState?.observer) {
    state.grillReplyObserverState.observer.disconnect();
  }
  state.grillReplyObserverState = null;
}

function evaluateGrillReplyForm() {
  const observerState = state.grillReplyObserverState;
  if (!observerState || !document.contains(observerState.commentElement)) {
    return;
  }

  const form = findGrillReplyForm(observerState.commentElement);
  if (form) {
    observerState.formSeen = true;
    decorateGrillReplyForm(form);
    return;
  }

  if (observerState.formSeen && !observerState.posted) {
    observerState.posted = true;
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
    state.grillReplyObserverState?.commentId === String(commentId) &&
    state.grillReplyObserverState.commentElement === commentElement
  ) {
    evaluateGrillReplyForm();
    return;
  }

  disconnectGrillReplyObserver();
  const observer = new MutationObserver(evaluateGrillReplyForm);
  state.grillReplyObserverState = {
    commentId: String(commentId),
    commentElement,
    observer,
    formSeen: false,
    posted: false,
  };
  observer.observe(commentElement, { childList: true, subtree: true });
  evaluateGrillReplyForm();
}

export async function pollPrStatusOnce() {
  const pr = parsePrReference();
  const reviewStatePromise = fetchPrReviewState(pr);
  const storedRunId = await GM_getValue(prRunKey());
  if (storedRunId) {
    const storedRun = await fetchPrRun(storedRunId);
    if (isPrRunActive(storedRun)) {
      renderPrReviewState((await reviewStatePromise).state);
      renderPrStatus(storedRun);
      return true;
    }

    const [status, reviewState] = await Promise.all([fetchPrStatus(pr), reviewStatePromise]);
    renderPrReviewState(reviewState.state);
    const activeRun = [status.repair, status.review].filter(Boolean).find(isPrRunActive);
    const run = activeRun || storedRun;
    if (activeRun?.id && activeRun.id !== storedRunId) {
      await GM_setValue(prRunKey(), activeRun.id);
    }
    renderPrStatus(run);
    return isPrRunActive(run);
  }

  const [status, reviewState] = await Promise.all([fetchPrStatus(pr), reviewStatePromise]);
  renderPrReviewState(reviewState.state);
  const run =
    [status.repair, status.review].filter(Boolean).find(isPrRunActive) ||
    status.review ||
    status.repair ||
    null;
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

export function startPrStatusPolling() {
  stopPrStatusPolling();
  pollPrStatusOnce().catch(() => renderPrOffline());
  state.activePrPoll = window.setInterval(() => {
    pollPrStatusOnce()
      .then((running) => {
        if (!running && state.activePrPoll) {
          stopPrStatusPolling();
        }
      })
      .catch(() => renderPrOffline());
  }, POLL_INTERVAL_MS);
}

export function stopPrStatusPolling() {
  if (state.activePrPoll) {
    window.clearInterval(state.activePrPoll);
    state.activePrPoll = null;
  }
}

export function renderStatus(status) {
  updateGrillButton(status.grill);
  renderIssuePanelStatus(status, {
    renderPlanApprovalSubView: (plan, runId) =>
      renderPlanApprovalSubView(plan, runId, {
        renderStatus,
        startIssueStatusPolling,
      }),
    attachGrillReplyObserver,
    disconnectGrillReplyObserver,
  });
}

function renderOffline() {
  renderIssueOffline({ disconnectGrillReplyObserver });
}

function activeIssueRun(status) {
  const run = currentRun(status);
  return run && !isRunDone(run) && run.run_id ? run : null;
}

function stopIssueStatusPollTimer() {
  if (state.activePoll) {
    window.clearInterval(state.activePoll);
    state.activePoll = null;
  }
}

function closeIssueStream() {
  if (state.activeRunStream) {
    state.activeRunStream.close();
  }
  state.activeRunStream = null;
  state.activeRunId = null;
  state.sseConnected = false;
}

function appendRunLogLine(event) {
  const stage = event.stage_name || event.stage || state.latestIssueStatus?.pipeline?.current_stage || "run";
  const message = event.message || event.line || event.text || "";
  state.runLogLines.push(`[${stage}] ${message}`);
  if (state.runLogLines.length > RUN_LOG_LIMIT) {
    state.runLogLines.splice(0, state.runLogLines.length - RUN_LOG_LIMIT);
  }
}

function mergeRunEvent(event) {
  if (!state.latestIssueStatus) {
    return;
  }
  const runId = event.run_id || state.activeRunId;
  const keys = ["pipeline", "grill", "epic_architect"];
  const key =
    keys.find((candidate) => state.latestIssueStatus?.[candidate]?.run_id === runId) || "pipeline";
  const run = state.latestIssueStatus[key] || { run_id: runId };
  const nextRun = {
    ...run,
    ...event.run,
    run_id: runId,
  };
  if (event.stage_name || event.stage) {
    nextRun.current_stage = event.stage_name || event.stage;
  }
  if (event.status) {
    nextRun.status = event.status;
  }
  if (Array.isArray(event.stages)) {
    nextRun.stages = event.stages;
  }
  if (event.pr_url) {
    nextRun.pr_url = event.pr_url;
  }
  if (event.warning) {
    nextRun.warnings = [...(Array.isArray(run.warnings) ? run.warnings : []), event.warning];
  } else if (event.message && event.type === "warning") {
    nextRun.warnings = [
      ...(Array.isArray(run.warnings) ? run.warnings : []),
      { stage_name: event.stage_name, code: event.code, message: event.message },
    ];
  }
  state.latestIssueStatus = {
    ...state.latestIssueStatus,
    [key]: nextRun,
  };
}

function parseRunStreamEvent(message) {
  try {
    return JSON.parse(message.data || "{}");
  } catch {
    return {};
  }
}

function handleRunStreamEvent(kind, message) {
  const event = { ...parseRunStreamEvent(message), type: kind };
  if (kind === "log_line") {
    appendRunLogLine(event);
  } else {
    mergeRunEvent(event);
  }
  if (state.latestIssueStatus) {
    renderStatus(state.latestIssueStatus);
  }
  if (kind === "run_complete" || kind === "run_failed") {
    closeIssueStream();
    startIssueStatusPolling();
  }
}

async function openIssueStream(runId) {
  closeIssueStream();
  stopIssueStatusPollTimer();
  state.activeRunId = runId;
  const stream = openRunStream(runId);
  state.activeRunStream = stream;
  stream.onopen = () => {
    state.sseConnected = true;
    stopIssueStatusPollTimer();
  };
  stream.onerror = () => {
    closeIssueStream();
    startIssueStatusPolling();
  };
  ["stage_transition", "warning", "run_complete", "run_failed", "log_line"].forEach((kind) => {
    stream.addEventListener(kind, (message) => handleRunStreamEvent(kind, message));
  });
}

function maybeSwitchToRunStream(status) {
  const run = activeIssueRun(status);
  if (!run) {
    if (state.activeRunStream) {
      closeIssueStream();
    }
    return false;
  }
  if (state.activeRunId === run.run_id && state.activeRunStream) {
    return true;
  }
  state.runLogLines = [];
  openIssueStream(run.run_id).catch(() => startIssueStatusPolling());
  return true;
}

export async function pollIssueStatusOnce() {
  if (state.activeRunStream && state.sseConnected) {
    return true;
  }
  const issue = parseIssueReference();
  const status = await fetchIssueStatus(issue);
  if (status.pipeline?.run_id && status.pipeline.run_id !== state.planAttemptRunId) {
    resetPlanAttemptForRun(status.pipeline.run_id);
  }
  if (status.pipeline?.status === "awaiting_plan_approval" && status.pipeline.run_id) {
    if (state.rejectedPlanRunIds.has(status.pipeline.run_id)) {
      state.planAttempt += 1;
      state.rejectedPlanRunIds.delete(status.pipeline.run_id);
    }
    status.plan_approval_plan = await requestJson(`/runs/${status.pipeline.run_id}/plan`, {
      label: "Plan request",
    });
  }
  renderStatus(status);
  const streaming = maybeSwitchToRunStream(status);
  const run = currentRun(status);
  const running = run && !isRunDone(run);
  const issueOpen = isIssueOpen();
  const anyActive = Boolean(
    (status.pipeline && !isRunDone(status.pipeline)) ||
    (status.grill && !isRunDone(status.grill)) ||
    (status.epic_architect && !isRunDone(status.epic_architect)) ||
    (status.epic && !isEpicDone(status.epic)) ||
    (!isEpicDone(status.epic) && epicSubRuns(status.epic).some((run) => !isRunDone(run))),
  );
  const shouldDisable = !issueOpen || anyActive;
  const closedTitle = !issueOpen ? "Issue is closed" : "";
  for (const id of [START_ID, GRILL_ID, EPIC_ARCHITECT_ID]) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.toggleAttribute("disabled", shouldDisable);
    btn.title = closedTitle;
  }
  return streaming || running;
}

export function startIssueStatusPolling() {
  stopIssueStatusPollTimer();
  pollIssueStatusOnce().catch(() => renderOffline());
  state.activePoll = window.setInterval(() => {
    pollIssueStatusOnce().catch(() => {
      renderOffline();
      if (isIssueOpen()) {
        document.getElementById(START_ID)?.removeAttribute("disabled");
        document.getElementById(GRILL_ID)?.removeAttribute("disabled");
        document.getElementById(EPIC_ARCHITECT_ID)?.removeAttribute("disabled");
      }
    });
  }, POLL_INTERVAL_MS);
}

export function stopIssueStatusPolling() {
  stopIssueStatusPollTimer();
  closeIssueStream();
}
