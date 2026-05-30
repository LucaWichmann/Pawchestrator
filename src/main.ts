import {
  API_BASE,
  CONFIRM_OVERLAY_ID,
  CONSTRUCTION,
  CREATE_ISSUES_ID,
  EPIC_ARCHITECT_ID,
  EPIC_ARCHITECT_STAGES,
  FIRE,
  GRILL_ID,
  GRILL_LABEL,
  GRILL_REPLY_TOOLTIP,
  GRILL_WAITING_STATUS,
  OFFLINE_MESSAGE,
  PANEL_ID,
  PAW,
  PIPELINE_ACTIVE,
  PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE,
  PIPELINE_STAGES,
  PLAN_APPROVAL_ID,
  PLAN_APPROVAL_MAX_ATTEMPTS,
  POLL_INTERVAL_MS,
  PR_PANEL_ID,
  PR_REPAIR_ID,
  PR_REVIEW_ID,
  REGRILL_CONFIRM_MESSAGE,
  REGRILL_LABEL,
  REINJECT_DEBOUNCE_MS,
  REPAIR_STAGES,
  REVIEW_STAGES,
  RUN_DONE,
  STAGE_DONE,
  START_ID,
  TOKEN_KEY,
  WARNING,
} from "./constants";
import { isIssuePage, isPrPage, parseIssueReference, parsePrReference } from "./router";
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

  function activePanel() {
    return document.getElementById(PANEL_ID) || document.getElementById(PR_PANEL_ID);
  }

  function prRunKey() {
    return `pawchestrator_pr_run:${window.location.pathname}`;
  }

  function epicArchitectRunKey() {
    return `pawchestrator_epic_architect_run:${window.location.pathname}`;
  }

  function findPrConversationContainer() {
    const selectors = [
      "#discussion_bucket",
      "#partial-discussion-header",
      '[data-testid="issue-viewer-issue-container"]',
      ".js-discussion",
    ];
    return selectors.map((selector) => document.querySelector(selector)).find(Boolean) || null;
  }

  function findIssueBodyContainer() {
    const selectors = [
      ".IssueBody-module__outerContainer__ULNTb",
      '[class*="IssueBody-module__outerContainer"]',
    ];
    return selectors.map((selector) => document.querySelector(selector)).find(Boolean) || null;
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
      status &&
      (status.pipeline ||
        status.grill ||
        status.epic_architect ||
        epicSubRuns(status.epic).some(
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

  function maybeAutoExpandForPipeline(status) {
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

  function currentRun(status) {
    if (!status) {
      return null;
    }
    const runs = [
      epicSummaryRun(status.epic),
      status.pipeline,
      status.grill,
      status.epic_architect,
    ].filter(Boolean);
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
        parts.push(
          questions === 0
            ? "No questions - issue ready"
            : `${questions} ${questions === 1 ? "question" : "questions"} posted`,
        );
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
    if (run.workflow_type === "epic_architect") {
      if (isRunDone(run)) {
        return run.status === "completed" || run.status === "epic_architect_complete"
          ? "Epic created"
          : summarizeError(run);
      }
      const stage = run.current_stage || "epic_scout";
      const stageStatus = (run.status || "pending").replace(/^(epic_scout|epic_architect)_/, "");
      return `[${stage}] ${stageStatus}...`;
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
    const failedVerifyCount = (byName.get("verify") || []).filter(
      (stage) => stageStatus(stage) === "failed",
    ).length;
    const repairTotal = Math.max(repairCount, failedVerifyCount);

    return PIPELINE_STAGES.map((name) => {
      const matching = byName.get(name) || [];
      const stage = matching[matching.length - 1] || { stage_name: name, status: "pending" };
      const label =
        name === "implement" && repairCount > 0
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
      stage: rows.find((stage) => stageName(stage) === name) || {
        stage_name: name,
        status: "pending",
      },
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
        !options.suppressActive && index === activeIndex && pipeline.status !== "completed",
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
    renderNamedTimeline(
      parent,
      run,
      run.workflow_type === "repair" ? REPAIR_STAGES : REVIEW_STAGES,
    );
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

  function epicSubRuns(epic) {
    return Array.isArray(epic?.sub_runs) ? epic.sub_runs : [];
  }

  function epicParentStages(epic) {
    return Array.isArray(epic?.parent_stages)
      ? epic.parent_stages.filter((stage) => {
          const name = stage.stage_name || stage.name;
          return name === "verify" || name === "implement";
        })
      : [];
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

    const parentStages = epicParentStages(epic);
    if (parentStages.length > 0) {
      const verification = document.createElement("div");
      verification.className = "pawchestrator-epic-verification";

      const verificationTitle = document.createElement("div");
      verificationTitle.className = "pawchestrator-epic-verification-title";
      verificationTitle.textContent = "Epic Verification";

      verification.append(verificationTitle);
      renderPipelineTimeline(
        verification,
        {
          stages: parentStages,
          current_stage: epic.current_stage,
          status: epic.status || epicStatus(epic),
        },
        { suppressActive: epicDone },
      );
      section.append(verification);
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
    if (
      isRunDone(run) &&
      !(run.status === "completed" || run.status === "epic_architect_complete")
    ) {
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

      const noButton = createDialogButton(options.cancelLabel || "No", "default", () =>
        close(false),
      );
      const yesButton = createDialogButton(options.confirmLabel || "Yes", "danger", () =>
        close(true),
      );
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
      status.textContent =
        "Waiting for your reply. Reply to the questions comment on GitHub to continue.";
    } else {
      status.textContent = active ? "[grill] running..." : `Status: ${grill.status || "unknown"}`;
    }
    details.append(status);

    const report = grillReport(grill);
    renderGrillDetail(
      details,
      "Criteria suggested",
      countGrillValue(grill, report, "criteria_count", "suggested_criteria"),
    );
    renderGrillDetail(
      details,
      "Questions posted",
      countGrillValue(grill, report, "questions_posted_count", "unanswerable_questions"),
    );
    renderGrillDetail(
      details,
      "Issue body updated",
      grillBodyUpdated(grill, report) ? "yes" : "no",
    );
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
      renderPlanApprovalSubView(status.plan_approval_plan, status.pipeline.run_id);
    }
    renderEpicSection(body, status.epic);
    if (status.grill?.status === "grill_waiting") {
      attachGrillReplyObserver(status.grill);
    } else {
      disconnectGrillReplyObserver();
    }
  }

  function renderOffline() {
    state.latestIssueStatus = null;
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
            reject(
              new Error(`${options.label || "Request"} returned invalid JSON: ${error.message}`),
            );
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

  async function fetchPlan(runId) {
    return requestJson(`/runs/${runId}/plan`, {
      label: "Plan request",
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

  function isPrRunActive(run) {
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

  function renderPrStatus(run) {
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
          createIssues,
        ),
      );
      section.append(issuesLine);
    }

    body.append(section);
  }

  function renderPrOffline() {
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

  function renderPrReviewState(reviewState) {
    state.latestPrReviewState = reviewState;
    updatePrActionButtons(state.latestPrRun);
    if (!state.latestPrRun && reviewState === "changes_requested") {
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

  function createButton(id, testid, labelText, onClick) {
    const button = document.createElement("button");
    if (id) {
      button.id = id;
    }
    button.type = "button";
    button.dataset.component = "Button";
    button.dataset.testid = testid;
    button.dataset.loading = "false";
    button.dataset.noVisuals = "true";
    button.dataset.size = "medium";
    button.dataset.variant = "default";
    button.dataset.idleLabel = labelText;
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
    return createButton(
      START_ID,
      "pawchestrator-work-button",
      `${PAW} Work on this issue`,
      startRun,
    );
  }

  function createReviewButton() {
    return createButton(
      PR_REVIEW_ID,
      "pawchestrator-review-button",
      `${PAW} Review with Pawchestrator`,
      startReview,
    );
  }

  function createRepairButton() {
    return createButton(
      PR_REPAIR_ID,
      "pawchestrator-repair-button",
      `${PAW} Work on Request Changes`,
      startRepair,
    );
  }

  function updatePrActionButtons(run = state.latestPrRun) {
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
    return createButton(
      GRILL_ID,
      "pawchestrator-grill-button",
      grillButtonLabel(grill),
      startGrill,
    );
  }

  function createEpicArchitectButton() {
    return createButton(
      EPIC_ARCHITECT_ID,
      "pawchestrator-epic-architect-button",
      `${CONSTRUCTION} Turn into Epic`,
      startEpicArchitect,
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
    button.toggleAttribute("disabled", Boolean(run && !isRunDone(run)) || !isIssueOpen());
    button.title = isIssueOpen() ? "" : "Issue is closed";
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

  function removeInjectedControls() {
    document.getElementById(PANEL_ID)?.remove();
    document.getElementById(PR_PANEL_ID)?.remove();
    state.lastPipelineExpansionKey = null;
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
    if (panelReady && !state.activePoll) {
      startIssueStatusPolling();
    }
  }

  function injectPrPanel() {
    const container = findPrConversationContainer();
    if (!container || !container.parentElement) {
      return false;
    }

    const existingPanel = document.getElementById(PR_PANEL_ID);
    const panel =
      existingPanel && document.contains(existingPanel) ? existingPanel : createPrPanel();
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
