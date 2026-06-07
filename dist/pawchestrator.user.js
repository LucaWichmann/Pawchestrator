// ==UserScript==
// @name         Pawchestrator
// @namespace    https://github.com/LucaWichmann/Pawchestrator
// @version      0.1.0
// @description  Agent orchestration controls for GitHub issues
// @downloadURL  https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/dist/pawchestrator.user.js
// @updateURL    https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/dist/pawchestrator.user.js
// @match        https://github.com/*
// @connect      127.0.0.1
// @grant        GM_addStyle
// @grant        GM_deleteValue
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @run-at       document-idle
// ==/UserScript==

(function() {
	"use strict";
	var API_BASE = "http://127.0.0.1:38472";
	var PANEL_ID = "pawchestrator-panel";
	var PR_PANEL_ID = "pawchestrator-pr-panel";
	var START_ID = "pawchestrator-start";
	var GRILL_ID = "pawchestrator-grill";
	var EPIC_ARCHITECT_ID = "pawchestrator-epic-architect";
	var PR_REVIEW_ID = "pawchestrator-review";
	var PR_REPAIR_ID = "pawchestrator-repair";
	var CREATE_ISSUES_ID = "pawchestrator-create-issues";
	var PLAN_APPROVAL_ID = "pawchestrator-plan-approval";
	var CONFIRM_OVERLAY_ID = "pawchestrator-confirm-overlay";
	var POLL_INTERVAL_MS = 3e3;
	var TOKEN_KEY = "pawchestrator_token";
	var PIPELINE_STAGES = [
		"snapshot",
		"scout",
		"plan",
		"implement",
		"verify",
		"pr"
	];
	var REVIEW_STAGES = [
		"review",
		"post",
		"issues"
	];
	var REPAIR_STAGES = ["repair", "push"];
	var EPIC_ARCHITECT_STAGES = [
		"epic_scout",
		"epic_architect",
		"creating"
	];
	var PAW = "🐾";
	var FIRE = "🔥";
	var CONSTRUCTION = "🏗️";
	var OFFLINE_MESSAGE = "Pawchestrator not running — start with `pawchestrator serve`";
	var GRILL_LABEL = `${FIRE} Grill Issue`;
	var REGRILL_LABEL = `${FIRE} Re-grill`;
	var RUN_DONE = new Set([
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
		"epic_architect_failed"
	]);
	var PIPELINE_ACTIVE = new Set([
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
		"completed"
	]);
	var STAGE_DONE = new Set([
		"complete",
		"completed",
		"skipped"
	]);
	var GRILL_REPLY_TOOLTIP = "Replying to Pawchestrator questions — submitting will continue the grilling session.";
	function injectStyles() {
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

    #${PANEL_ID} .pawchestrator-run-log {
      margin-top: 10px;
    }

    #${PANEL_ID} .pawchestrator-run-log summary {
      color: var(--fgColor-muted, #59636e);
      cursor: pointer;
      font-weight: 600;
    }

    #${PANEL_ID} .pawchestrator-run-log-lines {
      background: var(--bgColor-muted, #f6f8fa);
      border: 1px solid var(--borderColor-default, #d0d7de);
      border-radius: 6px;
      color: var(--fgColor-muted, #59636e);
      font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 12px;
      line-height: 1.45;
      margin: 6px 0 0;
      max-height: 220px;
      overflow: auto;
      padding: 8px;
      white-space: pre-wrap;
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
	var noopStatusSetter = () => {};
	function rawRequestJson(path, options = {}) {
		return new Promise((resolve, reject) => {
			GM_xmlhttpRequest({
				method: options.method || "GET",
				url: `${API_BASE}${path}`,
				headers: options.headers || {},
				data: options.body,
				timeout: 5e3,
				onload: (response) => {
					if (response.status < 200 || response.status >= 300) {
						const error = new Error(`${options.label || "Request"} failed (${response.status})`);
						error.status = response.status;
						reject(error);
						return;
					}
					if (!response.responseText) {
						resolve(null);
						return;
					}
					try {
						resolve(JSON.parse(response.responseText));
					} catch (error) {
						reject(new Error(`${options.label || "Request"} returned invalid JSON: ${error.message}`));
					}
				},
				onerror: () => reject(new Error(OFFLINE_MESSAGE)),
				ontimeout: () => reject(new Error(OFFLINE_MESSAGE))
			});
		});
	}
	async function getOrAcquireToken(statusSetter = noopStatusSetter) {
		const storedToken = await GM_getValue(TOKEN_KEY);
		if (storedToken) return storedToken;
		statusSetter("Pairing - approve in terminal...");
		const response = await rawRequestJson("/pair", {
			method: "POST",
			label: "Pairing request"
		});
		await GM_setValue(TOKEN_KEY, response.token);
		return response.token;
	}
	async function requestJson(path, options = {}) {
		if (path === "/health" || path === "/pair") return rawRequestJson(path, options);
		const statusSetter = options.statusSetter || noopStatusSetter;
		const token = await getOrAcquireToken(statusSetter);
		const headers = {
			...options.headers || {},
			"X-Pawchestrator-Token": token
		};
		try {
			return await rawRequestJson(path, {
				...options,
				headers
			});
		} catch (error) {
			if (error.status !== 403) throw error;
			await GM_deleteValue(TOKEN_KEY);
			const freshToken = await getOrAcquireToken(statusSetter);
			return rawRequestJson(path, {
				...options,
				headers: {
					...options.headers || {},
					"X-Pawchestrator-Token": freshToken
				}
			});
		}
	}
	function openRunStream(runId) {
		const token = GM_getValue(TOKEN_KEY);
		const url = new URL(`${API_BASE}/runs/${encodeURIComponent(runId)}/stream`);
		url.searchParams.set("token", token);
		return new EventSource(url.toString());
	}
	async function fetchIssueStatus(issue) {
		return requestJson(`/issue/${issue.owner}/${issue.repo}/${issue.number}/status`, { label: "Issue status request" });
	}
	async function fetchPrRun(runId) {
		return requestJson(`/runs/${runId}/status`, { label: "PR review status request" });
	}
	async function fetchPrStatus(pr) {
		return requestJson(`/pr/${pr.owner}/${pr.repo}/${pr.pr_number}/status`, { label: "PR status request" });
	}
	async function fetchPrReviewState(pr) {
		return requestJson(`/prs/${pr.owner}/${pr.repo}/${pr.pr_number}/review-state`, { label: "PR review state request" });
	}
	var state = {
		activePoll: null,
		activePrPoll: null,
		activeRunId: null,
		activeRunStream: null,
		activePathname: window.location.pathname,
		panelExpandedByUser: null,
		lastPipelineExpansionKey: null,
		reinjectTimer: null,
		grillReplyObserverState: null,
		latestIssueStatus: null,
		latestPrRun: null,
		latestPrReviewState: null,
		runLogLines: [],
		sseConnected: false,
		planAttempt: 1,
		planAttemptRunId: null,
		rejectedPlanRunIds: new Set()
	};
	function activePanel() {
		return document.getElementById("pawchestrator-panel") || document.getElementById("pawchestrator-pr-panel");
	}
	function setPanelSummary(message) {
		const panel = activePanel();
		const status = panel && panel.querySelector(".pawchestrator-panel-status-text");
		if (status) status.textContent = message;
	}
	function setPanelStatus(nextState) {
		const panel = activePanel();
		if (panel) panel.dataset.status = nextState;
	}
	function setPanelExpanded(expanded) {
		const panel = activePanel();
		if (!panel) return;
		panel.dataset.expanded = String(expanded);
		const toggle = panel.querySelector(".pawchestrator-panel-toggle");
		if (toggle) {
			toggle.textContent = expanded ? "▾" : "▸";
			toggle.setAttribute("aria-expanded", String(expanded));
		}
	}
	function shouldAutoExpand(status) {
		return Boolean(status && (status.pipeline || status.grill || status.epic_architect || (Array.isArray(status.epic?.sub_runs) ? status.epic.sub_runs : []).some((run) => run.status === "running" || /_running$/.test(run.status || ""))));
	}
	function isPipelineVisible(pipeline) {
		return Boolean(pipeline && (PIPELINE_ACTIVE.has(pipeline.status) || pipeline.status === "completed" || pipeline.status === "failed"));
	}
	function maybeAutoExpandForPipeline(status) {
		const pipeline = status && status.pipeline;
		if (!pipeline) {
			state.lastPipelineExpansionKey = null;
			return;
		}
		const key = `${pipeline.run_id || ""}:${pipeline.status || ""}:${pipeline.current_stage || ""}`;
		const shouldExpand = isPipelineVisible(pipeline) && key !== state.lastPipelineExpansionKey;
		state.lastPipelineExpansionKey = key;
		if (shouldExpand) setPanelExpanded(true);
	}
	function findIssueBodyContainer() {
		return [".IssueBody-module__outerContainer__ULNTb", "[class*=\"IssueBody-module__outerContainer\"]"].map((selector) => document.querySelector(selector)).find(Boolean) || null;
	}
	function findPrConversationContainer() {
		return [
			"#discussion_bucket",
			"#partial-discussion-header",
			"[data-testid=\"issue-viewer-issue-container\"]",
			".js-discussion"
		].map((selector) => document.querySelector(selector)).find(Boolean) || null;
	}
	function setButtonText(button, text) {
		const label = button.querySelector("[data-component='text'], .Button-label, span");
		if (label) label.textContent = text;
		else button.textContent = text;
	}
	function createButton(id, testid, labelText, onClick) {
		const button = document.createElement("button");
		if (id) button.id = id;
		button.type = "button";
		button.dataset.component = "Button";
		button.dataset.testid = testid;
		button.dataset.loading = "false";
		button.dataset.noVisuals = "true";
		button.dataset.size = "medium";
		button.dataset.variant = "default";
		button.dataset.idleLabel = labelText;
		button.className = "prc-Button-ButtonBase-9n-Xk";
		button.addEventListener("click", onClick);
		const content = document.createElement("span");
		content.dataset.component = "buttonContent";
		content.dataset.align = "center";
		content.className = "prc-Button-ButtonContent-Iohp5";
		const label = document.createElement("span");
		label.dataset.component = "text";
		label.className = "prc-Button-Label-FWkx3";
		label.textContent = labelText;
		content.append(label);
		button.append(content);
		return button;
	}
	function parseIssueReference() {
		const [, owner, repo, type, number] = window.location.pathname.split("/");
		if (!owner || !repo || type !== "issues" || !number) throw new Error("Not a GitHub issue page");
		const issueNumber = Number.parseInt(number, 10);
		if (!Number.isInteger(issueNumber) || issueNumber <= 0) throw new Error("Invalid GitHub issue number");
		return {
			owner,
			repo,
			number: issueNumber
		};
	}
	function isIssuePage() {
		const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
		const issueNumber = Number.parseInt(number, 10);
		return Boolean(owner) && Boolean(repo) && type === "issues" && String(issueNumber) === number && issueNumber > 0 && !extra;
	}
	function parsePrReference() {
		const [, owner, repo, type, number, extra] = window.location.pathname.split("/");
		if (!owner || !repo || type !== "pull" || extra) throw new Error("Not a GitHub pull request page");
		const prNumber = Number.parseInt(number, 10);
		if (!Number.isInteger(prNumber) || String(prNumber) !== number || prNumber <= 0) throw new Error("Invalid GitHub pull request number");
		return {
			owner,
			repo,
			pr_number: prNumber
		};
	}
	function isPrPage() {
		try {
			parsePrReference();
			return true;
		} catch {
			return false;
		}
	}
	function epicArchitectRunKey() {
		return `pawchestrator_epic_architect_run:${window.location.pathname}`;
	}
	async function startEpicArchitect$1({ renderStatus, startIssueStatusPolling }) {
		const button = document.getElementById(EPIC_ARCHITECT_ID);
		if (button) button.disabled = true;
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
				body: JSON.stringify(issue)
			});
			await GM_setValue(epicArchitectRunKey(), response.run_id);
			renderStatus({
				...state.latestIssueStatus || {},
				epic_architect: {
					run_id: response.run_id,
					workflow_type: "epic_architect",
					status: "epic_scout_running",
					current_stage: "epic_scout",
					stages: EPIC_ARCHITECT_STAGES.map((stage_name) => ({
						stage_name,
						status: stage_name === "epic_scout" ? "running" : "pending"
					})),
					created_sub_issues: []
				}
			});
			startIssueStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	function createDialogButton(labelText, variant, onClick) {
		const button = createButton("", "", labelText, onClick);
		button.removeAttribute("id");
		delete button.dataset.testid;
		if (variant === "danger") button.classList.add("pawchestrator-confirm-danger");
		return button;
	}
	function showConfirmDialog(message, options = {}) {
		document.getElementById(CONFIRM_OVERLAY_ID)?.remove();
		return new Promise((resolve) => {
			const overlay = document.createElement("div");
			overlay.id = CONFIRM_OVERLAY_ID;
			overlay.setAttribute("role", "presentation");
			const dialog = document.createElement("div");
			dialog.className = "pawchestrator-confirm-dialog";
			dialog.setAttribute("role", "dialog");
			dialog.setAttribute("aria-modal", "true");
			dialog.setAttribute("aria-labelledby", "pawchestrator-confirm-title");
			dialog.setAttribute("aria-describedby", "pawchestrator-confirm-message");
			const header = document.createElement("div");
			header.id = "pawchestrator-confirm-title";
			header.className = "pawchestrator-confirm-header";
			header.textContent = options.title || "Confirm action";
			const body = document.createElement("div");
			body.id = "pawchestrator-confirm-message";
			body.className = "pawchestrator-confirm-body";
			body.textContent = message;
			const actions = document.createElement("div");
			actions.className = "pawchestrator-confirm-actions";
			let settled = false;
			const close = (confirmed) => {
				if (settled) return;
				settled = true;
				document.removeEventListener("keydown", onKeydown);
				overlay.remove();
				resolve(confirmed);
			};
			const onKeydown = (event) => {
				if (event.key === "Escape") close(false);
			};
			const noButton = createDialogButton(options.cancelLabel || "No", "default", () => close(false));
			const yesButton = createDialogButton(options.confirmLabel || "Yes", "danger", () => close(true));
			actions.append(noButton, yesButton);
			dialog.append(header, body, actions);
			overlay.append(dialog);
			overlay.addEventListener("click", (event) => {
				if (event.target === overlay) close(false);
			});
			document.addEventListener("keydown", onKeydown);
			document.documentElement.append(overlay);
			noButton.focus();
		});
	}
	async function startGrill$1({ startIssueStatusPolling, updateGrillButton }) {
		const button = document.getElementById(GRILL_ID);
		try {
			const issue = parseIssueReference();
			await getOrAcquireToken(setPanelSummary);
			const status = state.latestIssueStatus || await fetchIssueStatus(issue);
			state.latestIssueStatus = status;
			updateGrillButton(status.grill);
			if (status.grill?.status === "grill_waiting") {
				if (!await showConfirmDialog("Grill is still waiting for answers on this issue. Are you sure you want to re-grill?", {
					title: "Re-grill issue?",
					confirmLabel: "Yes",
					cancelLabel: "No"
				})) return;
			}
			if (button) button.disabled = true;
			setPanelSummary("[grill] starting...");
			state.panelExpandedByUser = true;
			setPanelExpanded(true);
			await requestJson("/issue/grill", {
				method: "POST",
				label: "Grill request",
				statusSetter: setPanelSummary,
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(issue)
			});
			startIssueStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	async function startRun$1({ renderStatus, startIssueStatusPolling }) {
		const button = document.getElementById(START_ID);
		if (button) button.disabled = true;
		try {
			const issue = parseIssueReference();
			await getOrAcquireToken(setPanelSummary);
			const status = await fetchIssueStatus(issue);
			if (status.epic_confirm && !confirmEpicStart(status.epic)) {
				if (button) button.disabled = false;
				return;
			}
			if (status.grill?.status === "grill_waiting") {
				if (!await showConfirmDialog("Grill is still waiting for answers on this issue. Are you sure you want to start agentic work?", {
					title: "Start agentic work?",
					confirmLabel: "Yes",
					cancelLabel: "No"
				})) {
					if (button) button.disabled = false;
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
				body: JSON.stringify(issue)
			});
			if (response?.type === "epic") renderStatus({
				...status,
				pipeline: null,
				epic: epicFromStartResponse(response)
			});
			startIssueStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	function confirmEpicStart(epic) {
		const lines = (epic?.sub_runs || []).map((run) => {
			const title = run.title ? ` ${run.title}` : "";
			return `#${run.issue_number}${title}`;
		});
		const list = lines.length > 0 ? `\n\n${lines.join("\n")}` : "";
		return window.confirm(`Work on this epic issue and its sub-issues?${list}`);
	}
	function epicFromStartResponse(response) {
		return {
			run_id: response.run_id,
			group_id: response.group_id,
			status: "epic_running",
			mode: response.mode,
			branch: response.branch,
			pr_url: response.pr_url,
			sub_runs: (response.sub_runs || []).map((run) => ({
				issue_number: run.issue_number,
				run_id: run.run_id,
				title: run.title,
				status: "pending",
				current_stage: null,
				workflow_type: "pipeline",
				stages: PIPELINE_STAGES.map((stage_name) => ({
					stage_name,
					status: "pending"
				})),
				warnings: []
			}))
		};
	}
	function prRunKey$1() {
		return `pawchestrator_pr_run:${window.location.pathname}`;
	}
	async function startReview$1({ renderPrStatus, startPrStatusPolling }) {
		const button = document.getElementById(PR_REVIEW_ID);
		if (button) button.disabled = true;
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
				body: JSON.stringify(pr)
			});
			await GM_setValue(prRunKey$1(), response.run_id);
			renderPrStatus({
				run_id: response.run_id,
				workflow_type: "review",
				status: "review_running",
				current_stage: "review",
				stages: REVIEW_STAGES.map((stage_name) => ({
					stage_name,
					status: stage_name === "review" ? "running" : "pending"
				}))
			});
			startPrStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	async function startRepair$1({ renderPrStatus, startPrStatusPolling }) {
		const button = document.getElementById(PR_REPAIR_ID);
		if (button) button.disabled = true;
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
				body: JSON.stringify(pr)
			});
			await GM_setValue(prRunKey$1(), response.run_id);
			renderPrStatus({
				run_id: response.run_id,
				workflow_type: "repair",
				status: "repair_running",
				current_stage: "repair",
				stages: REPAIR_STAGES.map((stage_name) => ({
					stage_name,
					status: stage_name === "repair" ? "running" : "pending"
				}))
			});
			startPrStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	async function startCreateIssues({ renderPrStatus, startPrStatusPolling }) {
		const runId = state.latestPrRun?.id || state.latestPrRun?.run_id || await GM_getValue(prRunKey$1());
		const button = document.getElementById(CREATE_ISSUES_ID);
		if (button) button.disabled = true;
		try {
			if (!runId) throw new Error("No review run found for this PR");
			setPanelSummary("[issues] creating...");
			renderPrStatus(await requestJson(`/runs/${runId}/create-issues`, {
				method: "POST",
				label: "Create issues request",
				statusSetter: setPanelSummary
			}));
			startPrStatusPolling();
		} catch (error) {
			setPanelSummary(error.message);
			if (button) button.disabled = false;
		}
	}
	function epicSubRuns$1(epic) {
		return Array.isArray(epic?.sub_runs) ? epic.sub_runs : [];
	}
	function epicStatus$1(epic) {
		if (epic?.status === "epic_complete") return "completed";
		if (epic?.status === "epic_failed") return "failed";
		const runs = epicSubRuns$1(epic);
		if (runs.some((run) => run.status === "failed" || /_failed$/.test(run.status || ""))) return "failed";
		if (runs.length > 0 && runs.every((run) => run.status === "completed")) return "completed";
		return "running";
	}
	function epicSummaryRun(epic) {
		if (!epic) return null;
		return {
			workflow_type: "epic",
			status: epic.status || epicStatus$1(epic),
			current_stage: "epic",
			pr_url: epic.pr_url
		};
	}
	function currentRun(status) {
		if (!status) return null;
		const runs = [
			epicSummaryRun(status.epic),
			status.pipeline,
			status.grill,
			status.epic_architect
		].filter(Boolean);
		return runs.find((run) => !isRunDone(run)) || runs[0] || null;
	}
	function isRunDone(run) {
		const status = typeof run === "string" ? run : run?.status;
		return Boolean(status && (RUN_DONE.has(status) || /_failed$/.test(status)));
	}
	function isEpicDone(epic) {
		return Boolean(epic && isRunDone(epic.status || epicStatus$1(epic)));
	}
	function summarizeError(run) {
		const failedStage = (run.stages || []).find((stage) => stage.status === "failed");
		if (!failedStage) return "Run failed";
		return `[${failedStage.stage_name || failedStage.name || run.current_stage || "unknown"}] failed${failedStage.error ? `: ${failedStage.error}` : ""}`;
	}
	function summarizeGrillCompletion(run) {
		const report = run.grill_report || run.report || run.artifact || {};
		const criteria = Array.isArray(report.suggested_criteria) ? report.suggested_criteria.length : null;
		const questions = Array.isArray(report.unanswerable_questions) ? report.unanswerable_questions.length : null;
		const bodyUpdated = report.body_updated === true;
		if (criteria !== null || questions !== null || bodyUpdated) {
			const parts = [];
			if (bodyUpdated) parts.push("Criteria appended");
			else if (criteria === 0) parts.push("No new criteria");
			else if (criteria !== null) parts.push(`${criteria} ${criteria === 1 ? "criterion" : "criteria"} suggested`);
			if (questions !== null) parts.push(questions === 0 ? "No questions - issue ready" : `${questions} ${questions === 1 ? "question" : "questions"} posted`);
			if (parts.length > 0) return parts.join(" · ");
		}
		return "Grill completed";
	}
	function summarizeRun(run) {
		if (!run) return "Ready";
		if (run.status === "completed") return run.pr_url ? "Draft PR ready" : "Run completed";
		if (run.status === "failed") return summarizeError(run);
		if (run.status === "grill_complete") return summarizeGrillCompletion(run);
		if (run.status === "grill_failed") return summarizeError(run);
		if (run.workflow_type === "epic_architect") {
			if (isRunDone(run)) return run.status === "completed" || run.status === "epic_architect_complete" ? "Epic created" : summarizeError(run);
			return `[${run.current_stage || "epic_scout"}] ${(run.status || "pending").replace(/^(epic_scout|epic_architect)_/, "")}...`;
		}
		if (run.workflow_type === "epic") return `Epic ${run.status || "running"}`;
		return `[${run.current_stage || (run.workflow_type === "grill" ? "grill" : "queued")}] ${(run.status || "pending").replace(/^grill_/, "")}...`;
	}
	function epicSubRuns(epic) {
		return Array.isArray(epic?.sub_runs) ? epic.sub_runs : [];
	}
	function epicStatus(epic) {
		if (epic?.status === "epic_complete") return "completed";
		if (epic?.status === "epic_failed") return "failed";
		const runs = epicSubRuns(epic);
		if (runs.some((run) => run.status === "failed" || /_failed$/.test(run.status || ""))) return "failed";
		if (runs.length > 0 && runs.every((run) => run.status === "completed")) return "completed";
		return "running";
	}
	function stageName$2(stage) {
		return String(stage?.stage_name || stage?.name || "");
	}
	function stageStatus$2(stage) {
		return String(stage?.status || "pending").replace(/^[^_]+_/, "");
	}
	function normalizeStepStatus$2(stage, isAfterActive) {
		if (isAfterActive || !stage) return "pending";
		const status = stageStatus$2(stage);
		if (status === "running" || status === "failed" || status === "skipped") return status;
		return [
			"complete",
			"completed",
			"done",
			"success",
			"skipped"
		].includes(status) ? "done" : "pending";
	}
	function collapseEpicStages(stages) {
		const names = [
			"snapshot",
			"scout",
			"plan",
			"implement",
			"verify",
			"pr"
		];
		const rows = Array.isArray(stages) ? stages : [];
		return names.map((name) => ({
			name,
			label: name,
			stage: rows.find((stage) => stageName$2(stage) === name) || {
				stage_name: name,
				status: "pending"
			}
		}));
	}
	function activeEpicStageIndex(run, steps) {
		const failedIndex = steps.findIndex((step) => stageStatus$2(step.stage) === "failed");
		if (failedIndex >= 0) return failedIndex;
		const current = String(run.current_stage || "");
		const currentIndex = steps.findIndex((step) => step.name === current);
		if (currentIndex >= 0) return currentIndex;
		return steps.findIndex((step) => stageStatus$2(step.stage) === "running");
	}
	function renderEpicTimeline(parent, run, options = {}) {
		const steps = collapseEpicStages(run.stages);
		const activeIndex = activeEpicStageIndex(run, steps);
		const timeline = document.createElement("div");
		timeline.className = "pawchestrator-timeline";
		steps.forEach((step, index) => {
			const status = normalizeStepStatus$2(step.stage, activeIndex >= 0 && index > activeIndex);
			const item = document.createElement("div");
			item.className = "pawchestrator-step";
			item.dataset.status = status;
			item.dataset.active = String(!options.suppressActive && index === activeIndex && run.status !== "completed");
			const indicator = document.createElement("span");
			indicator.className = "pawchestrator-step-indicator";
			indicator.textContent = status === "done" ? "✓" : status === "failed" ? "×" : "•";
			const label = document.createElement("span");
			label.className = "pawchestrator-step-label";
			label.textContent = step.label;
			item.append(indicator, label);
			timeline.append(item);
		});
		parent.append(timeline);
	}
	function epicParentStages(epic) {
		return Array.isArray(epic?.parent_stages) ? epic.parent_stages.filter((stage) => {
			const name = stage.stage_name || stage.name;
			return name === "verify" || name === "implement";
		}) : [];
	}
	function renderEpicSection(parent, epic) {
		if (!epic) return;
		const section = document.createElement("section");
		section.className = "pawchestrator-epic-section";
		const title = document.createElement("div");
		title.className = "pawchestrator-epic-title";
		title.textContent = `Epic: ${epicStatus(epic)}`;
		if (epic.pr_url) {
			title.append(document.createTextNode(" · "));
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
			renderEpicTimeline(verification, {
				stages: parentStages,
				current_stage: epic.current_stage,
				status: epic.status || epicStatus(epic)
			}, { suppressActive: epicDone || isRunDone(epic.status) });
			section.append(verification);
		}
		parent.append(section);
	}
	function grillReport(grill) {
		return grill.grill_report || grill.report || grill.artifact || {};
	}
	function countGrillValue(grill, report, countKey, listKey) {
		if (Number.isFinite(grill[countKey])) return grill[countKey];
		if (Number.isFinite(report[countKey])) return report[countKey];
		return Array.isArray(report[listKey]) ? report[listKey].length : 0;
	}
	function grillBodyUpdated(grill, report) {
		return grill.body_updated === true || report.body_updated === true;
	}
	function grillTimestamp(grill) {
		return grill.updated_at || grill.completed_at || grill.started_at || "";
	}
	function formatGrillTimestamp(value) {
		if (!value) return "unknown";
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return String(value);
		return date.toLocaleString();
	}
	function isGrillActive(grill) {
		return Boolean(grill && !isRunDone(grill));
	}
	function renderGrillDetail(parent, label, value) {
		const item = document.createElement("div");
		item.textContent = `${label}: ${value}`;
		parent.append(item);
	}
	function renderGrillSection(parent, grill) {
		if (!grill) return;
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
		if (grill.status === "grill_waiting") {
			status.dataset.active = "false";
			status.textContent = "Waiting for your reply. Reply to the questions comment on GitHub to continue.";
		} else status.textContent = active ? "[grill] running..." : `Status: ${grill.status || "unknown"}`;
		details.append(status);
		const report = grillReport(grill);
		renderGrillDetail(details, "Criteria suggested", countGrillValue(grill, report, "criteria_count", "suggested_criteria"));
		renderGrillDetail(details, "Questions posted", countGrillValue(grill, report, "questions_posted_count", "unanswerable_questions"));
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
	function grillButtonLabel(grill) {
		return grill?.status === "grill_waiting" ? REGRILL_LABEL : GRILL_LABEL;
	}
	function updateGrillButton(grill) {
		const button = document.getElementById(GRILL_ID);
		if (button) setButtonText(button, grillButtonLabel(grill));
	}
	function stageName$1(stage) {
		return String(stage?.stage_name || stage?.name || "");
	}
	function stageStatus$1(stage) {
		return String(stage?.status || "pending").replace(/^[^_]+_/, "");
	}
	function normalizeStepStatus$1(stage, isAfterActive) {
		if (isAfterActive || !stage) return "pending";
		const status = stageStatus$1(stage);
		if (status === "running") return "running";
		if (status === "failed") return "failed";
		if (status === "skipped") return "skipped";
		if (STAGE_DONE.has(status)) return "done";
		return "pending";
	}
	function collapseStages(stages) {
		const rows = Array.isArray(stages) ? stages : [];
		const byName = new Map();
		PIPELINE_STAGES.forEach((name) => byName.set(name, []));
		rows.forEach((stage) => {
			const name = stageName$1(stage);
			if (byName.has(name)) byName.get(name).push(stage);
		});
		const repairCount = Math.max(0, (byName.get("implement") || []).length - 1);
		const repairTotal = state.config.pipeline.verify_repair_attempts;
		return PIPELINE_STAGES.map((name) => {
			const matching = byName.get(name) || [];
			const stage = matching[matching.length - 1] || {
				stage_name: name,
				status: "pending"
			};
			return {
				name,
				label: name === "implement" && repairCount > 0 ? `${name} (repair ${repairCount}/${repairTotal})` : name,
				stage
			};
		});
	}
	function collapseNamedStages$1(stages, names) {
		const rows = Array.isArray(stages) ? stages : [];
		return names.map((name) => ({
			name,
			label: name,
			stage: rows.find((stage) => stageName$1(stage) === name) || {
				stage_name: name,
				status: "pending"
			}
		}));
	}
	function activeStageIndex(pipeline, steps) {
		const failedIndex = steps.findIndex((step) => stageStatus$1(step.stage) === "failed");
		if (failedIndex >= 0) return failedIndex;
		const current = String(pipeline.current_stage || "");
		const currentIndex = steps.findIndex((step) => step.name === current);
		if (currentIndex >= 0) return currentIndex;
		const runningIndex = steps.findIndex((step) => stageStatus$1(step.stage) === "running");
		if (runningIndex >= 0) return runningIndex;
		if (pipeline.status === "completed") return PIPELINE_STAGES.length - 1;
		return -1;
	}
	function activeNamedStageIndex$1(run, steps) {
		const failedIndex = steps.findIndex((step) => stageStatus$1(step.stage) === "failed");
		if (failedIndex >= 0) return failedIndex;
		const current = String(run.current_stage || "");
		const currentIndex = steps.findIndex((step) => step.name === current);
		if (currentIndex >= 0) return currentIndex;
		const runningIndex = steps.findIndex((step) => stageStatus$1(step.stage) === "running");
		if (runningIndex >= 0) return runningIndex;
		return -1;
	}
	function renderStep(step, status, active) {
		const item = document.createElement("div");
		item.className = "pawchestrator-step";
		item.dataset.status = status;
		item.dataset.active = String(active);
		const indicator = document.createElement("span");
		indicator.className = "pawchestrator-step-indicator";
		indicator.textContent = status === "done" ? "✓" : status === "failed" ? "×" : "•";
		const label = document.createElement("span");
		label.className = "pawchestrator-step-label";
		label.textContent = step.label;
		item.append(indicator, label);
		return item;
	}
	function renderNamedTimeline$1(parent, run, stageNames, options = {}) {
		const steps = collapseNamedStages$1(run.stages, stageNames);
		const activeIndex = activeNamedStageIndex$1(run, steps);
		const timeline = document.createElement("div");
		timeline.className = "pawchestrator-timeline";
		timeline.style.gridTemplateColumns = `repeat(${stageNames.length}, minmax(72px, 1fr))`;
		steps.forEach((step, index) => {
			const status = options.markComplete && index <= activeIndex ? "done" : normalizeStepStatus$1(step.stage, activeIndex >= 0 && index > activeIndex);
			timeline.append(renderStep(step, status, !options.suppressActive && index === activeIndex && !isRunDone(run)));
		});
		parent.append(timeline);
	}
	function renderTimeline(parent, pipeline, options = {}) {
		const steps = collapseStages(pipeline.stages);
		const activeIndex = activeStageIndex(pipeline, steps);
		const timeline = document.createElement("div");
		timeline.className = "pawchestrator-timeline";
		steps.forEach((step, index) => {
			const status = normalizeStepStatus$1(step.stage, activeIndex >= 0 && index > activeIndex);
			timeline.append(renderStep(step, status, !options.suppressActive && index === activeIndex && pipeline.status !== "completed"));
		});
		parent.append(timeline);
	}
	var issuePanelHandlers = {
		startRun: () => {},
		startGrill: () => {},
		startEpicArchitect: () => {},
		isIssueOpen: () => true
	};
	function panelStatusForRun$1(run) {
		if (!run) return "idle";
		if (run.status === "epic_complete") return "done";
		if (run.status === "epic_failed") return "failed";
		if (run.status === "completed" || run.status === "grill_complete" || run.status === "post_complete" || run.status === "issues_complete" || run.status === "issues_skipped" || run.status === "repair_complete" || run.status === "push_complete" || run.status === "epic_architect_complete") return "done";
		if (run.status === "failed" || run.status === "grill_failed" || run.status === "review_failed" || run.status === "post_failed" || run.status === "issues_failed" || run.status === "repair_failed" || run.status === "push_failed" || run.status === "epic_architect_failed") return "failed";
		return "running";
	}
	function renderReadinessItem(parent, label, ready) {
		const item = document.createElement("span");
		item.className = "pawchestrator-readiness-item";
		item.dataset.ready = String(Boolean(ready));
		item.textContent = `${ready ? "✓" : "×"} ${label}`;
		parent.append(item);
	}
	function renderPipeline(parent, pipeline) {
		if (!pipeline) return;
		const section = document.createElement("section");
		section.className = "pawchestrator-pipeline";
		const title = document.createElement("div");
		title.className = "pawchestrator-pipeline-title";
		title.textContent = "Pipeline";
		if (pipeline.status === "completed" && pipeline.pr_url) {
			title.append(document.createTextNode(" · "));
			const link = document.createElement("a");
			link.href = pipeline.pr_url;
			link.textContent = "PR";
			title.append(link);
		}
		section.append(title);
		renderTimeline(section, pipeline);
		const warnings = Array.isArray(pipeline.warnings) ? pipeline.warnings : [];
		if (warnings.length > 0) {
			const details = document.createElement("details");
			details.className = "pawchestrator-warnings";
			const summary = document.createElement("summary");
			summary.textContent = `⚠ Warnings`;
			details.append(summary);
			const list = document.createElement("ul");
			list.className = "pawchestrator-warnings-list";
			warnings.forEach((warning) => {
				const item = document.createElement("li");
				item.textContent = `${warning.stage_name ? `[${warning.stage_name}] ` : ""}${warning.code ? `${warning.code}: ` : ""}${warning.message || "Warning"}`;
				list.append(item);
			});
			details.append(list);
			section.append(details);
		}
		parent.append(section);
	}
	function renderLogSection(parent) {
		if (state.runLogLines.length === 0) return;
		const details = document.createElement("details");
		details.className = "pawchestrator-run-log";
		const summary = document.createElement("summary");
		summary.textContent = `Run log (${state.runLogLines.length})`;
		details.append(summary);
		const list = document.createElement("pre");
		list.className = "pawchestrator-run-log-lines";
		list.textContent = state.runLogLines.join("\n");
		details.append(list);
		parent.append(details);
	}
	function epicArchitectCreatedIssues(run) {
		return Array.isArray(run?.created_sub_issues) ? run.created_sub_issues : [];
	}
	function issueAlreadyHasSubIssues(status) {
		if (epicArchitectCreatedIssues(status?.epic_architect).length > 0) return true;
		return Boolean(document.querySelector("[data-testid=\"sub-issues-issue-container\"]"));
	}
	function epicArchitectTimelineRun(run) {
		const stages = Array.isArray(run?.stages) ? [...run.stages] : [];
		const created = epicArchitectCreatedIssues(run);
		if (created.length > 0 || run?.status === "completed" || run?.status === "epic_architect_complete") stages.push({
			stage_name: "creating",
			status: "complete"
		});
		return {
			...run,
			current_stage: created.length > 0 || run?.status === "completed" ? "creating" : run?.current_stage,
			stages
		};
	}
	function renderCreatedSubIssueLinks(parent, created) {
		const list = document.createElement("ul");
		list.className = "pawchestrator-epic-architect-created";
		created.forEach((issue) => {
			const item = document.createElement("li");
			const link = document.createElement("a");
			link.href = issue.url;
			link.textContent = `#${issue.number}${issue.title ? ` ${issue.title}` : ""}`;
			item.append(link);
			list.append(item);
		});
		parent.append(list);
	}
	function renderEpicArchitectSection(parent, run) {
		if (!run) return;
		const section = document.createElement("section");
		section.className = "pawchestrator-epic-architect-section";
		const title = document.createElement("div");
		title.className = "pawchestrator-epic-architect-title";
		title.textContent = "EpicArchitect";
		section.append(title);
		renderNamedTimeline$1(section, epicArchitectTimelineRun(run), EPIC_ARCHITECT_STAGES, { markComplete: run.status === "completed" || run.status === "epic_architect_complete" });
		const created = epicArchitectCreatedIssues(run);
		if (run.epic_analysis && (run.status === "completed" || run.status === "epic_architect_complete")) {
			const analysis = document.createElement("div");
			analysis.className = "pawchestrator-epic-architect-analysis";
			analysis.textContent = run.epic_analysis;
			section.append(analysis);
		}
		if (created.length > 0 && (run.status === "completed" || run.status === "epic_architect_complete")) renderCreatedSubIssueLinks(section, created);
		if (isRunDone(run) && !(run.status === "completed" || run.status === "epic_architect_complete")) {
			const error = document.createElement("div");
			error.className = "pawchestrator-epic-architect-error";
			error.textContent = summarizeError(run);
			section.append(error);
			if (created.length > 0) {
				const partial = document.createElement("div");
				partial.className = "pawchestrator-epic-architect-partial";
				partial.textContent = `Created before failure: ${created.map((issue) => `#${issue.number}`).join(", ")}`;
				section.append(partial);
			}
		}
		parent.append(section);
	}
	function createGrillButton(grill) {
		return createButton(GRILL_ID, "pawchestrator-grill-button", grillButtonLabel(grill), issuePanelHandlers.startGrill);
	}
	function createEpicArchitectButton() {
		return createButton(EPIC_ARCHITECT_ID, "pawchestrator-epic-architect-button", `${CONSTRUCTION} Turn into Epic`, issuePanelHandlers.startEpicArchitect);
	}
	function updateEpicArchitectButton(status = state.latestIssueStatus) {
		const bar = document.getElementById(PANEL_ID)?.querySelector(".pawchestrator-panel-bar");
		let button = document.getElementById(EPIC_ARCHITECT_ID);
		if (!bar) return;
		if (issueAlreadyHasSubIssues(status)) {
			button?.remove();
			return;
		}
		if (!button) {
			button = createEpicArchitectButton();
			const grill = document.getElementById(GRILL_ID);
			if (grill?.nextSibling) bar.insertBefore(button, grill.nextSibling);
			else bar.append(button);
		}
		const run = status?.epic_architect;
		button.toggleAttribute("disabled", Boolean(run && !isRunDone(run)) || !issuePanelHandlers.isIssueOpen());
		button.title = issuePanelHandlers.isIssueOpen() ? "" : "Issue is closed";
	}
	function createStartButton() {
		return createButton(START_ID, "pawchestrator-work-button", `${PAW} Work on this issue`, issuePanelHandlers.startRun);
	}
	function buildIssuePanel(handlers = {}) {
		issuePanelHandlers = {
			...issuePanelHandlers,
			...handlers
		};
		const panel = document.createElement("div");
		panel.id = PANEL_ID;
		panel.dataset.expanded = "false";
		panel.dataset.status = "idle";
		const bar = document.createElement("div");
		bar.className = "pawchestrator-panel-bar";
		const toggle = document.createElement("button");
		toggle.type = "button";
		toggle.className = "pawchestrator-panel-toggle prc-Button-ButtonBase-9n-Xk";
		toggle.setAttribute("aria-label", "Toggle Pawchestrator panel");
		toggle.setAttribute("aria-expanded", "false");
		toggle.textContent = "▸";
		toggle.addEventListener("click", () => {
			const expanded = panel.dataset.expanded !== "true";
			state.panelExpandedByUser = expanded;
			setPanelExpanded(expanded);
		});
		const summary = document.createElement("div");
		summary.className = "pawchestrator-panel-summary";
		const brand = document.createElement("span");
		brand.className = "pawchestrator-panel-brand-name";
		brand.textContent = `${PAW} Pawchestrator`;
		const separator = document.createElement("span");
		separator.setAttribute("aria-hidden", "true");
		separator.textContent = "·";
		const status = document.createElement("span");
		status.className = "pawchestrator-panel-status-text";
		status.textContent = "Checking backend...";
		summary.append(brand, separator, status);
		const body = document.createElement("div");
		body.className = "pawchestrator-panel-body";
		bar.append(toggle, summary, createStartButton(), createGrillButton(), createEpicArchitectButton());
		panel.append(bar, body);
		return panel;
	}
	function injectIssuePanel(handlers = {}) {
		issuePanelHandlers = {
			...issuePanelHandlers,
			...handlers
		};
		const issueBody = findIssueBodyContainer();
		if (!issueBody || !issueBody.parentElement) return false;
		const existingPanel = document.getElementById(PANEL_ID);
		const panel = existingPanel && document.contains(existingPanel) ? existingPanel : buildIssuePanel();
		const innerBox = issueBody.querySelector("[data-testid=\"issue-body\"]");
		const panelOffset = innerBox ? innerBox.getBoundingClientRect().left - issueBody.getBoundingClientRect().left : 0;
		panel.style.marginLeft = `${panelOffset}px`;
		if (panel.previousElementSibling !== issueBody) issueBody.after(panel);
		return true;
	}
	function renderStatus$1(status, callbacks = {}) {
		state.latestIssueStatus = status;
		const panel = document.getElementById(PANEL_ID);
		if (!panel) return;
		updateGrillButton(status.grill);
		updateEpicArchitectButton(status);
		const run = currentRun(status);
		setPanelSummary(summarizeRun(run));
		setPanelStatus(panelStatusForRun$1(run));
		if (state.panelExpandedByUser === null) setPanelExpanded(shouldAutoExpand(status));
		maybeAutoExpandForPipeline(status);
		const body = panel.querySelector(".pawchestrator-panel-body");
		if (!body) return;
		body.textContent = "";
		const readiness = document.createElement("div");
		readiness.className = "pawchestrator-readiness-row";
		renderReadinessItem(readiness, "Backend connected", status.backend_connected);
		renderReadinessItem(readiness, "Repo registered", status.repo_registered);
		renderReadinessItem(readiness, "Claude available", status.runners?.claude?.available);
		renderReadinessItem(readiness, "Codex available", status.runners?.codex?.available);
		body.append(readiness);
		if (run) {
			const line = document.createElement("div");
			line.className = "pawchestrator-run-line";
			line.textContent = `${run.workflow_type || "pipeline"}: ${summarizeRun(run)}`;
			if (run.status === "completed" && run.pr_url) {
				line.append(document.createTextNode(" "));
				const link = document.createElement("a");
				link.href = run.pr_url;
				link.textContent = run.pr_url;
				line.append(link);
			}
			body.append(line);
		}
		renderGrillSection(body, status.grill);
		renderEpicArchitectSection(body, status.epic_architect);
		renderPipeline(body, status.pipeline);
		renderLogSection(body);
		if (status.pipeline?.status === "awaiting_plan_approval" && status.plan_approval_plan) callbacks.renderPlanApprovalSubView?.(status.plan_approval_plan, status.pipeline.run_id);
		renderEpicSection(body, status.epic);
		if (status.grill?.status === "grill_waiting") callbacks.attachGrillReplyObserver?.(status.grill);
		else callbacks.disconnectGrillReplyObserver?.();
	}
	function renderOffline$1(callbacks = {}) {
		state.latestIssueStatus = null;
		callbacks.disconnectGrillReplyObserver?.();
		updateGrillButton(null);
		setPanelSummary(OFFLINE_MESSAGE);
		setPanelStatus("offline");
		const panel = document.getElementById(PANEL_ID);
		const body = panel && panel.querySelector(".pawchestrator-panel-body");
		if (!body) return;
		body.textContent = "";
		const readiness = document.createElement("div");
		readiness.className = "pawchestrator-readiness-row";
		renderReadinessItem(readiness, "Backend connected", false);
		renderReadinessItem(readiness, "Repo registered", false);
		renderReadinessItem(readiness, "Claude available", false);
		renderReadinessItem(readiness, "Codex available", false);
		body.append(readiness);
	}
	var prPanelHandlers = {
		startReview: () => {},
		startRepair: () => {},
		createIssues: () => {},
		isPrMerged: () => false
	};
	function panelStatusForRun(run) {
		if (!run) return "idle";
		if (run.status === "epic_complete") return "done";
		if (run.status === "epic_failed") return "failed";
		if (run.status === "completed" || run.status === "grill_complete" || run.status === "post_complete" || run.status === "issues_complete" || run.status === "issues_skipped" || run.status === "repair_complete" || run.status === "push_complete" || run.status === "epic_architect_complete") return "done";
		if (run.status === "failed" || run.status === "grill_failed" || run.status === "review_failed" || run.status === "post_failed" || run.status === "issues_failed" || run.status === "repair_failed" || run.status === "push_failed" || run.status === "epic_architect_failed") return "failed";
		return "running";
	}
	function stageName(stage) {
		return String(stage?.stage_name || stage?.name || "");
	}
	function stageStatus(stage) {
		return String(stage?.status || "pending").replace(/^[^_]+_/, "");
	}
	function normalizeStepStatus(stage, isAfterActive) {
		if (isAfterActive || !stage) return "pending";
		const status = stageStatus(stage);
		if (status === "running") return "running";
		if (status === "failed") return "failed";
		if (STAGE_DONE.has(status)) return "done";
		return "pending";
	}
	function collapseNamedStages(stages, names) {
		const rows = Array.isArray(stages) ? stages : [];
		return names.map((name) => ({
			name,
			label: name,
			stage: rows.find((stage) => stageName(stage) === name) || {
				stage_name: name,
				status: "pending"
			}
		}));
	}
	function activeNamedStageIndex(run, steps) {
		const failedIndex = steps.findIndex((step) => stageStatus(step.stage) === "failed");
		if (failedIndex >= 0) return failedIndex;
		const current = String(run.current_stage || "");
		const currentIndex = steps.findIndex((step) => step.name === current);
		if (currentIndex >= 0) return currentIndex;
		const runningIndex = steps.findIndex((step) => stageStatus(step.stage) === "running");
		if (runningIndex >= 0) return runningIndex;
		return -1;
	}
	function renderNamedTimeline(parent, run, stageNames, options = {}) {
		const steps = collapseNamedStages(run.stages, stageNames);
		const activeIndex = activeNamedStageIndex(run, steps);
		const timeline = document.createElement("div");
		timeline.className = "pawchestrator-timeline";
		timeline.style.gridTemplateColumns = `repeat(${stageNames.length}, minmax(72px, 1fr))`;
		steps.forEach((step, index) => {
			const status = options.markComplete && index <= activeIndex ? "done" : normalizeStepStatus(step.stage, activeIndex >= 0 && index > activeIndex);
			const item = document.createElement("div");
			item.className = "pawchestrator-step";
			item.dataset.status = status;
			item.dataset.active = String(!options.suppressActive && index === activeIndex && !isRunDone(run));
			const indicator = document.createElement("span");
			indicator.className = "pawchestrator-step-indicator";
			indicator.textContent = status === "done" ? "✓" : status === "failed" ? "×" : "•";
			const label = document.createElement("span");
			label.className = "pawchestrator-step-label";
			label.textContent = step.label;
			item.append(indicator, label);
			timeline.append(item);
		});
		parent.append(timeline);
	}
	function renderReviewTimeline(parent, run) {
		renderNamedTimeline(parent, run, run.workflow_type === "repair" ? REPAIR_STAGES : REVIEW_STAGES);
	}
	function reviewHasSuggestedIssues(run) {
		const suggestedIssues = run?.review_report?.suggested_issues;
		return Array.isArray(suggestedIssues) && suggestedIssues.length > 0 && _stageStatusFromRun(run, "issues") === "pending";
	}
	function _stageStatusFromRun(run, stageName) {
		return (Array.isArray(run?.stages) ? run.stages : []).find((item) => item.stage_name === stageName)?.status || null;
	}
	function isPrRunActive(run) {
		return Boolean(run && !isRunDone(run));
	}
	function summarizePrRun(run) {
		if (!run) {
			if (state.latestPrReviewState === "changes_requested") return "Changes requested";
			return "Ready for review";
		}
		if (run.workflow_type === "repair") return isPrRunActive(run) ? "[repair] running..." : `Repair ${run.status || "complete"}`;
		if (run.status === "post_complete" && reviewHasSuggestedIssues(run)) return "Review complete - suggested issues ready";
		if (run.status === "post_complete" || run.status === "issues_complete" || run.status === "issues_skipped") return "Review complete";
		if (run.status === "review_failed" || run.status === "post_failed" || run.status === "issues_failed") return summarizeError(run);
		return `[${run.current_stage || "review"}] ${(run.status || "pending").replace(/^(review|post|issues)_/, "")}...`;
	}
	function renderPrStatus(run) {
		state.latestPrRun = run;
		setPanelSummary(summarizePrRun(run));
		setPanelStatus(panelStatusForRun(run));
		setPanelExpanded(Boolean(run));
		updatePrActionButtons(run);
		const body = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-body");
		if (!body) return;
		body.textContent = "";
		if (!run) {
			const line = document.createElement("div");
			line.className = "pawchestrator-run-line";
			line.textContent = "No active review run for this PR.";
			body.append(line);
			return;
		}
		const line = document.createElement("div");
		line.className = "pawchestrator-run-line";
		line.textContent = `${run.workflow_type || "review"}: ${summarizePrRun(run)}`;
		body.append(line);
		const section = document.createElement("section");
		section.className = "pawchestrator-pipeline";
		const title = document.createElement("div");
		title.className = "pawchestrator-pipeline-title";
		title.textContent = run.workflow_type === "repair" ? "Repair" : "Review";
		section.append(title);
		renderReviewTimeline(section, run);
		if (reviewHasSuggestedIssues(run)) {
			const issuesLine = document.createElement("div");
			issuesLine.className = "pawchestrator-run-line";
			issuesLine.textContent = "issues: pending";
			issuesLine.append(document.createTextNode(" "));
			issuesLine.append(createButton(CREATE_ISSUES_ID, "pawchestrator-create-issues-button", "Create Issues", prPanelHandlers.createIssues));
			section.append(issuesLine);
		}
		body.append(section);
	}
	function renderPrOffline() {
		state.latestPrRun = null;
		state.latestPrReviewState = null;
		setPanelSummary(OFFLINE_MESSAGE);
		setPanelStatus("offline");
		const body = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-body");
		if (body) body.textContent = "";
		updatePrActionButtons();
		document.getElementById(PR_REPAIR_ID)?.remove();
	}
	function renderPrReviewState(reviewState) {
		state.latestPrReviewState = reviewState;
		updatePrActionButtons(state.latestPrRun);
		if (!state.latestPrRun && reviewState === "changes_requested") setPanelSummary("Changes requested");
	}
	function updatePrActionButtons(run = state.latestPrRun) {
		const active = isPrRunActive(run);
		const merged = prPanelHandlers.isPrMerged();
		const disableMessage = "Pull request is merged";
		const reviewButton = document.getElementById(PR_REVIEW_ID);
		if (reviewButton) {
			reviewButton.toggleAttribute("disabled", active || merged);
			if (merged) reviewButton.title = disableMessage;
			else reviewButton.removeAttribute("title");
		}
		let repairButton = document.getElementById(PR_REPAIR_ID);
		if (state.latestPrReviewState !== "changes_requested") {
			repairButton?.remove();
			return;
		}
		if (!repairButton) {
			repairButton = createRepairButton();
			const bar = document.getElementById(PR_PANEL_ID)?.querySelector(".pawchestrator-panel-bar");
			const review = document.getElementById(PR_REVIEW_ID);
			if (bar) if (review?.nextSibling) bar.insertBefore(repairButton, review.nextSibling);
			else bar.append(repairButton);
		}
		repairButton.toggleAttribute("disabled", active || merged);
		if (merged) repairButton.title = disableMessage;
		else repairButton.removeAttribute("title");
	}
	function createReviewButton() {
		return createButton(PR_REVIEW_ID, "pawchestrator-review-button", `${PAW} Review with Pawchestrator`, prPanelHandlers.startReview);
	}
	function createRepairButton() {
		return createButton(PR_REPAIR_ID, "pawchestrator-repair-button", `${PAW} Work on Request Changes`, prPanelHandlers.startRepair);
	}
	function buildPrPanel(handlers = {}) {
		prPanelHandlers = {
			...prPanelHandlers,
			...handlers
		};
		const panel = document.createElement("div");
		panel.id = PR_PANEL_ID;
		panel.dataset.expanded = "false";
		panel.dataset.status = "idle";
		const bar = document.createElement("div");
		bar.className = "pawchestrator-panel-bar";
		const toggle = document.createElement("button");
		toggle.type = "button";
		toggle.className = "pawchestrator-panel-toggle prc-Button-ButtonBase-9n-Xk";
		toggle.setAttribute("aria-label", "Toggle Pawchestrator panel");
		toggle.setAttribute("aria-expanded", "false");
		toggle.textContent = "▸";
		toggle.addEventListener("click", () => {
			const expanded = panel.dataset.expanded !== "true";
			state.panelExpandedByUser = expanded;
			setPanelExpanded(expanded);
		});
		const summary = document.createElement("div");
		summary.className = "pawchestrator-panel-summary";
		const brand = document.createElement("span");
		brand.className = "pawchestrator-panel-brand-name";
		brand.textContent = `${PAW} Pawchestrator`;
		const separator = document.createElement("span");
		separator.setAttribute("aria-hidden", "true");
		separator.textContent = "·";
		const status = document.createElement("span");
		status.className = "pawchestrator-panel-status-text";
		status.textContent = "Checking review status...";
		summary.append(brand, separator, status);
		const body = document.createElement("div");
		body.className = "pawchestrator-panel-body";
		bar.append(toggle, summary, createReviewButton());
		panel.append(bar, body);
		return panel;
	}
	function injectPrPanel(handlers = {}) {
		prPanelHandlers = {
			...prPanelHandlers,
			...handlers
		};
		const container = findPrConversationContainer();
		if (!container || !container.parentElement) return false;
		const existingPanel = document.getElementById(PR_PANEL_ID);
		const panel = existingPanel && document.contains(existingPanel) ? existingPanel : buildPrPanel();
		panel.style.marginLeft = "";
		if (panel.nextElementSibling !== container) container.before(panel);
		return true;
	}
	function normalizePlanItems(items) {
		return Array.isArray(items) ? items : [];
	}
	function planFileOperations(plan) {
		return normalizePlanItems(plan?.file_operations || plan?.files || plan?.files_to_modify);
	}
	function operationType(operation) {
		return String(operation?.type || operation?.operation || "modify").toLowerCase();
	}
	function operationPath(operation) {
		return operation?.path || operation?.file_path || operation?.file || String(operation || "");
	}
	function operationDescription(operation) {
		return operation?.description || operation?.summary || "";
	}
	function renderPlanFileSection(parent, titleText, operations) {
		if (operations.length === 0) return;
		const title = document.createElement("h4");
		title.className = "pawchestrator-plan-approval-section-title";
		title.textContent = titleText;
		parent.append(title);
		const list = document.createElement("ul");
		list.className = "pawchestrator-plan-approval-list";
		operations.forEach((operation) => {
			const item = document.createElement("li");
			const code = document.createElement("code");
			code.textContent = operationPath(operation);
			item.append(code);
			const description = operationDescription(operation);
			if (description) item.append(document.createTextNode(` - ${description}`));
			list.append(item);
		});
		parent.append(list);
	}
	function removePlanApprovalSubView() {
		document.getElementById(PLAN_APPROVAL_ID)?.remove();
	}
	function resetPlanAttemptForRun(runId) {
		if (runId && state.planAttemptRunId !== runId) {
			state.planAttemptRunId = runId;
			state.planAttempt = 1;
			state.rejectedPlanRunIds.clear();
		}
	}
	function renderRePlanningState() {
		const body = document.getElementById(PANEL_ID)?.querySelector(".pawchestrator-panel-body");
		if (!body) return;
		removePlanApprovalSubView();
		const view = document.createElement("div");
		view.id = PLAN_APPROVAL_ID;
		view.className = "re-planning";
		const spinner = document.createElement("span");
		spinner.className = "spinner";
		view.append(spinner, document.createTextNode(" Re-planning…"));
		body.append(view);
		setPanelExpanded(true);
	}
	function renderPlanApprovalSubView(plan, runId, callbacks = {}) {
		resetPlanAttemptForRun(runId);
		const body = document.getElementById(PANEL_ID)?.querySelector(".pawchestrator-panel-body");
		if (!body) return;
		removePlanApprovalSubView();
		const view = document.createElement("div");
		view.id = PLAN_APPROVAL_ID;
		const header = document.createElement("div");
		header.className = "pawchestrator-plan-approval-header";
		const title = document.createElement("h4");
		title.className = "pawchestrator-plan-approval-title";
		title.textContent = "Plan Approval";
		const attempt = document.createElement("span");
		attempt.className = "pawchestrator-plan-approval-attempt";
		const maxPlanAttempts = state.config.pipeline.plan_approval_max_attempts;
		attempt.textContent = `Plan attempt ${state.planAttempt} of ${maxPlanAttempts}`;
		const risk = String(plan?.estimated_risk || "medium").toLowerCase();
		const badge = document.createElement("span");
		badge.className = `risk-badge risk-${[
			"low",
			"medium",
			"high"
		].includes(risk) ? risk : "medium"}`;
		badge.textContent = `Risk: ${risk}`;
		header.append(title, attempt, badge);
		view.append(header);
		const summary = document.createElement("div");
		summary.className = "pawchestrator-plan-approval-summary prc-Text-Text-0ima0";
		summary.textContent = plan?.approach_summary || "";
		view.append(summary);
		const filesTitle = document.createElement("h4");
		filesTitle.className = "pawchestrator-plan-approval-section-title";
		filesTitle.textContent = "Files";
		view.append(filesTitle);
		const operations = planFileOperations(plan);
		const grouped = {
			Modify: operations.filter((operation) => operationType(operation) === "modify"),
			Create: operations.filter((operation) => operationType(operation) === "create"),
			Delete: operations.filter((operation) => operationType(operation) === "delete")
		};
		renderPlanFileSection(view, "Modify", grouped.Modify);
		renderPlanFileSection(view, "Create", grouped.Create);
		renderPlanFileSection(view, "Delete", grouped.Delete);
		const stepsTitle = document.createElement("h4");
		stepsTitle.className = "pawchestrator-plan-approval-section-title";
		stepsTitle.textContent = "Steps";
		view.append(stepsTitle);
		const steps = document.createElement("ol");
		steps.className = "pawchestrator-plan-approval-list";
		normalizePlanItems(plan?.steps).forEach((step) => {
			const item = document.createElement("li");
			const description = document.createElement("div");
			description.textContent = step?.description || String(step || "");
			item.append(description);
			const affectedFiles = normalizePlanItems(step?.affected_files || step?.files_to_modify || step?.files);
			if (affectedFiles.length > 0) {
				const files = document.createElement("div");
				files.className = "pawchestrator-plan-step-files";
				files.textContent = `Affected files: ${affectedFiles.join(", ")}`;
				item.append(files);
			}
			if (step?.notes) {
				const notes = document.createElement("div");
				notes.className = "pawchestrator-plan-step-notes";
				notes.textContent = step.notes;
				item.append(notes);
			}
			steps.append(item);
		});
		view.append(steps);
		const error = document.createElement("div");
		error.className = "pawchestrator-plan-approval-error";
		error.hidden = true;
		view.append(error);
		const feedbackArea = document.createElement("div");
		feedbackArea.className = "pawchestrator-plan-feedback";
		feedbackArea.style.display = "none";
		const feedback = document.createElement("textarea");
		feedback.placeholder = "Describe what should change…";
		const feedbackActions = document.createElement("div");
		feedbackActions.className = "pawchestrator-plan-approval-actions";
		const cancelBtn = createButton("", "pawchestrator-plan-reject-cancel-button", "Cancel", () => {
			feedback.value = "";
			error.hidden = true;
			error.textContent = "";
			feedbackArea.style.display = "none";
			rejectBtn.style.display = "";
		});
		const submitFeedbackBtn = createButton("", "pawchestrator-plan-submit-feedback-button", "Submit Feedback", () => {
			handlePlanRejection(runId, feedback.value, submitFeedbackBtn, cancelBtn, feedbackArea, error, callbacks);
		});
		submitFeedbackBtn.disabled = true;
		feedback.addEventListener("input", () => {
			submitFeedbackBtn.disabled = feedback.value.trim().length === 0;
		});
		feedbackActions.append(cancelBtn, submitFeedbackBtn);
		feedbackArea.append(feedback, feedbackActions);
		view.append(feedbackArea);
		const actions = document.createElement("div");
		actions.className = "pawchestrator-plan-approval-actions";
		const abortBtn = createButton("", "pawchestrator-plan-abort-button", "Abort", () => {
			handlePlanApprovalAction(runId, "abort", abortBtn, approveBtn, error, callbacks);
		});
		abortBtn.classList.add("btn-danger");
		const rejectBtn = createButton("", "pawchestrator-plan-reject-button", "Reject", () => {
			rejectBtn.style.display = "none";
			feedbackArea.style.display = "grid";
			feedback.focus();
		});
		const approveBtn = createButton("", "pawchestrator-plan-approve-button", "Approve", () => {
			handlePlanApprovalAction(runId, "approve", approveBtn, abortBtn, error, callbacks);
		});
		approveBtn.classList.add("btn-primary");
		actions.append(abortBtn, rejectBtn, approveBtn);
		view.append(actions);
		body.append(view);
		setPanelExpanded(true);
	}
	function setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, disabled) {
		[primaryButton, secondaryButton].forEach((button) => {
			button.disabled = disabled;
		});
		setButtonText(primaryButton, disabled ? "…" : primaryButton.dataset.idleLabel);
		setButtonText(secondaryButton, secondaryButton.dataset.idleLabel);
	}
	function setPlanFeedbackButtonsDisabled(submitButton, cancelButton, disabled) {
		submitButton.disabled = disabled;
		cancelButton.disabled = disabled;
		setButtonText(submitButton, disabled ? "…" : submitButton.dataset.idleLabel);
	}
	async function handlePlanRejection(runId, feedback, submitButton, cancelButton, feedbackArea, errorElement, callbacks) {
		const trimmedFeedback = feedback.trim();
		if (!runId) {
			errorElement.textContent = "No run id found for this plan approval.";
			errorElement.hidden = false;
			return;
		}
		if (!trimmedFeedback) {
			submitButton.disabled = true;
			return;
		}
		errorElement.hidden = true;
		errorElement.textContent = "";
		setPlanFeedbackButtonsDisabled(submitButton, cancelButton, true);
		try {
			await requestJson(`/runs/${runId}/reject`, {
				method: "POST",
				body: JSON.stringify({ feedback: trimmedFeedback }),
				label: "Plan rejection request"
			});
			state.rejectedPlanRunIds.add(runId);
			feedbackArea.style.display = "none";
			renderRePlanningState();
			callbacks.startIssueStatusPolling?.();
		} catch (error) {
			errorElement.textContent = error.message;
			errorElement.hidden = false;
			feedbackArea.style.display = "grid";
			setPlanFeedbackButtonsDisabled(submitButton, cancelButton, false);
			submitButton.disabled = trimmedFeedback.length === 0;
		}
	}
	async function handlePlanApprovalAction(runId, action, primaryButton, secondaryButton, errorElement, callbacks) {
		if (!runId) {
			errorElement.textContent = "No run id found for this plan approval.";
			errorElement.hidden = false;
			return;
		}
		errorElement.hidden = true;
		errorElement.textContent = "";
		setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, true);
		try {
			const run = await requestJson(`/runs/${runId}/${action}`, {
				method: "POST",
				label: `Plan ${action} request`
			});
			removePlanApprovalSubView();
			if (action === "abort") {
				callbacks.renderStatus?.({
					...state.latestIssueStatus || {},
					pipeline: {
						...(state.latestIssueStatus || {}).pipeline || {},
						...run || {},
						run_id: run?.run_id || run?.id || runId,
						status: run?.status || "failed"
					}
				});
				return;
			}
			callbacks.startIssueStatusPolling?.();
		} catch (error) {
			errorElement.textContent = error.message;
			errorElement.hidden = false;
			setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, false);
		}
	}
	function isIssueOpen$1() {
		return document.querySelector("[data-testid=\"header-state\"]")?.dataset.status === "issueOpened";
	}
	function prRunKey() {
		return `pawchestrator_pr_run:${window.location.pathname}`;
	}
	function commentElementId(commentId) {
		return `issuecomment-${commentId}`;
	}
	function findGrillReplyForm(commentElement) {
		if (!commentElement) return null;
		return Array.from(commentElement.querySelectorAll("form")).find((form) => form.querySelector("textarea, [contenteditable='true']") && findGrillReplySubmit(form)) || null;
	}
	function buttonText(button) {
		return (button?.textContent || "").replace(/\s+/g, " ").trim();
	}
	function findGrillReplySubmit(form) {
		return Array.from(form.querySelectorAll("button, input[type='submit']")).find((button) => {
			if (button.disabled) return false;
			if ((button.getAttribute("type") || "submit").toLowerCase() !== "submit") return false;
			return buttonText(button) === "Comment" || buttonText(button) === "Answer Questions" || button.value === "Comment" || button.value === "Answer Questions";
		}) || null;
	}
	function decorateGrillReplyForm(form) {
		const submit = findGrillReplySubmit(form);
		if (!submit) return;
		if (submit.tagName === "INPUT") submit.value = "Answer Questions";
		else setButtonText(submit, "Answer Questions");
		submit.title = GRILL_REPLY_TOOLTIP;
		submit.setAttribute("aria-label", GRILL_REPLY_TOOLTIP);
	}
	async function continueGrillFromReply() {
		const issue = parseIssueReference();
		await requestJson("/issue/grill", {
			method: "POST",
			label: "Grill reply request",
			statusSetter: setPanelSummary,
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(issue)
		});
		startIssueStatusPolling();
	}
	function disconnectGrillReplyObserver() {
		if (state.grillReplyObserverState?.observer) state.grillReplyObserverState.observer.disconnect();
		state.grillReplyObserverState = null;
	}
	function evaluateGrillReplyForm() {
		const observerState = state.grillReplyObserverState;
		if (!observerState || !document.contains(observerState.commentElement)) return;
		const form = findGrillReplyForm(observerState.commentElement);
		if (form) {
			observerState.formSeen = true;
			decorateGrillReplyForm(form);
			return;
		}
		if (observerState.formSeen && !observerState.posted) {
			observerState.posted = true;
			continueGrillFromReply().catch((error) => setPanelSummary(error.message));
		}
	}
	function attachGrillReplyObserver(grill) {
		const commentId = grill?.github_comment_id;
		if (!commentId) {
			disconnectGrillReplyObserver();
			return;
		}
		const commentElement = document.getElementById(commentElementId(commentId));
		if (!commentElement) {
			disconnectGrillReplyObserver();
			return;
		}
		if (state.grillReplyObserverState?.commentId === String(commentId) && state.grillReplyObserverState.commentElement === commentElement) {
			evaluateGrillReplyForm();
			return;
		}
		disconnectGrillReplyObserver();
		const observer = new MutationObserver(evaluateGrillReplyForm);
		state.grillReplyObserverState = {
			commentId: String(commentId),
			commentElement,
			observer,
			formSeen: false,
			posted: false
		};
		observer.observe(commentElement, {
			childList: true,
			subtree: true
		});
		evaluateGrillReplyForm();
	}
	async function pollPrStatusOnce() {
		const pr = parsePrReference();
		const reviewStatePromise = fetchPrReviewState(pr);
		const storedRunId = await GM_getValue(prRunKey());
		if (storedRunId) {
			const storedRun = await fetchPrRun(storedRunId);
			if (isPrRunActive(storedRun)) {
				renderPrReviewState((await reviewStatePromise).state);
				renderPrStatus(storedRun);
				return true;
			}
			const [status, reviewState] = await Promise.all([fetchPrStatus(pr), reviewStatePromise]);
			renderPrReviewState(reviewState.state);
			const activeRun = [status.repair, status.review].filter(Boolean).find(isPrRunActive);
			const run = activeRun || storedRun;
			if (activeRun?.id && activeRun.id !== storedRunId) await GM_setValue(prRunKey(), activeRun.id);
			renderPrStatus(run);
			return isPrRunActive(run);
		}
		const [status, reviewState] = await Promise.all([fetchPrStatus(pr), reviewStatePromise]);
		renderPrReviewState(reviewState.state);
		const run = [status.repair, status.review].filter(Boolean).find(isPrRunActive) || status.review || status.repair || null;
		if (run?.id) await GM_setValue(prRunKey(), run.id);
		renderPrStatus(run);
		if (!run) {
			renderPrStatus(null);
			return false;
		}
		return isPrRunActive(run);
	}
	function startPrStatusPolling() {
		stopPrStatusPolling();
		pollPrStatusOnce().catch(() => renderPrOffline());
		state.activePrPoll = window.setInterval(() => {
			pollPrStatusOnce().then((running) => {
				if (!running && state.activePrPoll) stopPrStatusPolling();
			}).catch(() => renderPrOffline());
		}, POLL_INTERVAL_MS);
	}
	function stopPrStatusPolling() {
		if (state.activePrPoll) {
			window.clearInterval(state.activePrPoll);
			state.activePrPoll = null;
		}
	}
	function renderStatus(status) {
		updateGrillButton(status.grill);
		renderStatus$1(status, {
			renderPlanApprovalSubView: (plan, runId) => renderPlanApprovalSubView(plan, runId, {
				renderStatus,
				startIssueStatusPolling
			}),
			attachGrillReplyObserver,
			disconnectGrillReplyObserver
		});
	}
	function renderOffline() {
		renderOffline$1({ disconnectGrillReplyObserver });
	}
	var RUN_LOG_LIMIT = 200;
	function activeIssueRun(status) {
		const run = currentRun(status);
		return run && !isRunDone(run) && run.run_id ? run : null;
	}
	function stopIssueStatusPollTimer() {
		if (state.activePoll) {
			window.clearInterval(state.activePoll);
			state.activePoll = null;
		}
	}
	function closeIssueStream() {
		if (state.activeRunStream) state.activeRunStream.close();
		state.activeRunStream = null;
		state.activeRunId = null;
		state.sseConnected = false;
	}
	function appendRunLogLine(event) {
		const stage = event.stage_name || event.stage || state.latestIssueStatus?.pipeline?.current_stage || "run";
		const message = event.message || event.line || event.text || "";
		state.runLogLines.push(`[${stage}] ${message}`);
		if (state.runLogLines.length > RUN_LOG_LIMIT) state.runLogLines.splice(0, state.runLogLines.length - RUN_LOG_LIMIT);
	}
	function mergeRunEvent(event) {
		if (!state.latestIssueStatus) return;
		const runId = event.run_id || state.activeRunId;
		const keys = [
			"pipeline",
			"grill",
			"epic_architect"
		];
		const key = keys.find((candidate) => state.latestIssueStatus?.[candidate]?.run_id === runId) || "pipeline";
		const run = state.latestIssueStatus[key] || { run_id: runId };
		const nextRun = {
			...run,
			...event.run,
			run_id: runId
		};
		if (event.stage_name || event.stage) nextRun.current_stage = event.stage_name || event.stage;
		if (event.status) nextRun.status = event.status;
		if (Array.isArray(event.stages)) nextRun.stages = event.stages;
		if (event.pr_url) nextRun.pr_url = event.pr_url;
		if (event.warning) nextRun.warnings = [
			...Array.isArray(run.warnings) ? run.warnings : [],
			event.warning
		];
		else if (event.message && event.type === "warning") nextRun.warnings = [
			...Array.isArray(run.warnings) ? run.warnings : [],
			{
				stage_name: event.stage_name,
				code: event.code,
				message: event.message
			}
		];
		state.latestIssueStatus = {
			...state.latestIssueStatus,
			[key]: nextRun
		};
	}
	function parseRunStreamEvent(message) {
		try {
			return JSON.parse(message.data || "{}");
		} catch {
			return {};
		}
	}
	function handleRunStreamEvent(kind, message) {
		const event = {
			...parseRunStreamEvent(message),
			type: kind
		};
		if (kind === "log_line") appendRunLogLine(event);
		else mergeRunEvent(event);
		if (state.latestIssueStatus) renderStatus(state.latestIssueStatus);
		if (kind === "run_complete" || kind === "run_failed") {
			closeIssueStream();
			startIssueStatusPolling();
		}
	}
	async function openIssueStream(runId) {
		closeIssueStream();
		stopIssueStatusPollTimer();
		state.activeRunId = runId;
		const stream = openRunStream(runId);
		state.activeRunStream = stream;
		stream.onopen = () => {
			state.sseConnected = true;
			stopIssueStatusPollTimer();
		};
		stream.onerror = () => {
			closeIssueStream();
			startIssueStatusPolling();
		};
		["stage_transition", "warning", "run_complete", "run_failed", "log_line"].forEach((kind) => {
			stream.addEventListener(kind, (message) => handleRunStreamEvent(kind, message));
		});
	}
	function maybeSwitchToRunStream(status) {
		const run = activeIssueRun(status);
		if (!run) {
			if (state.activeRunStream) closeIssueStream();
			return false;
		}
		if (state.activeRunId === run.run_id && state.activeRunStream) return true;
		state.runLogLines = [];
		openIssueStream(run.run_id).catch(() => startIssueStatusPolling());
		return true;
	}
	async function pollIssueStatusOnce() {
		if (state.activeRunStream && state.sseConnected) return true;
		const status = await fetchIssueStatus(parseIssueReference());
		if (status.pipeline?.run_id && status.pipeline.run_id !== state.planAttemptRunId) resetPlanAttemptForRun(status.pipeline.run_id);
		if (status.pipeline?.status === "awaiting_plan_approval" && status.pipeline.run_id) {
			if (state.rejectedPlanRunIds.has(status.pipeline.run_id)) {
				state.planAttempt += 1;
				state.rejectedPlanRunIds.delete(status.pipeline.run_id);
			}
			status.plan_approval_plan = await requestJson(`/runs/${status.pipeline.run_id}/plan`, { label: "Plan request" });
		}
		renderStatus(status);
		const streaming = maybeSwitchToRunStream(status);
		const run = currentRun(status);
		const running = run && !isRunDone(run);
		const issueOpen = isIssueOpen$1();
		const anyActive = Boolean(status.pipeline && !isRunDone(status.pipeline) || status.grill && !isRunDone(status.grill) || status.epic_architect && !isRunDone(status.epic_architect) || status.epic && !isEpicDone(status.epic) || !isEpicDone(status.epic) && epicSubRuns(status.epic).some((run) => !isRunDone(run)));
		const shouldDisable = !issueOpen || anyActive;
		const closedTitle = !issueOpen ? "Issue is closed" : "";
		for (const id of [
			START_ID,
			GRILL_ID,
			EPIC_ARCHITECT_ID
		]) {
			const btn = document.getElementById(id);
			if (!btn) continue;
			btn.toggleAttribute("disabled", shouldDisable);
			btn.title = closedTitle;
		}
		return streaming || running;
	}
	function startIssueStatusPolling() {
		stopIssueStatusPollTimer();
		pollIssueStatusOnce().catch(() => renderOffline());
		state.activePoll = window.setInterval(() => {
			pollIssueStatusOnce().catch(() => {
				renderOffline();
				if (isIssueOpen$1()) {
					document.getElementById(START_ID)?.removeAttribute("disabled");
					document.getElementById(GRILL_ID)?.removeAttribute("disabled");
					document.getElementById(EPIC_ARCHITECT_ID)?.removeAttribute("disabled");
				}
			});
		}, POLL_INTERVAL_MS);
	}
	function stopIssueStatusPolling() {
		stopIssueStatusPollTimer();
		closeIssueStream();
	}
	function isIssueOpen() {
		return document.querySelector("[data-testid=\"header-state\"]")?.dataset.status === "issueOpened";
	}
	function isPrMerged() {
		return Boolean(document.querySelector("[data-status=\"pullMerged\"]"));
	}
	function startRun() {
		return startRun$1({
			renderStatus,
			startIssueStatusPolling
		});
	}
	function startGrill() {
		return startGrill$1({
			startIssueStatusPolling,
			updateGrillButton
		});
	}
	function startEpicArchitect() {
		return startEpicArchitect$1({
			renderStatus,
			startIssueStatusPolling
		});
	}
	function startReview() {
		return startReview$1({
			renderPrStatus,
			startPrStatusPolling
		});
	}
	function startRepair() {
		return startRepair$1({
			renderPrStatus,
			startPrStatusPolling
		});
	}
	function createIssues() {
		return startCreateIssues({
			renderPrStatus,
			startPrStatusPolling
		});
	}
	function removeInjectedControls() {
		document.getElementById(PANEL_ID)?.remove();
		document.getElementById(PR_PANEL_ID)?.remove();
		state.lastPipelineExpansionKey = null;
		disconnectGrillReplyObserver();
		stopIssueStatusPolling();
		stopPrStatusPolling();
	}
	function injectIssueControls() {
		if (!isIssuePage()) {
			removeInjectedControls();
			return;
		}
		if (injectIssuePanel({
			startRun,
			startGrill,
			startEpicArchitect,
			isIssueOpen
		}) && !state.activePoll) startIssueStatusPolling();
	}
	function injectPrControls() {
		if (!isPrPage()) return false;
		document.getElementById(PANEL_ID)?.remove();
		stopIssueStatusPolling();
		const panelReady = injectPrPanel({
			startReview,
			startRepair,
			createIssues,
			isPrMerged
		});
		if (panelReady && !state.activePrPoll) startPrStatusPolling();
		return panelReady;
	}
	function injectControls() {
		if (isPrPage()) {
			injectPrControls();
			return;
		}
		document.getElementById(PR_PANEL_ID)?.remove();
		stopPrStatusPolling();
		injectIssueControls();
	}
	function scheduleInjection(injectControls) {
		const pathnameChanged = state.activePathname !== window.location.pathname;
		if (pathnameChanged) {
			state.activePathname = window.location.pathname;
			state.panelExpandedByUser = null;
			state.lastPipelineExpansionKey = null;
			stopIssueStatusPolling();
			stopPrStatusPolling();
		}
		if (state.reinjectTimer) window.clearTimeout(state.reinjectTimer);
		state.reinjectTimer = window.setTimeout(() => {
			state.reinjectTimer = null;
			injectControls();
		}, pathnameChanged ? 0 : 100);
	}
	function installNavigationHooks(injectControls) {
		["pushState", "replaceState"].forEach((method) => {
			const original = history[method];
			history[method] = function(...args) {
				const result = original.apply(this, args);
				scheduleInjection(injectControls);
				return result;
			};
		});
		[
			"turbo:load",
			"turbo:render",
			"popstate"
		].forEach((eventName) => {
			window.addEventListener(eventName, () => scheduleInjection(injectControls));
		});
	}
	(function() {
		"use strict";
		injectControls();
		installNavigationHooks(injectControls);
		new MutationObserver(() => {
			scheduleInjection(injectControls);
		}).observe(document.documentElement, {
			childList: true,
			subtree: true
		});
	})();
})();
