import { PIPELINE_STAGES, REPAIR_STAGES, REVIEW_STAGES, STAGE_DONE } from "../constants";
import { state } from "../state";
import { isRunDone } from "../summarize";

function stageName(stage) {
  return String(stage?.stage_name || stage?.name || "");
}

function stageStatus(stage) {
  return String(stage?.status || "pending").replace(/^[^_]+_/, "");
}

function hasSmartRoutingPlanSkippedWarning(pipeline) {
  const warnings = Array.isArray(pipeline?.warnings) ? pipeline.warnings : [];
  return warnings.some((warning) => warning?.code === "smart_routing_plan_skipped");
}

export function normalizeStepStatus(stage, isAfterActive) {
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
  if (status === "skipped") {
    return "skipped";
  }
  if (STAGE_DONE.has(status)) {
    return "done";
  }
  return "pending";
}

function collapseStages(pipeline) {
  const stages = pipeline?.stages;
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
  const repairTotal = state.config!.pipeline.verify_repair_attempts;

  const smartRoutingPlanSkipped = hasSmartRoutingPlanSkippedWarning(pipeline);

  return PIPELINE_STAGES.map((name) => {
    const matching = byName.get(name) || [];
    const stage = matching[matching.length - 1] || { stage_name: name, status: "pending" };
    const label =
      name === "implement" && repairCount > 0
        ? `${name} (repair ${repairCount}/${repairTotal})`
        : name;
    return {
      name,
      label,
      stage,
      badge: name === "plan" && smartRoutingPlanSkipped ? "micro-plan via Haiku" : "",
    };
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

export function renderStep(step, status, active) {
  const item = document.createElement("div");
  item.className = "pawchestrator-step";
  item.dataset.status = status;
  item.dataset.active = String(active);

  const indicator = document.createElement("span");
  indicator.className = "pawchestrator-step-indicator";
  indicator.textContent = status === "done" ? "\u2713" : status === "failed" ? "\u00D7" : "\u2022";

  const label = document.createElement("span");
  label.className = "pawchestrator-step-label";
  label.textContent = step.label;

  if (step.badge) {
    const badge = document.createElement("span");
    badge.className = "pawchestrator-step-badge pawchestrator-smart-routing-badge";
    badge.textContent = step.badge;
    label.append(badge);
  }

  item.append(indicator, label);
  return item;
}

export function renderNamedTimeline(parent, run, stageNames, options = {}) {
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
    timeline.append(
      renderStep(step, status, !options.suppressActive && index === activeIndex && !isRunDone(run)),
    );
  });
  parent.append(timeline);
}

export function renderTimeline(parent, pipeline, options = {}) {
  const steps = collapseStages(pipeline);
  const activeIndex = activeStageIndex(pipeline, steps);
  const timeline = document.createElement("div");
  timeline.className = "pawchestrator-timeline";
  steps.forEach((step, index) => {
    const status = normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
    timeline.append(
      renderStep(
        step,
        status,
        !options.suppressActive && index === activeIndex && pipeline.status !== "completed",
      ),
    );
  });
  parent.append(timeline);
}

export function renderReviewTimeline(parent, run) {
  renderNamedTimeline(parent, run, run.workflow_type === "repair" ? REPAIR_STAGES : REVIEW_STAGES);
}
