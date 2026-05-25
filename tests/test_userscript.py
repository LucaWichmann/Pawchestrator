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
    assert "function collapseStages(stages)" in source
    assert "function renderPipeline(parent, pipeline)" in source
    assert 'section.className = "pawchestrator-pipeline"' in source
    assert 'timeline.className = "pawchestrator-timeline"' in source
    assert 'item.dataset.status = status' in source
    assert 'item.dataset.active = String(index === activeIndex && pipeline.status !== "completed")' in source
    assert 'status === "done" ? "\\u2713" : status === "failed" ? "\\u00D7" : "\\u2022"' in source
    assert '`${name} (repair ${repairCount}/${repairTotal || repairCount})`' in source
    assert "renderPipeline(body, status.pipeline)" in source


def test_userscript_renders_independent_grill_section() -> None:
    source = _read_userscript()

    assert "function renderGrillSection(parent, grill)" in source
    assert "if (!grill)" in source
    assert 'section.className = "pawchestrator-grill-section"' in source
    assert 'title.textContent = "Grill"' in source
    assert "renderPipeline(body, status.pipeline)" in source
    assert "renderGrillSection(body, status.grill)" in source
    assert source.index("renderPipeline(body, status.pipeline)") < source.index("renderGrillSection(body, status.grill)")


def test_userscript_renders_epic_section_with_sub_run_timelines() -> None:
    source = _read_userscript()

    assert "function renderEpicSection(parent, epic)" in source
    assert 'section.className = "pawchestrator-epic-section"' in source
    assert 'title.textContent = `Epic: ${epicStatus(epic)}`' in source
    assert 'row.className = "pawchestrator-epic-run"' in source
    assert 'rowTitle.textContent = `#${subRun.issue_number}${titleText}`' in source
    assert "renderPipelineTimeline(row, subRun)" in source
    assert "renderEpicSection(body, status.epic)" in source
    assert source.index("renderPipeline(body, status.pipeline)") < source.index("renderEpicSection(body, status.epic)")
    assert source.index("renderEpicSection(body, status.epic)") < source.index("renderGrillSection(body, status.grill)")


def test_userscript_epic_updates_panel_status_and_auto_expand() -> None:
    source = _read_userscript()

    assert "function epicSummaryRun(epic)" in source
    assert 'workflow_type: "epic"' in source
    assert "const runs = [epicSummaryRun(status.epic), status.pipeline, status.grill].filter(Boolean)" in source
    assert "function epicStatus(epic)" in source
    assert 'run.status === "running" || /_running$/.test(run.status || "")' in source
    assert "epicSubRuns(status.epic).some((run) => !RUN_DONE.has(run.status))" in source


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
    assert "bar.append(toggle, summary, createStartButton(), createGrillButton())" in source
    assert source.index("createStartButton()") < source.index("panel.append(bar, body)")
    assert source.index("createGrillButton()") < source.index("panel.append(bar, body)")
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
