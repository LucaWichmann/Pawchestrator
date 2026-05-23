from pathlib import Path


USERSCRIPT = Path(__file__).resolve().parents[1] / "Pawchestrator.user.js"


def _read_userscript() -> str:
    return USERSCRIPT.read_text(encoding="utf-8")


def test_userscript_header_is_tampermonkey_installable() -> None:
    source = _read_userscript()

    assert "// ==UserScript==" in source
    assert "// @match        https://github.com/*/*/issues/*" in source
    assert "// @run-at       document-idle" in source
    assert "// @grant        GM_addStyle" in source


def test_userscript_uses_local_backend_contract() -> None:
    source = _read_userscript()

    assert 'const API_BASE = "http://127.0.0.1:38472"' in source
    assert 'fetch(`${API_BASE}/health`)' in source
    assert 'fetch(`${API_BASE}/issue/start`' in source
    assert 'fetch(`${API_BASE}/runs/${runId}`)' in source
    assert "body: JSON.stringify(issue)" in source


def test_userscript_renders_issue_action_and_states() -> None:
    source = _read_userscript()

    assert 'const PAW = "\\uD83D\\uDC3E"' in source
    assert "Work on this issue" in source
    assert 'data-testid="pawchestrator-work-button"' not in source
    assert 'button.dataset.testid = "pawchestrator-work-button"' in source
    assert 'const OFFLINE_MESSAGE = "Pawchestrator not running \\u2014 start with `pawchestrator serve`"' in source
    assert "Draft PR ready:" in source
    assert "failed" in source


def test_userscript_injects_into_github_issue_header() -> None:
    source = _read_userscript()

    assert '[data-testid="issue-header"] [data-component="PH_Actions"]' in source
    assert "HeaderMenu-module__menuActionsContainer__K0Mga" in source
    assert "findHeaderActions" in source
    assert "findNewIssueHost" in source
    assert 'button.dataset.component = "Button"' in source
    assert 'button.dataset.size = "medium"' in source
    assert 'button.dataset.variant = "default"' in source
    assert 'button.className = "prc-Button-ButtonBase-9n-Xk"' in source
    assert 'content.dataset.component = "buttonContent"' in source
    assert 'label.className = "prc-Button-Label-FWkx3"' in source
    assert 'a[href$="/issues/new/choose"], a[href*="/issues/new"]' in source


def test_userscript_avoids_sidebar_and_floating_fallbacks() -> None:
    source = _read_userscript()

    assert 'document.querySelector(\'[data-testid="sidebar"]\')' not in source
    assert 'document.querySelector(\'[data-testid="issue-viewer-sidebar"]\')' not in source
    assert 'document.querySelector(".Layout-sidebar")' not in source
    assert "pawchestrator-floating" not in source
    assert "document.body" not in source


def test_userscript_polls_every_three_seconds_and_reinjects() -> None:
    source = _read_userscript()

    assert "const POLL_INTERVAL_MS = 3000" in source
    assert "window.setInterval" in source
    assert "new MutationObserver" in source
