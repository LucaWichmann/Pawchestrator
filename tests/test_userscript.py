from pathlib import Path


USERSCRIPT = Path(__file__).resolve().parents[1] / "Pawchestrator.user.js"


def _read_userscript() -> str:
    return USERSCRIPT.read_text(encoding="utf-8")


def test_userscript_header_is_tampermonkey_installable() -> None:
    source = _read_userscript()

    assert "// ==UserScript==" in source
    assert "// @match        https://github.com/*" in source
    assert "// @match        https://github.com/*/*/issues/*" not in source
    assert "// @run-at       document-idle" in source
    assert "// @grant        GM_addStyle" in source
    assert "// @grant        GM_xmlhttpRequest" in source
    assert "// @connect      127.0.0.1" in source
    assert (
        "// @downloadURL  https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/Pawchestrator.user.js"
        in source
    )
    assert (
        "// @updateURL    https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/Pawchestrator.user.js"
        in source
    )


def test_userscript_uses_issue_status_backend_contract() -> None:
    source = _read_userscript()

    assert 'const API_BASE = "http://127.0.0.1:38472"' in source
    assert "GM_xmlhttpRequest" in source
    assert 'requestJson(`/issue/${issue.owner}/${issue.repo}/${issue.number}/status`' in source
    assert 'requestJson("/issue/start"' in source
    assert 'requestJson("/issue/grill"' in source
    assert 'requestJson("/issue/epic-architect"' in source
    assert "requestJson(`/runs/${runId}`" not in source
    assert "fetchRun" not in source
    assert "body: JSON.stringify(issue)" in source


def test_userscript_renders_panel_and_readiness_states() -> None:
    source = _read_userscript()

    assert 'const PAW = "\\uD83D\\uDC3E"' in source
    assert 'const PANEL_ID = "pawchestrator-panel"' in source
    assert 'brand.className = "pawchestrator-panel-brand-name"' in source
    assert 'brand.textContent = `${PAW} Pawchestrator`' in source
    assert 'status.className = "pawchestrator-panel-status-text"' in source
    assert 'status.textContent = "Checking backend..."' in source
    assert 'panel.dataset.status = "idle"' in source
    assert "function setPanelStatus(state)" in source
    assert "setPanelStatus(panelStatusForRun(run))" in source
    assert 'setPanelStatus("offline")' in source
    assert "status.textContent = message" in source
    assert '[data-status="running"]' in source
    assert "border-left-color: var(--fgColor-accent" in source
    assert "border-left-color: var(--fgColor-success" in source
    assert "border-left-color: var(--fgColor-danger" in source
    assert "Backend connected" in source
    assert "Repo registered" in source
    assert "Claude available" in source
    assert "Codex available" in source
    assert 'const OFFLINE_MESSAGE = "Pawchestrator not running \\u2014 start with `pawchestrator serve`"' in source
    assert "Draft PR ready" in source
    assert "failed" in source


def test_userscript_renders_pipeline_timeline_section() -> None:
    source = _read_userscript()

    assert 'const PIPELINE_STAGES = ["snapshot", "scout", "plan", "implement", "verify", "pr"]' in source
    assert 'const EPIC_ARCHITECT_STAGES = ["epic_scout", "epic_architect", "creating"]' in source
    assert "function collapseStages(stages)" in source
    assert "function renderPipeline(parent, pipeline)" in source
    assert 'section.className = "pawchestrator-pipeline"' in source
    assert 'timeline.className = "pawchestrator-timeline"' in source
    assert 'item.dataset.status = status' in source
    assert "function renderPipelineTimeline(parent, pipeline, options = {})" in source
    assert "!options.suppressActive && index === activeIndex && pipeline.status !== \"completed\"" in source
    assert 'status === "done" ? "\\u2713" : status === "failed" ? "\\u00D7" : "\\u2022"' in source
    assert '`${name} (repair ${repairCount}/${repairTotal || repairCount})`' in source
    assert "renderPipeline(body, status.pipeline)" in source


