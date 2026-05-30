import { isEpicDone, isRunDone } from "../summarize";

export function epicSubRuns(epic) {
  return Array.isArray(epic?.sub_runs) ? epic.sub_runs : [];
}

export function epicStatus(epic) {
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

export function epicSummaryRun(epic) {
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
  if (status === "running" || status === "failed" || status === "skipped") {
    return status;
  }
  return ["complete", "completed", "done", "success", "skipped"].includes(status)
    ? "done"
    : "pending";
}

function collapseEpicStages(stages) {
  const names = ["snapshot", "scout", "plan", "implement", "verify", "pr"];
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

function activeEpicStageIndex(run, steps) {
  const failedIndex = steps.findIndex((step) => stageStatus(step.stage) === "failed");
  if (failedIndex >= 0) {
    return failedIndex;
  }
  const current = String(run.current_stage || "");
  const currentIndex = steps.findIndex((step) => step.name === current);
  if (currentIndex >= 0) {
    return currentIndex;
  }
  return steps.findIndex((step) => stageStatus(step.stage) === "running");
}

function renderEpicTimeline(parent, run, options = {}) {
  const steps = collapseEpicStages(run.stages);
  const activeIndex = activeEpicStageIndex(run, steps);
  const timeline = document.createElement("div");
  timeline.className = "pawchestrator-timeline";
  steps.forEach((step, index) => {
    const status = normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
    const item = document.createElement("div");
    item.className = "pawchestrator-step";
    item.dataset.status = status;
    item.dataset.active = String(
      !options.suppressActive && index === activeIndex && run.status !== "completed",
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

function epicParentStages(epic) {
  return Array.isArray(epic?.parent_stages)
    ? epic.parent_stages.filter((stage) => {
        const name = stage.stage_name || stage.name;
        return name === "verify" || name === "implement";
      })
    : [];
}

export function renderEpicSection(parent, epic) {
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
    renderEpicTimeline(row, subRun, { suppressActive: epicDone });
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
    renderEpicTimeline(
      verification,
      {
        stages: parentStages,
        current_stage: epic.current_stage,
        status: epic.status || epicStatus(epic),
      },
      { suppressActive: epicDone || isRunDone(epic.status) },
    );
    section.append(verification);
  }

  parent.append(section);
}
