import { GRILL_ID, GRILL_WAITING_STATUS, REGRILL_CONFIRM_MESSAGE } from "../constants";
import { fetchIssueStatus, getOrAcquireToken, requestJson } from "../api";
import { setPanelExpanded, setPanelSummary } from "../panel/common";
import { showConfirmDialog } from "../panel/confirm";
import { parseIssueReference } from "../router";
import { state } from "../state";

type GrillActionOptions = {
  startIssueStatusPolling: () => void;
  updateGrillButton: (grill: any) => void;
};

export async function startGrill({
  startIssueStatusPolling,
  updateGrillButton,
}: GrillActionOptions) {
  const button = document.getElementById(GRILL_ID) as HTMLButtonElement | null;
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
    setPanelSummary((error as Error).message);
    if (button) {
      button.disabled = false;
    }
  }
}