def test_userscript_detects_awaiting_plan_approval_and_fetches_plan() -> None:
    source = _read_userscript()

    assert '"awaiting_plan_approval"' in source
    assert 'status.pipeline?.status === "awaiting_plan_approval"' in source
    assert "status.plan_approval_plan = await fetchPlan(status.pipeline.run_id)" in source
    assert "function fetchPlan(runId)" in source
    assert "requestJson(`/runs/${runId}/plan`" in source
    assert "renderPlanApprovalSubView(status.plan_approval_plan, status.pipeline.run_id)" in source


def test_userscript_renders_plan_approval_subview_content() -> None:
    source = _read_userscript()

    assert 'const PLAN_APPROVAL_ID = "pawchestrator-plan-approval"' in source
    assert "function renderPlanApprovalSubView(plan, runId)" in source
    assert "view.id = PLAN_APPROVAL_ID" in source
    assert "summary.textContent = plan?.approach_summary" in source
    assert "badge.textContent = `Risk: ${risk}`" in source
    assert 'badge.className = `risk-badge risk-${["low", "medium", "high"].includes(risk) ? risk : "medium"}`' in source
    assert 'stepsTitle.textContent = "Steps"' in source
    assert 'description.textContent = step?.description || String(step || "")' in source
    assert 'files.textContent = `Affected files: ${affectedFiles.join(", ")}`' in source
    assert "notes.textContent = step.notes" in source


def test_userscript_plan_approval_renders_approve_and_abort_actions() -> None:
    source = _read_userscript()

    assert 'actions.className = "pawchestrator-plan-approval-actions"' in source
    assert '"pawchestrator-plan-approve-button", "Approve"' in source
    assert 'approveBtn.classList.add("btn-primary")' in source
    assert '"pawchestrator-plan-abort-button", "Abort"' in source
    assert 'abortBtn.classList.add("btn-danger")' in source
    assert ".pawchestrator-plan-approval-actions" in source


def test_userscript_plan_approval_approve_posts_and_resumes_polling() -> None:
    source = _read_userscript()

    assert "function handlePlanApprovalAction(runId, action, primaryButton, secondaryButton, errorElement)" in source
    assert "await requestJson(`/runs/${runId}/${action}`" in source
    assert 'method: "POST"' in source
    assert 'handlePlanApprovalAction(runId, "approve", approveBtn, abortBtn, error)' in source
    assert "removePlanApprovalSubView()" in source
    assert "startIssueStatusPolling()" in source


def test_userscript_plan_approval_abort_posts_and_marks_failed() -> None:
    source = _read_userscript()

    assert 'handlePlanApprovalAction(runId, "abort", abortBtn, approveBtn, error)' in source
    assert 'if (action === "abort")' in source
    assert 'status: run?.status || "failed"' in source
    assert "renderStatus({" in source


def test_userscript_plan_approval_disables_buttons_during_request() -> None:
    source = _read_userscript()

    assert "function setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, disabled)" in source
    assert "button.disabled = disabled" in source
    assert 'setButtonText(primaryButton, disabled ? "\\u2026" : primaryButton.dataset.idleLabel)' in source
    assert "setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, true)" in source
    assert "setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, false)" in source


def test_userscript_plan_approval_error_reenables_buttons() -> None:
    source = _read_userscript()

    assert 'error.className = "pawchestrator-plan-approval-error"' in source
    assert "errorElement.textContent = error.message" in source
    assert "errorElement.hidden = false" in source
    assert "setPlanApprovalButtonsDisabled(primaryButton, secondaryButton, false)" in source


def test_userscript_groups_plan_file_operations_by_type() -> None:
    source = _read_userscript()

    assert "function planFileOperations(plan)" in source
    assert "function renderPlanFileSection(parent, titleText, operations)" in source
    assert 'filesTitle.textContent = "Files"' in source
    assert 'Modify: operations.filter((operation) => operationType(operation) === "modify")' in source
    assert 'Create: operations.filter((operation) => operationType(operation) === "create")' in source
    assert 'Delete: operations.filter((operation) => operationType(operation) === "delete")' in source
    assert 'renderPlanFileSection(view, "Modify", grouped.Modify)' in source
    assert 'renderPlanFileSection(view, "Create", grouped.Create)' in source
    assert 'renderPlanFileSection(view, "Delete", grouped.Delete)' in source
    assert "code.textContent = operationPath(operation)" in source


