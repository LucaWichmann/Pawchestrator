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
  PLAN_APPROVAL_ID,
  PLAN_APPROVAL_MAX_ATTEMPTS,
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
  fetchPlan,
  fetchPrReviewState,
  fetchPrRun,
  fetchPrStatus,
  getOrAcquireToken,
  requestJson,
} from "./api";
import { createButton, setButtonText, setPanelExpanded, setPanelSummary } from "./panel/common";
import { isIssuePage, isPrPage, parseIssueReference, parsePrReference } from "./router";
import { showConfirmDialog } from "./panel/confirm";
import {
  injectIssuePanel,
  renderOffline as renderIssueOffline,
  renderStatus as renderIssuePanelStatus,
  updateGrillButton,
} from "./panel/issue";
import {
  injectPrPanel,
  isPrRunActive,
  renderPrOffline,
  renderPrReviewState,
  renderPrStatus,
} from "./panel/pr";
import { currentRun, epicSubRuns, isEpicDone, isRunDone } from "./summarize";
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

  function normalizePlanItems(items) {
    return Array.isArray(items) ? items : [];
  }

  function planFileOperations(plan) {
    return normalizePlanItems(plan?.file_operations || plan?.files || plan?.files_to_modify);
  }

  function operationType(operation) {
    return String(operation?.type || operation?.operation || "modify").toLowerCase();
  }

  function operationPath(operation) {
    return operation?.path || operation?.file_path || operation?.file || String(operation || "");
  }

  function operationDescription(operation) {
    return operation?.description || operation?.summary || "";
  }

  function renderPlanFileSection(parent, titleText, operations) {
    if (operations.length === 0) {
      return;
    }

    const title = document.createElement("h4");
    title.className = "pawchestrator-plan-approval-section-title";
    title.textContent = titleText;
    parent.append(title);

    const list = document.createElement("ul");
    list.className = "pawchestrator-plan-approval-list";
    operations.forEach((operation) => {
      const item = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = operationPath(operation);
      item.append(code);
      const description = operationDescription(operation);
      if (description) {
        item.append(document.createTextNode(` - ${description}`));
      }
      list.append(item);
    });
    parent.append(list);
  }

  function removePlanApprovalSubView() {
    document.getElementById(PLAN_APPROVAL_ID)?.remove();
  }

  function resetPlanAttemptForRun(runId) {
    if (runId && state.planAttemptRunId !== runId) {
      state.planAttemptRunId = runId;
      state.planAttempt = 1;
      state.rejectedPlanRunIds.clear();
    }
  }

  function renderRePlanningState() {
    const panel = document.getElementById(PANEL_ID);
    const body = panel?.querySelector(".pawchestrator-panel-body");
    if (!body) {
      return;
    }

    removePlanApprovalSubView();
    const view = document.createElement("div");
    view.id = PLAN_APPROVAL_ID;
    view.className = "re-planning";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    view.append(spinner, document.createTextNode(" Re-planning\u2026"));
    body.append(view);
    setPanelExpanded(true);
  }

  function renderPlanApprovalSubView(plan, runId) {
    resetPlanAttemptForRun(runId);
    const panel = document.getElementById(PANEL_ID);
    const body = panel?.querySelector(".pawchestrator-panel-body");
    if (!body) {
      return;
    }

    removePlanApprovalSubView();
    const view = document.createElement("div");
    view.id = PLAN_APPROVAL_ID;

    const header = document.createElement("div");
    header.className = "pawchestrator-plan-approval-header";
    const title = document.createElement("h4");
    title.className = "pawchestrator-plan-approval-title";
    title.textContent = "Plan Approval";
    const attempt = document.createElement("span");
    attempt.className = "pawchestrator-plan-approval-attempt";
    attempt.textContent = `Plan attempt ${state.planAttempt} of ${PLAN_APPROVAL_MAX_ATTEMPTS}`;
    const risk = String(plan?.estimated_risk || "medium").toLowerCase();
    const badge = document.createElement("span");
    badge.className = `risk-badge risk-${["low", "medium", "high"].includes(risk) ? risk : "medium"}`;
    badge.textContent = `Risk: ${risk}`;
    header.append(title, attempt, badge);
    view.append(header);

    const summary = document.createElement("div");
    summary.className = "pawchestrator-plan-approval-summary prc-Text-Text-0ima0";
    summary.textContent = plan?.approach_summary || "";
    view.append(summary);

    const filesTitle = document.createElement("h4");
    filesTitle.className = "pawchestrator-plan-approval-section-title";
    filesTitle.textContent = "Files";
    view.append(filesTitle);

    const operations = planFileOperations(plan);
    const grouped = {
      Modify: operations.filter((operation) => operationType(operation) === "modify"),
      Create: operations.filter((operation) => operationType(operation) === "create"),
      Delete: operations.filter((operation) => operationType(operation) === "delete"),
    };
    renderPlanFileSection(view, "Modify", grouped.Modify);
    renderPlanFileSection(view, "Create", grouped.Create);
    renderPlanFileSection(view, "Delete", grouped.Delete);

    const stepsTitle = document.createElement("h4");
    stepsTitle.className = "pawchestrator-plan-approval-section-title";
    stepsTitle.textContent = "Steps";
    view.append(stepsTitle);

    const steps = document.createElement("ol");
    steps.className = "pawchestrator-plan-approval-list";
    normalizePlanItems(plan?.steps).forEach((step) => {
      const item = document.createElement("li");
      const description = document.createElement("div");
      description.textContent = step?.description || String(step || "");
      item.append(description);

      const affectedFiles = normalizePlanItems(
        step?.affected_files || step?.files_to_modify || step?.files,
      );
      if (affectedFiles.length > 0) {
        const files = document.createElement("div");
        files.className = "pawchestrator-plan-step-files";
        files.textContent = `Affected files: ${affectedFiles.join(", ")}`;
        item.append(files);
      }

      if (step?.notes) {
        const notes = document.createElement("div");
        notes.className = "pawchestrator-plan-step-notes";
        notes.textContent = step.notes;
        item.append(notes);
      }
      steps.append(item);
    });
    view.append(steps);

    const error = document.createElement("div");
    error.className = "pawchestrator-plan-approval-error";
    error.hidden = true;
    view.append(error);

    const feedbackArea = document.createElement("div");
    feedbackArea.className = "pawchestrator-plan-feedback";
    feedbackArea.style.display = "none";
    const feedback = document.createElement("textarea");
    feedback.placeholder = "Describe what should change\u2026";
    const feedbackActions = document.createElement("div");
    feedbackActions.className = "pawchestrator-plan-approval-actions";
    const cancelBtn = createButton("", "pawchestrator-plan-reject-cancel-button", "Cancel", () => {
      feedback.value = "";
      error.hidden = true;
      error.textContent = "";
      feedbackArea.style.display = "none";
      rejectBtn.style.display = "";
    });
    const submitFeedbackBtn = createButton(
      "",
      "pawchestrator-plan-submit-feedback-button",
      "Submit Feedback",
      () => {
        handlePlanRejection(
          runId,
          feedback.value,
          submitFeedbackBtn,
          cancelBtn,
          feedbackArea,
          error,
        );
      },
    );
    submitFeedbackBtn.disabled = true;
    feedback.addEventListener("input", () => {
      submitFeedbackBtn.disabled = feedback.value.trim().length === 0;
    });
    feedbackActions.append(cancelBtn, submitFeedbackBtn);
    feedbackArea.append(feedback, feedbackActions);
    view.append(feedbackArea);

    const actions = document.createElement("div");
    actions.className = "pawchestrator-plan-approval-actions";
    const abortBtn = createButton("", "pawchestrator-plan-abort-button", "Abort", () => {
      handlePlanApprovalAction(runId, "abort", abortBtn, approveBtn, error);
    });
    abortBtn.classList.add("btn-danger");
    const rejectBtn = createButton("", "pawchestrator-plan-reject-button", "Reject", () => {
      rejectBtn.style.display = "none";
      feedbackArea.style.display = "grid";
      feedback.focus();
    });
    const approveBtn = createButton("", "pawchestrator-plan-approve-button", "Approve", () => {
      handlePlanApprovalAction(runId, "approve", approveBtn, abortBtn, error);
    });
    approveBtn.classList.add("btn-primary");
    actions.append(abortBtn, rejectBtn, approveBtn);
    view.append(actions);

    body.append(view);
    setPanelExpanded(true);
  }

  function setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, disabled) {
    [primaryButton, secondaryButton].forEach((button) => {
      button.disabled = disabled;
    });
    setButtonText(primaryButton, disabled ? "\u2026" : primaryButton.dataset.idleLabel);
    setButtonText(secondaryButton, secondaryButton.dataset.idleLabel);
  }

  function setPlanFeedbackButtonsDisabled(submitButton, cancelButton, disabled) {
    submitButton.disabled = disabled;
    cancelButton.disabled = disabled;
    setButtonText(submitButton, disabled ? "\u2026" : submitButton.dataset.idleLabel);
  }

  async function handlePlanRejection(
    runId,
    feedback,
    submitButton,
    cancelButton,
    feedbackArea,
    errorElement,
  ) {
    const trimmedFeedback = feedback.trim();
    if (!runId) {
      errorElement.textContent = "No run id found for this plan approval.";
      errorElement.hidden = false;
      return;
    }
    if (!trimmedFeedback) {
      submitButton.disabled = true;
      return;
    }

    errorElement.hidden = true;
    errorElement.textContent = "";
    setPlanFeedbackButtonsDisabled(submitButton, cancelButton, true);
    try {
      await requestJson(`/runs/${runId}/reject`, {
        method: "POST",
        body: JSON.stringify({ feedback: trimmedFeedback }),
        label: "Plan rejection request",
      });
      state.rejectedPlanRunIds.add(runId);
      feedbackArea.style.display = "none";
      renderRePlanningState();
      startIssueStatusPolling();
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      feedbackArea.style.display = "grid";
      setPlanFeedbackButtonsDisabled(submitButton, cancelButton, false);
      submitButton.disabled = trimmedFeedback.length === 0;
    }
  }

  async function handlePlanApprovalAction(
    runId,
    action,
    primaryButton,
    secondaryButton,
    errorElement,
  ) {
    if (!runId) {
      errorElement.textContent = "No run id found for this plan approval.";
      errorElement.hidden = false;
      return;
    }

    errorElement.hidden = true;
    errorElement.textContent = "";
    setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, true);
    try {
      const run = await requestJson(`/runs/${runId}/${action}`, {
        method: "POST",
        label: `Plan ${action} request`,
      });
      removePlanApprovalSubView();
      if (action === "abort") {
        renderStatus({
          ...(state.latestIssueStatus || {}),
          pipeline: {
            ...((state.latestIssueStatus || {}).pipeline || {}),
            ...(run || {}),
            run_id: run?.run_id || run?.id || runId,
            status: run?.status || "failed",
          },
        });
        return;
      }
      startIssueStatusPolling();
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, false);
    }
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
      renderPlanApprovalSubView,
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
      status.plan_approval_plan = await fetchPlan(status.pipeline.run_id);
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
