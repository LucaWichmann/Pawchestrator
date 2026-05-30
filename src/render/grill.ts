import { GRILL_ID, GRILL_LABEL, GRILL_WAITING_STATUS, REGRILL_LABEL } from "../constants";
import { isRunDone, summarizeError } from "../summarize";
import { setButtonText } from "../panel/common";

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

export function renderGrillDetail(parent, label, value) {
  const item = document.createElement("div");
  item.textContent = `${label}: ${value}`;
  parent.append(item);
}

export function renderGrillSection(parent, grill) {
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
  renderGrillDetail(details, "Issue body updated", grillBodyUpdated(grill, report) ? "yes" : "no");
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

export function grillButtonLabel(grill) {
  return grill?.status === GRILL_WAITING_STATUS ? REGRILL_LABEL : GRILL_LABEL;
}

export function updateGrillButton(grill) {
  const button = document.getElementById(GRILL_ID);
  if (button) {
    setButtonText(button, grillButtonLabel(grill));
  }
}