def test_userscript_plan_approval_is_idempotent_and_uses_panel_styles() -> None:
    source = _read_userscript()

    assert "document.getElementById(PLAN_APPROVAL_ID)?.remove()" in source
    assert "body.textContent = \"\"" in source
    assert "setPanelExpanded(true)" in source
    assert "#${PANEL_ID} #${PLAN_APPROVAL_ID}" in source
    assert "prc-Text-Text-0ima0" in source
    assert ".risk-badge" in source
    assert ".risk-low" in source
    assert "var(--bgColor-success-muted" in source
    assert ".risk-medium" in source
    assert "var(--bgColor-attention-muted" in source
    assert ".risk-high" in source
    assert "var(--bgColor-danger-muted" in source


def test_userscript_renders_independent_grill_section() -> None:
    source = _read_userscript()

    assert "function renderGrillSection(parent, grill)" in source
    assert "if (!grill)" in source
    assert 'section.className = "pawchestrator-grill-section"' in source
    assert 'title.textContent = "Grill"' in source
    assert "renderPipeline(body, status.pipeline)" in source
    assert "renderGrillSection(body, status.grill)" in source
    assert source.index("renderGrillSection(body, status.grill)") < source.index("renderPipeline(body, status.pipeline)")


def test_userscript_renders_epic_architect_section_states() -> None:
    source = _read_userscript()

    assert "function renderEpicArchitectSection(parent, run)" in source
    assert 'section.className = "pawchestrator-epic-architect-section"' in source
    assert 'title.textContent = "EpicArchitect"' in source
    assert "renderNamedTimeline(section, epicArchitectTimelineRun(run), EPIC_ARCHITECT_STAGES" in source
    assert 'analysis.className = "pawchestrator-epic-architect-analysis"' in source
    assert "analysis.textContent = run.epic_analysis" in source
    assert "function renderCreatedSubIssueLinks(parent, created)" in source
    assert "link.href = issue.url" in source
    assert 'link.textContent = `#${issue.number}${issue.title ? ` ${issue.title}` : ""}`' in source
    assert 'error.className = "pawchestrator-epic-architect-error"' in source
    assert "error.textContent = summarizeError(run)" in source
    assert 'partial.textContent = `Created before failure: ${created.map((issue) => `#${issue.number}`).join(", ")}`' in source
    assert "renderGrillSection(body, status.grill)" in source
    assert "renderEpicArchitectSection(body, status.epic_architect)" in source
    assert source.index("renderGrillSection(body, status.grill)") < source.index(
        "renderEpicArchitectSection(body, status.epic_architect)"
    )


def test_userscript_renders_epic_section_with_sub_run_timelines() -> None:
    source = _read_userscript()

    assert "function renderEpicSection(parent, epic)" in source
    assert 'section.className = "pawchestrator-epic-section"' in source
    assert 'title.textContent = `Epic: ${epicStatus(epic)}`' in source
    assert 'row.className = "pawchestrator-epic-run"' in source
    assert 'rowTitle.textContent = `#${subRun.issue_number}${titleText}`' in source
    assert "renderPipelineTimeline(row, subRun, { suppressActive: epicDone })" in source
    assert "renderEpicSection(body, status.epic)" in source
    assert source.index("renderPipeline(body, status.pipeline)") < source.index("renderEpicSection(body, status.epic)")
    assert source.index("renderGrillSection(body, status.grill)") < source.index("renderPipeline(body, status.pipeline)")


def test_userscript_renders_epic_verification_timeline() -> None:
    source = _read_userscript()

    assert 'verification.className = "pawchestrator-epic-verification"' in source
    assert 'verificationTitle.className = "pawchestrator-epic-verification-title"' in source
    assert 'verificationTitle.textContent = "Epic Verification"' in source
    assert "function epicParentStages(epic)" in source
    assert "Array.isArray(epic?.parent_stages)" in source
    assert 'return name === "verify" || name === "implement"' in source
    assert "if (parentStages.length > 0)" in source
    assert "renderPipelineTimeline(verification, {" in source
    assert "stages: parentStages" in source
    assert "current_stage: epic.current_stage" in source
    assert "status: epic.status || epicStatus(epic)" in source
    assert "}, { suppressActive: epicDone })" in source
    assert source.index('section.append(list)') < source.index('verification.className = "pawchestrator-epic-verification"')


