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
    assert 'const OFFLINE_MESSAGE = "Pawchestrator not running \\u2014 start with `pawchestrator serve`"' in source
    assert "Draft PR ready:" in source
    assert "failed" in source


def test_userscript_polls_every_three_seconds_and_reinjects() -> None:
    source = _read_userscript()

    assert "const POLL_INTERVAL_MS = 3000" in source
    assert "window.setInterval" in source
    assert "new MutationObserver" in source
    assert 'document.querySelector(\'[data-testid="sidebar"]\')' in source
    assert 'document.querySelector(".Layout-sidebar")' in source
