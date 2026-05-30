import {
  CREATE_ISSUES_ID,
  EPIC_ARCHITECT_ID,
  EPIC_ARCHITECT_STAGES,
  GRILL_ID,
  GRILL_REPLY_TOOLTIP,
  GRILL_WAITING_STATUS,
  PANEL_ID,
  PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE,
  PIPELINE_STAGES,
  POLL_INTERVAL_MS,
  PR_PANEL_ID,
  PR_REPAIR_ID,
  PR_REVIEW_ID,
  REGRILL_CONFIRM_MESSAGE,
  REINJECT_DEBOUNCE_MS,
  REPAIR_STAGES,
  REVIEW_STAGES,
  START_ID,
} from "./constants";
import {
  fetchIssueStatus,
  fetchPrReviewState,
  fetchPrRun,
  fetchPrStatus,
  getOrAcquireToken,
  requestJson,
} from "./api";
import { setButtonText, setPanelExpanded, setPanelSummary } from "./panel/common";
import { isIssuePage, isPrPage, parseIssueReference, parsePrReference } from "./router";
import { showConfirmDialog } from "./panel/confirm";
import {
  injectIssuePanel,
  renderOffline as renderIssueOffline,
  renderStatus as renderIssuePanelStatus,
} from "./panel/issue";
import {
  injectPrPanel,
  isPrRunActive,
  renderPrOffline,
  renderPrReviewState,
  renderPrStatus,
} from "./panel/pr";
import { epicSubRuns } from "./render/epic";
import { updateGrillButton } from "./render/grill";
import { renderPlanApprovalSubView, resetPlanAttemptForRun } from "./render/plan-approval";
import { currentRun, isEpicDone, isRunDone } from "./summarize";
import { state } from "./state";
import { injectStyles } from "./styles";

(function () {
  "use strict";

  injectStyles();

  function isIssueOpen() {
    const el = document.querySelector('[data-testid="header-state"]');
    return el?.dataset.status === "issueOpened";
  }

  function isPrMerged() {
    return Boolean(document.querySelector('[data-status="pullMerged"]'));
  }

  function prRunKey() {
    return `pawchestrator_pr_run:${window.location.pathname}`;
  }

  function epicArchitectRunKey() {
    return `pawchestrator_epic_architect_run:${window.location.pathname}`;
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

  function buttonText(button) {
    return (button?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function findGrillReplySubmit(form) {
    return (
      Array.from(form.querySelectorAll("button, input[type='submit']")).find((button) => {
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

  function disconnectGrillReplyObserver() {
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

  async function pollPrStatusOnce() {
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

  function startPrStatusPolling() {
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

  function stopPrStatusPolling() {
    if (state.activePrPoll) {
      window.clearInterval(state.activePrPoll);
      state.activePrPoll = null;
    }
  }

  function renderStatus(status) {
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

  async function pollIssueStatusOnce() {
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

  function startIssueStatusPolling() {
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

  function stopIssueStatusPolling() {
    if (state.activePoll) {
      window.clearInterval(state.activePoll);
      state.activePoll = null;
    }
  }

  async function startRun() {
    const button = document.getElementById(START_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const issue = parseIssueReference();
      await getOrAcquireToken(setPanelSummary);
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
      state.panelExpandedByUser = true;
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
      const status = state.latestIssueStatus || (await fetchIssueStatus(issue));
      state.latestIssueStatus = status;
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
      state.panelExpandedByUser = true;
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

  async function startEpicArchitect() {
    const button = document.getElementById(EPIC_ARCHITECT_ID);
    if (button) {
      button.disabled = true;
    }

    try {
      const issue = parseIssueReference();
      await getOrAcquireToken(setPanelSummary);
      setPanelSummary("[epic_scout] starting...");
      state.panelExpandedByUser = true;
      setPanelExpanded(true);
      const response = await requestJson("/issue/epic-architect", {
        method: "POST",
        label: "EpicArchitect request",
        statusSetter: setPanelSummary,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(issue),
      });
      await GM_setValue(epicArchitectRunKey(), response.run_id);
      renderStatus({
        ...(state.latestIssueStatus || {}),
        epic_architect: {
          run_id: response.run_id,
          workflow_type: "epic_architect",
          status: "epic_scout_running",
          current_stage: "epic_scout",
          stages: EPIC_ARCHITECT_STAGES.map((stage_name) => ({
            stage_name,
            status: stage_name === "epic_scout" ? "running" : "pending",
          })),
          created_sub_issues: [],
        },
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
      state.panelExpandedByUser = true;
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
      state.panelExpandedByUser = true;
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
    const runId =
      state.latestPrRun?.id || state.latestPrRun?.run_id || (await GM_getValue(prRunKey()));
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
    const pathnameChanged = state.activePathname !== window.location.pathname;
    if (pathnameChanged) {
      state.activePathname = window.location.pathname;
      state.panelExpandedByUser = null;
      state.lastPipelineExpansionKey = null;
      stopIssueStatusPolling();
      stopPrStatusPolling();
    }

    if (state.reinjectTimer) {
      window.clearTimeout(state.reinjectTimer);
    }

    state.reinjectTimer = window.setTimeout(
      () => {
        state.reinjectTimer = null;
        injectControls();
      },
      pathnameChanged ? 0 : REINJECT_DEBOUNCE_MS,
    );
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