def test_userscript_epic_updates_panel_status_and_auto_expand() -> None:
    source = _read_userscript()

    assert "function epicSummaryRun(epic)" in source
    assert 'workflow_type: "epic"' in source
    assert "const runs = [epicSummaryRun(status.epic), status.pipeline, status.grill, status.epic_architect].filter(Boolean)" in source
    assert "function epicStatus(epic)" in source
    assert "function isRunDone(run)" in source
    assert "function isEpicDone(epic)" in source
    assert 'RUN_DONE.has(status) || /_failed$/.test(status)' in source
    assert 'run.status === "running" || /_running$/.test(run.status || "")' in source
    assert "epicSubRuns(status.epic).some((run) => !isRunDone(run))" in source
    assert "(status.epic_architect && !isRunDone(status.epic_architect))" in source
    assert "const running = run && !isRunDone(run)" in source


def test_userscript_failed_epic_child_runs_do_not_block_restart() -> None:
    source = _read_userscript()

    assert "function isRunDone(run)" in source
    assert "function isEpicDone(epic)" in source
    assert "RUN_DONE.has(status) || /_failed$/.test(status)" in source
    assert "(!isEpicDone(status.epic) && epicSubRuns(status.epic).some((run) => !isRunDone(run)))" in source
    assert "epicSubRuns(status.epic).some((run) => !RUN_DONE.has(run.status))" not in source


def test_userscript_terminal_epic_suppresses_child_activity() -> None:
    source = _read_userscript()

    assert 'if (run.status === "epic_failed")' in source
    assert 'return "failed"' in source
    assert "const epicDone = isEpicDone(epic)" in source
    assert "renderPipelineTimeline(row, subRun, { suppressActive: epicDone })" in source
    assert "!options.suppressActive && index === activeIndex" in source


def test_userscript_renders_grill_status_outcome_and_failures() -> None:
    source = _read_userscript()

    assert "function grillReport(grill)" in source
    assert "function countGrillValue(grill, report, countKey, listKey)" in source
    assert "function grillBodyUpdated(grill, report)" in source
    assert 'status.textContent = active ? "[grill] running..." : `Status: ${grill.status || "unknown"}`' in source
    assert 'status.dataset.active = String(active)' in source
    assert '"criteria_count", "suggested_criteria"' in source
    assert '"questions_posted_count", "unanswerable_questions"' in source
    assert '"Issue body updated", grillBodyUpdated(grill, report) ? "yes" : "no"' in source
    assert "grill.updated_at || grill.completed_at || grill.started_at" in source
    assert 'error.className = "pawchestrator-grill-error"' in source
    assert "error.textContent = summarizeError(grill)" in source


def test_userscript_observes_questions_comment_reply_form() -> None:
    source = _read_userscript()

    assert "function attachGrillReplyObserver(grill)" in source
    assert "document.getElementById(commentElementId(commentId))" in source
    assert "observer.observe(commentElement, { childList: true, subtree: true })" in source
    assert 'status.grill?.status === "grill_waiting"' in source
    assert "attachGrillReplyObserver(status.grill)" in source
    assert "disconnectGrillReplyObserver()" in source
    assert '"Answer Questions"' in source
    assert (
        '"Replying to Pawchestrator questions \\u2014 submitting will continue the grilling session."'
        in source
    )
    assert 'submit.title = GRILL_REPLY_TOOLTIP' in source
    assert 'await requestJson("/issue/grill"' in source
    assert "if (state.formSeen && !state.posted)" in source
    assert "findGrillReplyForm(state.commentElement)" in source


def test_userscript_renders_pipeline_warnings_and_completed_pr_link_only() -> None:
    source = _read_userscript()

    assert 'const WARNING = "\\u26A0"' in source
    assert 'details.className = "pawchestrator-warnings"' in source
    assert 'summary.textContent = `${WARNING} Warnings`' in source
    assert "const warnings = Array.isArray(pipeline.warnings) ? pipeline.warnings : []" in source
    assert "if (warnings.length > 0)" in source
    assert 'if (pipeline.status === "completed" && pipeline.pr_url)' in source
    assert 'if (run.status === "completed" && run.pr_url)' in source


