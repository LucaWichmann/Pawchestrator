import {
  GRILL_WAITING_STATUS,
  PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE,
  PIPELINE_STAGES,
  START_ID,
} from "../constants";
import { fetchIssueStatus, getOrAcquireToken, requestJson } from "../api";
import { setPanelExpanded, setPanelSummary } from "../panel/common";
import { showConfirmDialog } from "../panel/confirm";
import { parseIssueReference } from "../router";
import { state } from "../state";

type RunActionOptions = {
  renderStatus: (status: any) => void;
  startIssueStatusPolling: () => void;
};

export async function startRun({ renderStatus, startIssueStatusPolling }: RunActionOptions) {
  const button = document.getElementById(START_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}

export function confirmEpicStart(epic: any) {
  const runs = epic?.sub_runs || [];
  const lines = runs.map((run: any) => {
    const title = run.title ? ` ${run.title}` : "";
    return `#${run.issue_number}${title}`;
  });
  const list = lines.length > 0 ? `\n\n${lines.join("\n")}` : "";
  return window.confirm(`Work on this epic issue and its sub-issues?${list}`);
}

export function epicFromStartResponse(response: any) {
  return {
    run_id: response.run_id,
    group_id: response.group_id,
    status: "epic_running",
    mode: response.mode,
    branch: response.branch,
    pr_url: response.pr_url,
    sub_runs: (response.sub_runs || []).map((run: any) => ({
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
