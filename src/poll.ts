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

export async function pollIssueStatusOnce() {
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
  return running;
}

export function startIssueStatusPolling() {
  stopIssueStatusPolling();
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
  if (state.activePoll) {
    window.clearInterval(state.activePoll);
    state.activePoll = null;
  }
}
