import { EPIC_ARCHITECT_ID, EPIC_ARCHITECT_STAGES } from "../constants";
import { getOrAcquireToken, requestJson } from "../api";
import { setPanelExpanded, setPanelSummary } from "../panel/common";
import { parseIssueReference } from "../router";
import { state } from "../state";

type EpicArchitectActionOptions = {
  renderStatus: (status: any) => void;
  startIssueStatusPolling: () => void;
};

function epicArchitectRunKey() {
  return `pawchestrator_epic_architect_run:${window.location.pathname}`;
}

export async function startEpicArchitect({
  renderStatus,
  startIssueStatusPolling,
}: EpicArchitectActionOptions) {
  const button = document.getElementById(EPIC_ARCHITECT_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}