def test_userscript_injects_panel_after_github_issue_body() -> None:
    source = _read_userscript()

    assert ".IssueBody-module__outerContainer__ULNTb" in source
    assert '[class*="IssueBody-module__outerContainer"]' in source
    assert "function findIssueBodyContainer()" in source
    assert 'issueBody.querySelector(\'[data-testid="issue-body"]\')' in source
    assert "innerBox.getBoundingClientRect().left - issueBody.getBoundingClientRect().left" in source
    assert "panel.style.marginLeft = `${panelOffset}px`" in source
    assert ": 0" in source
    assert "issueBody.after(panel)" in source
    assert "panel.previousElementSibling !== issueBody" in source


def test_userscript_renders_action_buttons_inside_panel_bar() -> None:
    source = _read_userscript()

    assert 'bar.className = "pawchestrator-panel-bar"' in source
    assert "createStartButton" in source
    assert "createGrillButton" in source
    assert "createEpicArchitectButton" in source
    assert "bar.append(toggle, summary, createStartButton(), createGrillButton(), createEpicArchitectButton())" in source
    assert source.index("createStartButton()") < source.index("panel.append(bar, body)")
    assert source.index("createGrillButton()") < source.index("panel.append(bar, body)")
    assert source.index("createEpicArchitectButton()") < source.index("panel.append(bar, body)")
    assert "injectHeaderActions" not in source
    assert "findHeaderActions" not in source
    assert "findNewIssueHost" not in source
    assert '[data-testid="issue-header"] [data-component="PH_Actions"]' not in source
    assert 'button.dataset.component = "Button"' in source
    assert 'button.dataset.size = "medium"' in source
    assert 'button.dataset.variant = "default"' in source
    assert 'button.className = "prc-Button-ButtonBase-9n-Xk"' in source
    assert 'content.dataset.component = "buttonContent"' in source
    assert 'label.className = "prc-Button-Label-FWkx3"' in source
    assert "STATUS_ID" not in source
    assert "GRILL_STATUS_ID" not in source


def test_userscript_epic_architect_button_visibility_and_start_contract() -> None:
    source = _read_userscript()

    assert 'const EPIC_ARCHITECT_ID = "pawchestrator-epic-architect"' in source
    assert 'const CONSTRUCTION = "\\uD83C\\uDFD7\\uFE0F"' in source
    assert '`${CONSTRUCTION} Turn into Epic`' in source
    assert "function issueAlreadyHasSubIssues(status)" in source
    assert "epicArchitectCreatedIssues(status?.epic_architect).length > 0" in source
    assert "status?.issue?.sub_issues_summary || status?.sub_issues_summary" in source
    assert "button?.remove()" in source
    assert "function startEpicArchitect()" in source
    assert 'requestJson("/issue/epic-architect"' in source
    assert "await GM_setValue(epicArchitectRunKey(), response.run_id)" in source
    assert "button.toggleAttribute(\"disabled\", Boolean(run && !isRunDone(run)) || !isIssueOpen())" in source


def test_userscript_panel_uses_github_css_variables_and_button_classes() -> None:
    source = _read_userscript()

    assert "--fgColor-default" in source
    assert "--bgColor-default" in source
    assert "--borderColor-default" in source
    assert "prc-Button-ButtonBase-9n-Xk" in source
    assert "prc-Button-ButtonContent-Iohp5" in source
    assert "prc-Button-Label-FWkx3" in source


def test_userscript_collapses_by_default_and_auto_expands_for_runs() -> None:
    source = _read_userscript()

    assert 'panel.dataset.expanded = "false"' in source
    assert "let panelExpandedByUser = null" in source
    assert "function shouldAutoExpand(status)" in source
    assert "status.pipeline ||" in source
    assert "status.grill ||" in source
    assert "setPanelExpanded(shouldAutoExpand(status))" in source
    assert 'panel.dataset.expanded = String(expanded)' in source
    assert 'toggle.setAttribute("aria-expanded", String(expanded))' in source


