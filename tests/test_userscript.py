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
    assert "Pawchestrator \\u00B7" in source
    assert "Backend connected" in source
    assert "Repo registered" in source
    assert "Claude available" in source
    assert "Codex available" in source
    assert 'const OFFLINE_MESSAGE = "Pawchestrator not running \\u2014 start with `pawchestrator serve`"' in source
    assert "Draft PR ready" in source
    assert "failed" in source


def test_userscript_injects_panel_after_github_issue_body() -> None:
    source = _read_userscript()

    assert ".IssueBody-module__outerContainer__ULNTb" in source
    assert '[class*="IssueBody-module__outerContainer"]' in source
    assert "function findIssueBodyContainer()" in source
    assert "issueBody.after(panel)" in source
    assert "panel.previousElementSibling !== issueBody" in source


def test_userscript_keeps_only_action_buttons_in_issue_header() -> None:
    source = _read_userscript()

    assert '[data-testid="issue-header"] [data-component="PH_Actions"]' in source
    assert "HeaderMenu-module__menuActionsContainer__K0Mga" in source
    assert "findHeaderActions" in source
    assert "findNewIssueHost" in source
    assert "createStartButton" in source
    assert "createGrillButton" in source
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
    assert "status.pipeline || status.grill" in source
    assert "setPanelExpanded(shouldAutoExpand(status))" in source
    assert 'panel.dataset.expanded = String(expanded)' in source
    assert 'toggle.setAttribute("aria-expanded", String(expanded))' in source


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
    assert "[START_ID, GRILL_ID, PANEL_ID].forEach" in source
    assert "document.getElementById(id)" in source
    assert "element.remove()" in source
    assert "stopIssueStatusPolling()" in source


def test_userscript_rehomes_stale_header_controls_and_panel() -> None:
    source = _read_userscript()

    assert "existingButton && document.contains(existingButton) ? existingButton : createStartButton()" in source
    assert "existingGrillButton && document.contains(existingGrillButton) ? existingGrillButton : createGrillButton()" in source
    assert "existingPanel && document.contains(existingPanel) ? existingPanel : createPanel()" in source
    assert "button.parentElement !== actions" in source
    assert "grillButton.parentElement !== actions" in source
    assert "panel.previousElementSibling !== issueBody" in source
