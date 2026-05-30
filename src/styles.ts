import {
  CONFIRM_OVERLAY_ID,
  CREATE_ISSUES_ID,
  EPIC_ARCHITECT_ID,
  GRILL_ID,
  PANEL_ID,
  PLAN_APPROVAL_ID,
  PR_PANEL_ID,
  PR_REPAIR_ID,
  PR_REVIEW_ID,
  START_ID,
} from "./constants";

export function injectStyles() {
  GM_addStyle(`
    #${START_ID},
    #${GRILL_ID},
    #${EPIC_ARCHITECT_ID},
    #${PR_REVIEW_ID},
    #${PR_REPAIR_ID},
    #${CREATE_ISSUES_ID} {
      white-space: nowrap;
    }

    #${START_ID}:disabled,
    #${GRILL_ID}:disabled,
    #${EPIC_ARCHITECT_ID}:disabled,
    #${PR_REVIEW_ID}:disabled,
    #${PR_REPAIR_ID}:disabled,
    #${CREATE_ISSUES_ID}:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }

    #${PANEL_ID},
    #${PR_PANEL_ID} {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-left: 4px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font-size: 13px;
      line-height: 20px;
      margin: 8px 0 16px;
    }

    #${PANEL_ID}[data-status="idle"],
    #${PANEL_ID}[data-status="offline"],
    #${PR_PANEL_ID}[data-status="idle"],
    #${PR_PANEL_ID}[data-status="offline"] {
      border-left-color: var(--borderColor-default, #d0d7de);
    }

    #${PANEL_ID}[data-status="running"],
    #${PR_PANEL_ID}[data-status="running"] {
      border-left-color: var(--fgColor-accent, #0969da);
    }

    #${PANEL_ID}[data-status="done"],
    #${PR_PANEL_ID}[data-status="done"] {
      border-left-color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID}[data-status="failed"],
    #${PR_PANEL_ID}[data-status="failed"] {
      border-left-color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-panel-bar,
    #${PR_PANEL_ID} .pawchestrator-panel-bar {
      align-items: center;
      display: flex;
      gap: 8px;
      min-height: 38px;
      padding: 8px 12px;
    }

    #${PANEL_ID} .pawchestrator-panel-toggle,
    #${PR_PANEL_ID} .pawchestrator-panel-toggle {
      align-items: center;
      background: transparent;
      border: 0;
      color: var(--fgColor-muted, #59636e);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      height: 24px;
      justify-content: center;
      padding: 0;
      width: 24px;
    }

    #${PANEL_ID} .pawchestrator-panel-summary,
    #${PR_PANEL_ID} .pawchestrator-panel-summary {
      align-items: center;
      display: flex;
      flex: 1;
      gap: 6px;
      min-width: 0;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-panel-brand-name,
    #${PR_PANEL_ID} .pawchestrator-panel-brand-name {
      flex: 0 0 auto;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-panel-status-text,
    #${PR_PANEL_ID} .pawchestrator-panel-status-text {
      min-width: 0;
    }

    #${PANEL_ID} .pawchestrator-panel-body,
    #${PR_PANEL_ID} .pawchestrator-panel-body {
      border-top: 1px solid var(--borderColor-default, #d0d7de);
      display: none;
      padding: 10px 12px 12px;
    }

    #${PANEL_ID}[data-expanded="true"] .pawchestrator-panel-body,
    #${PR_PANEL_ID}[data-expanded="true"] .pawchestrator-panel-body {
      display: block;
    }

    #${PANEL_ID} .pawchestrator-readiness-row,
    #${PR_PANEL_ID} .pawchestrator-readiness-row {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
    }

    #${PANEL_ID} .pawchestrator-readiness-item,
    #${PR_PANEL_ID} .pawchestrator-readiness-item {
      color: var(--fgColor-muted, #59636e);
      white-space: nowrap;
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="true"],
    #${PR_PANEL_ID} .pawchestrator-readiness-item[data-ready="true"] {
      color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID} .pawchestrator-readiness-item[data-ready="false"],
    #${PR_PANEL_ID} .pawchestrator-readiness-item[data-ready="false"] {
      color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-run-line,
    #${PR_PANEL_ID} .pawchestrator-run-line {
      color: var(--fgColor-muted, #59636e);
      margin-top: 8px;
    }

    #${PANEL_ID} .pawchestrator-pipeline,
    #${PR_PANEL_ID} .pawchestrator-pipeline {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-section {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-section {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-epic-section {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-pipeline-title,
    #${PR_PANEL_ID} .pawchestrator-pipeline-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} #${PLAN_APPROVAL_ID} {
      border-top: 1px solid var(--borderColor-muted, #d8dee4);
      display: grid;
      gap: 10px;
      margin-top: 10px;
      padding-top: 10px;
    }

    #${PANEL_ID} #${PLAN_APPROVAL_ID}.re-planning {
      align-items: center;
      color: var(--fgColor-muted, #59636e);
      display: flex;
      gap: 8px;
    }

    #${PANEL_ID} #${PLAN_APPROVAL_ID} .spinner {
      animation: pawchestrator-spin 0.8s linear infinite;
      border: 2px solid var(--borderColor-muted, #d8dee4);
      border-top-color: var(--fgColor-accent, #0969da);
      border-radius: 50%;
      display: inline-block;
      height: 14px;
      width: 14px;
    }

    @keyframes pawchestrator-spin {
      to {
        transform: rotate(360deg);
      }
    }

    #${PANEL_ID} .pawchestrator-plan-approval-header {
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
    }

    #${PANEL_ID} .pawchestrator-plan-approval-title,
    #${PANEL_ID} .pawchestrator-plan-approval-section-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin: 0;
    }

    #${PANEL_ID} .pawchestrator-plan-approval-summary {
      color: var(--fgColor-default, #24292f);
      white-space: pre-wrap;
    }

    #${PANEL_ID} .pawchestrator-plan-approval-attempt {
      color: var(--fgColor-muted, #59636e);
      font-size: 12px;
      font-weight: 600;
    }

    #${PANEL_ID} .risk-badge {
      border: 1px solid transparent;
      border-radius: 999px;
      display: inline-flex;
      font-size: 12px;
      font-weight: 600;
      line-height: 18px;
      padding: 0 8px;
      white-space: nowrap;
    }

    #${PANEL_ID} .risk-low {
      background: var(--bgColor-success-muted, #dafbe1);
      border-color: var(--borderColor-success-muted, #4ac26b);
      color: var(--fgColor-success, #1a7f37);
    }

    #${PANEL_ID} .risk-medium {
      background: var(--bgColor-attention-muted, #fff8c5);
      border-color: var(--borderColor-attention-muted, #d4a72c);
      color: var(--fgColor-attention, #9a6700);
    }

    #${PANEL_ID} .risk-high {
      background: var(--bgColor-danger-muted, #ffebe9);
      border-color: var(--borderColor-danger-muted, #ff8182);
      color: var(--fgColor-danger, #cf222e);
    }

    #${PANEL_ID} .pawchestrator-plan-approval-list {
      margin: 4px 0 0;
      padding-left: 18px;
    }

    #${PANEL_ID} .pawchestrator-plan-step-files,
    #${PANEL_ID} .pawchestrator-plan-step-notes {
      color: var(--fgColor-muted, #59636e);
      margin-top: 2px;
    }

    #${PANEL_ID} .pawchestrator-plan-approval-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }

    #${PANEL_ID} .pawchestrator-plan-feedback {
      display: grid;
      gap: 8px;
    }

    #${PANEL_ID} .pawchestrator-plan-feedback textarea {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font: inherit;
      min-height: 84px;
      padding: 8px;
      resize: vertical;
      width: 100%;
    }

    #${PANEL_ID} .pawchestrator-plan-approval-error {
      color: var(--fgColor-danger, #cf222e);
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-grill-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 6px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-analysis {
      margin-top: 8px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-created {
      margin: 6px 0 0;
      padding-left: 18px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-error {
      color: var(--fgColor-danger, #cf222e);
      margin-top: 8px;
    }

    #${PANEL_ID} .pawchestrator-epic-architect-partial {
      color: var(--fgColor-muted, #59636e);
      margin-top: 4px;
    }

    #${PANEL_ID} .pawchestrator-epic-title {
      color: var(--fgColor-muted, #59636e);
      font-weight: 600;
      margin-bottom: 8px;
    }

    #${PANEL_ID} .pawchestrator-epic-runs {
      display: grid;
      gap: 10px;
    }

    #${PANEL_ID} .pawchestrator-epic-run {
      display: grid;
      gap: 6px;
    }

    #${PANEL_ID} .pawchestrator-epic-verification {
      display: grid;
      gap: 6px;
    }

    #${PANEL_ID} .pawchestrator-epic-run-title {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-epic-verification-title {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-grill-details {
      color: var(--fgColor-muted, #59636e);
      display: grid;
      gap: 4px;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-active="true"]::before {
      animation: pawchestrator-spin 0.8s linear infinite;
      border: 1px solid var(--fgColor-accent, #0969da);
      border-radius: 50%;
      border-right-color: transparent;
      content: "";
      display: inline-block;
      height: 10px;
      margin-right: 6px;
      vertical-align: -1px;
      width: 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-status="grill_waiting"] {
      background: var(--bgColor-attention-muted, #fff8c5);
      border: 1px solid var(--borderColor-attention-muted, #d4a72c);
      border-radius: 6px;
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
      grid-column: 1 / -1;
      padding: 8px 10px;
    }

    #${PANEL_ID} .pawchestrator-grill-status[data-status="grill_waiting"]::before {
      content: "\\26A0";
      display: inline-block;
      margin-right: 6px;
    }

    #${PANEL_ID} .pawchestrator-grill-error {
      color: var(--fgColor-danger, #cf222e);
      grid-column: 1 / -1;
    }

    #${PANEL_ID} .pawchestrator-timeline,
    #${PR_PANEL_ID} .pawchestrator-timeline {
      align-items: flex-start;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(6, minmax(72px, 1fr));
      overflow-x: auto;
      padding-bottom: 2px;
    }

    #${PANEL_ID} .pawchestrator-step,
    #${PR_PANEL_ID} .pawchestrator-step {
      color: var(--fgColor-muted, #59636e);
      min-width: 72px;
      position: relative;
    }

    #${PANEL_ID} .pawchestrator-step:not(:last-child)::after,
    #${PR_PANEL_ID} .pawchestrator-step:not(:last-child)::after {
      background: var(--borderColor-muted, #d8dee4);
      content: "";
      height: 1px;
      left: 23px;
      position: absolute;
      right: -9px;
      top: 8px;
    }

    #${PANEL_ID} .pawchestrator-step[data-active="true"],
    #${PR_PANEL_ID} .pawchestrator-step[data-active="true"] {
      color: var(--fgColor-default, #24292f);
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-step-label,
    #${PR_PANEL_ID} .pawchestrator-step-label {
      display: block;
      font-size: 12px;
      line-height: 16px;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }

    #${PANEL_ID} .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step-indicator {
      align-items: center;
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-muted, #d8dee4);
      border-radius: 50%;
      display: inline-flex;
      font-size: 11px;
      height: 18px;
      justify-content: center;
      position: relative;
      width: 18px;
      z-index: 1;
    }

    #${PANEL_ID} .pawchestrator-step[data-status="pending"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="pending"] .pawchestrator-step-indicator {
      color: var(--fgColor-muted, #59636e);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="running"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="running"] .pawchestrator-step-indicator {
      animation: pawchestrator-spin 0.8s linear infinite;
      border-color: var(--fgColor-accent, #0969da);
      border-right-color: transparent;
      color: transparent;
    }

    #${PANEL_ID} .pawchestrator-step[data-status="done"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="done"] .pawchestrator-step-indicator {
      background: var(--bgColor-success-emphasis, #1a7f37);
      border-color: var(--bgColor-success-emphasis, #1a7f37);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-step[data-status="failed"] .pawchestrator-step-indicator,
    #${PR_PANEL_ID} .pawchestrator-step[data-status="failed"] .pawchestrator-step-indicator {
      background: var(--bgColor-danger-emphasis, #cf222e);
      border-color: var(--bgColor-danger-emphasis, #cf222e);
      color: var(--fgColor-onEmphasis, #ffffff);
    }

    #${PANEL_ID} .pawchestrator-warnings,
    #${PR_PANEL_ID} .pawchestrator-warnings {
      margin-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-warnings summary,
    #${PR_PANEL_ID} .pawchestrator-warnings summary {
      color: var(--fgColor-attention, #9a6700);
      cursor: pointer;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-warnings-list,
    #${PR_PANEL_ID} .pawchestrator-warnings-list {
      color: var(--fgColor-muted, #59636e);
      margin: 6px 0 0;
      padding-left: 18px;
    }

    @keyframes pawchestrator-spin {
      to {
        transform: rotate(360deg);
      }
    }

    #${PANEL_ID} a,
    #${PR_PANEL_ID} a {
      color: var(--fgColor-accent, #0969da);
    }

    #${CONFIRM_OVERLAY_ID} {
      align-items: flex-start;
      background: rgba(31, 35, 40, 0.45);
      bottom: 0;
      display: flex;
      justify-content: center;
      left: 0;
      padding: 12vh 16px 16px;
      position: fixed;
      right: 0;
      top: 0;
      z-index: 99999;
    }

    .pawchestrator-confirm-dialog {
      background: var(--bgColor-default, #ffffff);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      box-shadow: var(--shadow-floating-large, 0 8px 24px rgba(140, 149, 159, 0.2));
      color: var(--fgColor-default, #24292f);
      font-size: 14px;
      line-height: 20px;
      max-width: 440px;
      overflow: hidden;
      width: min(440px, 100%);
    }

    .pawchestrator-confirm-header {
      align-items: center;
      background: var(--bgColor-muted, #f6f8fa);
      border-bottom: 1px solid var(--borderColor-default, #d0d7de);
      display: flex;
      font-weight: 600;
      min-height: 40px;
      padding: 8px 16px;
    }

    .pawchestrator-confirm-body {
      padding: 16px;
    }

    .pawchestrator-confirm-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      padding: 0 16px 16px;
    }

    .pawchestrator-confirm-actions .pawchestrator-confirm-danger {
      background: var(--button-danger-bgColor-rest, #cf222e);
      border-color: var(--button-danger-borderColor-rest, rgba(31, 35, 40, 0.15));
      color: var(--button-danger-fgColor-rest, #ffffff);
    }
  `);
}

injectStyles();