def test_userscript_epic_confirm_gate_and_pending_start_render() -> None:
    source = _read_userscript()

    assert "const status = await fetchIssueStatus(issue)" in source
    assert "if (status.epic_confirm && !confirmEpicStart(status.epic))" in source
    assert "if (status.grill?.status === GRILL_WAITING_STATUS)" in source
    assert (
        "Grill is still waiting for answers on this issue. "
        "Are you sure you want to start agentic work?"
    ) in source
    assert "showConfirmDialog(PIPELINE_GRILL_WAITING_CONFIRM_MESSAGE" in source
    assert 'title: "Start agentic work?"' in source
    assert 'confirmLabel: "Yes"' in source
    assert 'cancelLabel: "No"' in source
    assert "function confirmEpicStart(epic)" in source
    assert "window.confirm" in source
    assert 'response?.type === "epic"' in source
    assert "function epicFromStartResponse(response)" in source
    assert 'status: "pending"' in source
    assert "stages: PIPELINE_STAGES.map((stage_name) => ({ stage_name, status: \"pending\" }))" in source


def test_userscript_non_epic_start_path_still_posts_issue() -> None:
    source = _read_userscript()

    assert 'requestJson("/issue/start"' in source
    assert "body: JSON.stringify(issue)" in source
    assert "startIssueStatusPolling()" in source


def test_userscript_avoids_sidebar_body_and_floating_fallbacks() -> None:
    source = _read_userscript()

    assert 'document.querySelector(\'[data-testid="sidebar"]\')' not in source
    assert 'document.querySelector(\'[data-testid="issue-viewer-sidebar"]\')' not in source
    assert 'document.querySelector(".Layout-sidebar")' not in source
    assert "pawchestrator-floating" not in source
    assert "document.body" not in source


def test_userscript_polls_every_three_seconds_and_reinjects() -> None:
    source = _read_userscript()

    assert "const POLL_INTERVAL_MS = 3000" in source
    assert "const REINJECT_DEBOUNCE_MS = 100" in source
    assert "window.setInterval" in source
    assert "new MutationObserver" in source
    assert "scheduleInjection" in source
    assert "window.clearTimeout(reinjectTimer)" in source
    assert "window.setTimeout" in source


def test_userscript_reinjects_after_client_side_navigation() -> None:
    source = _read_userscript()

    assert "let activePathname = window.location.pathname" in source
    assert "const pathnameChanged = activePathname !== window.location.pathname" in source
    assert "activePathname = window.location.pathname" in source
    assert "pathnameChanged ? 0 : REINJECT_DEBOUNCE_MS" in source
    assert "installNavigationHooks" in source
    assert '["pushState", "replaceState"]' in source
    assert "original.apply(this, args)" in source
    assert '["turbo:load", "turbo:render", "popstate"]' in source
    assert "window.addEventListener(eventName, scheduleInjection)" in source


def test_userscript_gates_injection_to_issue_pages() -> None:
    source = _read_userscript()

    assert "function isIssuePage()" in source
    assert 'type === "issues"' in source
    assert "String(issueNumber) === number" in source
    assert "issueNumber > 0" in source
    assert "!extra" in source
    assert "if (!isIssuePage())" in source
    assert "removeInjectedControls()" in source
    assert "return;" in source


def test_userscript_removes_controls_on_non_issue_pages() -> None:
    source = _read_userscript()

    assert "function removeInjectedControls()" in source
    assert "document.getElementById(PANEL_ID)?.remove()" in source
    assert "[START_ID, GRILL_ID, PANEL_ID].forEach" not in source
    assert "stopIssueStatusPolling()" in source


def test_userscript_rehomes_stale_panel_only() -> None:
    source = _read_userscript()

    assert "existingPanel && document.contains(existingPanel) ? existingPanel : createPanel()" in source
    assert "existingButton && document.contains(existingButton) ? existingButton : createStartButton()" not in source
    assert "existingGrillButton && document.contains(existingGrillButton) ? existingGrillButton : createGrillButton()" not in source
    assert "button.parentElement !== actions" not in source
    assert "grillButton.parentElement !== actions" not in source
    assert "panel.previousElementSibling !== issueBody" in source
