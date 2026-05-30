import { RUN_DONE } from "./constants";

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

export function currentRun(status) {
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

export function isRunDone(run) {
  const status = typeof run === "string" ? run : run?.status;
  return Boolean(status && (RUN_DONE.has(status) || /_failed$/.test(status)));
}

export function isEpicDone(epic) {
  return Boolean(epic && isRunDone(epic.status || epicStatus(epic)));
}

export function summarizeError(run) {
  const failedStage = (run.stages || []).find((stage) => stage.status === "failed");
  if (!failedStage) {
    return "Run failed";
  }

  const stageName = failedStage.stage_name || failedStage.name || run.current_stage || "unknown";
  const error = failedStage.error ? `: ${failedStage.error}` : "";
  return `[${stageName}] failed${error}`;
}

export function summarizeGrillCompletion(run) {
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

export function summarizeRun(run) {
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
