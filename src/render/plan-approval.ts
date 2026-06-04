import { PANEL_ID, PLAN_APPROVAL_ID } from "../constants";
import { requestJson } from "../api";
import { state } from "../state";
import { createButton, setButtonText, setPanelExpanded } from "../panel/common";

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

function isSmartRoutingMicroPlanApproval() {
  if (!state.config?.pipeline.smart_routing?.confirm_skip) {
    return false;
  }

  const warnings = Array.isArray(state.latestIssueStatus?.pipeline?.warnings)
    ? state.latestIssueStatus.pipeline.warnings
    : [];
  return warnings.some((warning) => warning?.code === "smart_routing_plan_skipped");
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

export function removePlanApprovalSubView() {
  document.getElementById(PLAN_APPROVAL_ID)?.remove();
}

export function resetPlanAttemptForRun(runId) {
  if (runId && state.planAttemptRunId !== runId) {
    state.planAttemptRunId = runId;
    state.planAttempt = 1;
    state.rejectedPlanRunIds.clear();
  }
}

export function renderRePlanningState() {
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

export async function renderPlanApprovalSection(runId, callbacks = {}) {
  const plan = await requestJson(`/runs/${runId}/plan`, {
    label: "Plan request",
  });
  renderPlanApprovalSubView(plan, runId, callbacks);
}

export function renderPlanApprovalSubView(plan, runId, callbacks = {}) {
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
  const maxPlanAttempts = state.config!.pipeline.plan_approval_max_attempts;
  attempt.textContent = `Plan attempt ${state.planAttempt} of ${maxPlanAttempts}`;
  const risk = String(plan?.estimated_risk || "medium").toLowerCase();
  const badge = document.createElement("span");
  badge.className = `risk-badge risk-${["low", "medium", "high"].includes(risk) ? risk : "medium"}`;
  badge.textContent = `Risk: ${risk}`;
  header.append(title, attempt, badge);
  view.append(header);

  if (isSmartRoutingMicroPlanApproval()) {
    const note = document.createElement("div");
    note.className = "pawchestrator-plan-approval-context";
    note.textContent =
      "Micro-plan generated (smart routing) \u2014 approve to proceed or reject to run full plan.";
    view.append(note);
  }

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
        callbacks,
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
    handlePlanApprovalAction(runId, "abort", abortBtn, approveBtn, error, callbacks);
  });
  abortBtn.classList.add("btn-danger");
  const rejectBtn = createButton("", "pawchestrator-plan-reject-button", "Reject", () => {
    rejectBtn.style.display = "none";
    feedbackArea.style.display = "grid";
    feedback.focus();
  });
  const approveBtn = createButton("", "pawchestrator-plan-approve-button", "Approve", () => {
    handlePlanApprovalAction(runId, "approve", approveBtn, abortBtn, error, callbacks);
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
  callbacks,
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
    callbacks.startIssueStatusPolling?.();
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
  callbacks,
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
      callbacks.renderStatus?.({
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
    callbacks.startIssueStatusPolling?.();
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.hidden = false;
    setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, false);
  }
}
