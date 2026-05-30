import {
  CREATE_ISSUES_ID,
  PR_REPAIR_ID,
  PR_REVIEW_ID,
  REPAIR_STAGES,
  REVIEW_STAGES,
} from "../constants";
import { getOrAcquireToken, requestJson } from "../api";
import { setPanelExpanded, setPanelSummary } from "../panel/common";
import { parsePrReference } from "../router";
import { state } from "../state";

type ReviewActionOptions = {
  renderPrStatus: (run: any) => void;
  startPrStatusPolling: () => void;
};

function prRunKey() {
  return `pawchestrator_pr_run:${window.location.pathname}`;
}

export async function startReview({ renderPrStatus, startPrStatusPolling }: ReviewActionOptions) {
  const button = document.getElementById(PR_REVIEW_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}

export async function startRepair({ renderPrStatus, startPrStatusPolling }: ReviewActionOptions) {
  const button = document.getElementById(PR_REPAIR_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}

export async function startCreateIssues({
  renderPrStatus,
  startPrStatusPolling,
}: ReviewActionOptions) {
  const runId =
    state.latestPrRun?.id || state.latestPrRun?.run_id || (await GM_getValue(prRunKey()));
  const button = document.getElementById(CREATE_ISSUES_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}
