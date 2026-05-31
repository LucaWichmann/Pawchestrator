export const API_BASE = "http://127.0.0.1:38472";
export const PANEL_ID = "pawchestrator-panel";
export const PR_PANEL_ID = "pawchestrator-pr-panel";
export const START_ID = "pawchestrator-start";
export const GRILL_ID = "pawchestrator-grill";
export const EPIC_ARCHITECT_ID = "pawchestrator-epic-architect";
export const PR_REVIEW_ID = "pawchestrator-review";
export const PR_REPAIR_ID = "pawchestrator-repair";
export const CREATE_ISSUES_ID = "pawchestrator-create-issues";
export const PLAN_APPROVAL_ID = "pawchestrator-plan-approval";
export const CONFIRM_OVERLAY_ID = "pawchestrator-confirm-overlay";
export const POLL_INTERVAL_MS = 3000;
export const REINJECT_DEBOUNCE_MS = 100;
export const TOKEN_KEY = "pawchestrator_token";
export const PIPELINE_STAGES = ["snapshot", "scout", "plan", "implement", "verify", "pr"];
export const REVIEW_STAGES = ["review", "post", "issues"];
export const REPAIR_STAGES = ["repair", "push"];
export const EPIC_ARCHITECT_STAGES = ["epic_scout", "epic_architect", "creating"];
export const PAW = "\uD83D\uDC3E";
export const FIRE = "\uD83D\uDD25";
export const CONSTRUCTION = "\uD83C\uDFD7\uFE0F";
export const WARNING = "\u26A0";
export const OFFLINE_MESSAGE = "Pawchestrator not running \u2014 start with `pawchestrator serve`";
export const GRILL_WAITING_STATUS = "grill_waiting";
export const GRILL_LABEL = `${FIRE} Grill Issue`;
export const REGRILL_LABEL = `${FIRE} Re-grill`;
export const REGRILL_CONFIRM_MESSAGE =
  "Grill is still waiting for answers on this issue. Are you sure you want to re-grill?";
export const PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE =
  "Grill is still waiting for answers on this issue. Are you sure you want to start agentic work?";
export const RUN_DONE = new Set([
  "completed",
  "failed",
  "grill_complete",
  "grill_failed",
  "epic_complete",
  "epic_failed",
  "post_complete",
  "post_failed",
  "issues_complete",
  "issues_failed",
  "issues_skipped",
  "review_failed",
  "repair_complete",
  "repair_failed",
  "push_complete",
  "push_failed",
  "epic_architect_complete",
  "epic_architect_failed",
]);
export const PIPELINE_ACTIVE = new Set([
  "snapshot_running",
  "snapshot_complete",
  "scout_running",
  "scout_complete",
  "plan_running",
  "plan_complete",
  "awaiting_plan_approval",
  "implement_running",
  "implement_complete",
  "verify_running",
  "verify_complete",
  "verify_skipped",
  "pr_running",
  "pr_complete",
  "completed",
]);
export const STAGE_DONE = new Set(["complete", "completed", "skipped"]);
export const GRILL_REPLY_TOOLTIP =
  "Replying to Pawchestrator questions \u2014 submitting will continue the grilling session.";
