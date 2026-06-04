import json
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import httpx
from fastapi.testclient import TestClient

from pawchestrator.approval_gate import register_approval_event
from pawchestrator.config import LOCAL_HOST, PipelineSettings, Settings
from pawchestrator.db import init_db
from pawchestrator.github import GitHubIssueClient
from pawchestrator.server import create_app
from pawchestrator.sessions import save_sessions


def test_create_app_prints_loaded_config_when_debug_enabled(
    tmp_path: Path,
    capsys,
) -> None:
    settings = Settings(app_dir=tmp_path, debug=True)
    settings.runners.codex.model = "gpt-test"

    create_app(settings)

    output = capsys.readouterr().out
    assert "[pawchestrator:debug] config:" in output
    assert '"debug": true' in output
    assert '"app_dir":' in output
    assert '"model": "gpt-test"' in output
    _, config_json = output.split("[pawchestrator:debug] config:\n", 1)
    payload = json.loads(config_json)
    assert payload["app_dir"] == str(tmp_path)
    assert payload["runners"]["codex"]["model"] == "gpt-test"


def test_create_app_does_not_print_loaded_config_when_debug_disabled(
    tmp_path: Path,
    capsys,
) -> None:
    create_app(Settings(app_dir=tmp_path, debug=False))

    output = capsys.readouterr().out
    assert "[pawchestrator:debug] config:" not in output


def test_health_returns_version_and_local_bind(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "pawchestrator"
    assert payload["version"]
    assert payload["status"] == "ok"
    assert payload["database"]["status"] == "ok"
    assert payload["bind"] == {"host": LOCAL_HOST, "localhost_only": True}
    assert (tmp_path / "database.sqlite").exists()


def test_config_returns_pipeline_settings(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/config", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {
        "pipeline": {
            "verify_repair_attempts": 3,
            "plan_approval_max_attempts": 3,
            "smart_routing": {
                "enabled": False,
                "skip_plan_when": ["implement"],
                "require_readiness": ["ready"],
                "require_max_risk": "low",
                "confirm_skip": False,
            },
        },
    }


def test_config_returns_custom_pipeline_settings(tmp_path: Path) -> None:
    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(
            verify_repair_attempts=1,
            plan_approval_max_attempts=5,
            smart_routing={
                "enabled": True,
                "skip_plan_when": ["implement", "verify"],
                "require_readiness": ["ready", "accepted"],
                "require_max_risk": "medium",
                "confirm_skip": True,
            },
        ),
    )
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/config", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {
        "pipeline": {
            "verify_repair_attempts": 1,
            "plan_approval_max_attempts": 5,
            "smart_routing": {
                "enabled": True,
                "skip_plan_when": ["implement", "verify"],
                "require_readiness": ["ready", "accepted"],
                "require_max_risk": "medium",
                "confirm_skip": True,
            },
        },
    }


def test_config_requires_auth(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/config")

    assert response.status_code == 403


def test_run_state_returns_run_stages_and_artifacts(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        _insert_run_state(settings)
        response = client.get("/runs/run-123", headers=_token_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "run-123"
    assert payload["status"] == "snapshot_complete"
    assert payload["stages"][0]["stage_name"] == "snapshot"
    assert payload["artifacts"][0]["artifact_type"] == "issue_snapshot"


def test_run_state_returns_404_for_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/missing", headers=_token_headers())

    assert response.status_code == 404


def test_run_plan_returns_projection_with_file_operations(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)
    _write_implementation_plan(
        settings,
        "run-123",
        {
            "approach_summary": "Add a route.",
            "estimated_risk": "low",
            "file_operations": [
                {
                    "path": "src/api/users.py",
                    "type": "modify",
                    "description": "Add GET /users route.",
                }
            ],
            "steps": [
                {
                    "order": 1,
                    "description": "Add route.",
                    "files_to_modify": ["src/api/users.py"],
                    "notes": "Keep it small.",
                }
            ],
        },
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123/plan", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {
        "approach_summary": "Add a route.",
        "estimated_risk": "low",
        "file_operations": [
            {
                "path": "src/api/users.py",
                "type": "modify",
                "description": "Add GET /users route.",
            }
        ],
        "steps": [
            {
                "order": 1,
                "description": "Add route.",
                "files_to_modify": ["src/api/users.py"],
                "notes": "Keep it small.",
            }
        ],
    }


def test_run_plan_falls_back_to_files_to_modify(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)
    _write_implementation_plan(
        settings,
        "run-123",
        {
            "approach_summary": "Update files.",
            "files_to_modify": ["pawchestrator/server.py", "tests/test_server.py"],
        },
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123/plan", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {
        "approach_summary": "Update files.",
        "estimated_risk": "medium",
        "file_operations": [
            {"path": "pawchestrator/server.py", "type": "modify", "description": ""},
            {"path": "tests/test_server.py", "type": "modify", "description": ""},
        ],
        "steps": [],
    }


def test_run_plan_returns_404_for_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/missing/plan", headers=_token_headers())

    assert response.status_code == 404


def test_run_plan_returns_404_for_missing_artifact(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123/plan", headers=_token_headers())

    assert response.status_code == 404


def test_run_plan_requires_pairing_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123/plan")

    assert response.status_code == 403


def test_approve_non_awaiting_run_returns_409(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/run-123/approve", headers=_token_headers())

    assert response.status_code == 409


def test_abort_non_awaiting_run_returns_409(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/run-123/abort", headers=_token_headers())

    assert response.status_code == 409


def test_approve_signals_plan_approval_event(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings, status="awaiting_plan_approval", current_stage="plan")
    _seed_token(settings)
    event = register_approval_event("run-123")

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/run-123/approve", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-123", "decision": "approve"}
    assert event.is_set() is True


def test_abort_signals_plan_approval_event(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings, status="awaiting_plan_approval", current_stage="plan")
    _seed_token(settings)
    event = register_approval_event("run-123")

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/run-123/abort", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-123", "decision": "abort"}
    assert event.is_set() is True


def test_reject_non_awaiting_run_returns_409(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/reject",
            headers=_token_headers(),
            json={"feedback": "Use axios instead of fetch."},
        )

    assert response.status_code == 409


def test_reject_signals_plan_approval_event_and_writes_feedback(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings, status="awaiting_plan_approval", current_stage="plan")
    _seed_token(settings)
    event = register_approval_event("run-123")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/reject",
            headers=_token_headers(),
            json={"feedback": "Use axios instead of fetch."},
        )

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-123", "decision": "reject"}
    assert event.is_set() is True
    path = tmp_path / "runs" / "run-123" / "plan_rejections.json"
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"attempt": 1, "feedback": "Use axios instead of fetch."}
    ]


def test_reject_appends_feedback(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings, status="awaiting_plan_approval", current_stage="plan")
    _seed_token(settings)
    rejections_path = tmp_path / "runs" / "run-123" / "plan_rejections.json"
    rejections_path.parent.mkdir(parents=True)
    rejections_path.write_text(
        json.dumps([{"attempt": 1, "feedback": "Use axios instead of fetch."}]),
        encoding="utf-8",
    )
    register_approval_event("run-123")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/run-123/reject",
            headers=_token_headers(),
            json={"feedback": "Move logic into a service class."},
        )

    assert response.status_code == 200
    assert json.loads(rejections_path.read_text(encoding="utf-8")) == [
        {"attempt": 1, "feedback": "Use axios instead of fetch."},
        {"attempt": 2, "feedback": "Move logic into a service class."},
    ]


def test_issue_start_returns_run_id_and_schedules_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []
    sub_issue_client = _FakeSubIssueClient([])
    _patch_sub_issue_client(monkeypatch, sub_issue_client)

    async def fake_run_pipeline(
        issue_url: str,
        runtime_settings: Settings,
        *,
        run_id: str,
        allow_empty_commit: bool = False,
    ):
        calls.append((issue_url, runtime_settings.app_dir, run_id, allow_empty_commit))

    monkeypatch.setattr("pawchestrator.server.run_pipeline", fake_run_pipeline)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/issue/start",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"type": "pipeline", "run_id": run_id}
    assert sub_issue_client.fetched is True
    assert calls == [
        ("https://github.com/owner/repo/issues/42", tmp_path, run_id, False),
    ]
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["status"] == "pending"
    assert [stage["stage_name"] for stage in payload["stages"]] == [
        "snapshot",
        "scout",
        "plan",
        "implement",
        "verify",
        "pr",
    ]
    assert {stage["status"] for stage in payload["stages"]} == {"pending"}


def test_issue_start_routes_epic_when_sub_issues_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    client = _FakeSubIssueClient(
        [
            {
                "number": 43,
                "title": "First child",
                "url": "https://github.com/owner/repo/issues/43",
            }
        ],
        settings=settings,
    )
    calls = []
    _patch_sub_issue_client(monkeypatch, client)
    _insert_repo_registration(settings, tmp_path)

    async def fake_run_epic(
        issue_url: str,
        runtime_settings: Settings,
        *,
        repo_path: Path,
        group_id: str,
        parent_run_id: str,
    ):
        calls.append((issue_url, runtime_settings.app_dir, repo_path, group_id, parent_run_id))
        return SimpleNamespace(
            group_id=group_id,
            sub_runs=[SimpleNamespace(issue_number=43, title="First child", run_id="run-43")],
        )

    async def fail_run_pipeline(*_args, **_kwargs):
        raise AssertionError("pipeline should not be scheduled for epics")

    monkeypatch.setattr("pawchestrator.server.run_epic", fake_run_epic)
    monkeypatch.setattr("pawchestrator.server.run_pipeline", fail_run_pipeline)

    with TestClient(create_app(settings)) as client_app:
        response = client_app.post(
            "/issue/start",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["type"] == "epic"
    assert payload["run_id"]
    assert payload["group_id"]
    assert payload["sub_runs"] == [
        {"issue_number": 43, "title": "First child", "run_id": ""}
    ]
    assert client.fetched is True
    assert client.run_count_at_fetch == 0
    assert calls == [
        (
            "https://github.com/owner/repo/issues/42",
            tmp_path,
            tmp_path.resolve(),
            payload["group_id"],
            payload["run_id"],
        )
    ]


def test_issue_start_resumes_latest_failed_epic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _insert_repo_registration(settings, tmp_path)
    _insert_failed_epic_resume_state(settings)
    client = _FakeSubIssueClient(
        [
            {
                "number": 43,
                "title": "Done",
                "url": "https://github.com/owner/repo/issues/43",
            },
            {
                "number": 44,
                "title": "Retry",
                "url": "https://github.com/owner/repo/issues/44",
            },
        ],
        settings=settings,
    )
    calls = []
    _patch_sub_issue_client(monkeypatch, client)

    async def fake_run_epic(
        issue_url: str,
        runtime_settings: Settings,
        *,
        repo_path: Path,
        group_id: str,
        parent_run_id: str,
    ):
        calls.append((issue_url, runtime_settings.app_dir, repo_path, group_id, parent_run_id))
        return SimpleNamespace(group_id=group_id, sub_runs=[])

    monkeypatch.setattr("pawchestrator.server.run_epic", fake_run_epic)

    with TestClient(create_app(settings)) as client_app:
        response = client_app.post(
            "/issue/start",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["type"] == "epic"
    assert payload["resumed"] is True
    assert payload["run_id"] == "failed-epic"
    assert payload["group_id"] == "resume-group"
    assert payload["status"] == "epic_running"
    assert payload["mode"] == "epic-with-sub-issues"
    assert payload["branch"] == "paw/epic-42-resume"
    assert payload["pr_url"] == "https://github.com/owner/repo/pull/42"
    assert payload["sub_runs"] == [
        {"issue_number": 43, "title": "Done", "run_id": "done-43"},
        {"issue_number": 44, "title": "Retry", "run_id": "failed-44"},
    ]
    assert calls == [
        (
            "https://github.com/owner/repo/issues/42",
            tmp_path,
            tmp_path.resolve(),
            "resume-group",
            "failed-epic",
        )
    ]


def test_issue_start_background_failure_does_not_raise(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _patch_sub_issue_client(monkeypatch, _FakeSubIssueClient([]))

    async def fake_run_pipeline(
        issue_url: str,
        runtime_settings: Settings,
        *,
        run_id: str,
        allow_empty_commit: bool = False,
    ):
        assert allow_empty_commit is False
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr("pawchestrator.server.run_pipeline", fake_run_pipeline)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/issue/start",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}", headers=_token_headers())

    assert response.status_code == 200
    assert state_response.json()["status"] == "failed"


def test_issue_grill_returns_run_id_and_schedules_grill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []

    async def fake_run_grill(
        issue_url: str,
        runtime_settings: Settings,
        *,
        run_id: str,
    ):
        calls.append((issue_url, runtime_settings.app_dir, run_id))

    monkeypatch.setattr("pawchestrator.server.run_grill", fake_run_grill)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/issue/grill",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id}
    assert calls == [
        ("https://github.com/owner/repo/issues/42", tmp_path, run_id),
    ]
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["workflow_type"] == "grill"


def test_issue_epic_architect_returns_run_id_and_runs_scout_then_architect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []

    async def fake_run_epic_scout(issue_url: str, settings: Settings, *, run_id: str):
        calls.append(("scout", issue_url, settings.app_dir, run_id))

    async def fake_run_epic_architect(issue_url: str, settings: Settings, *, run_id: str):
        calls.append(("architect", issue_url, settings.app_dir, run_id))

    monkeypatch.setattr("pawchestrator.server.run_epic_scout", fake_run_epic_scout)
    monkeypatch.setattr(
        "pawchestrator.server.run_epic_architect",
        fake_run_epic_architect,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/issue/epic-architect",
            json={"owner": "owner", "repo": "repo", "number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id}
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["workflow_type"] == "epic_architect"
    assert payload["issue_number"] == 42
    assert payload["status"] == "completed"
    assert payload["current_stage"] == "epic_architect"
    assert calls == [
        ("scout", "https://github.com/owner/repo/issues/42", tmp_path, run_id),
        ("architect", "https://github.com/owner/repo/issues/42", tmp_path, run_id),
    ]


def test_review_start_returns_run_id_and_schedules_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []

    async def fake_run_review(
        run_id: str,
        runtime_settings: Settings,
        *,
        implement_runner: str | None = None,
    ):
        calls.append(("review", run_id, runtime_settings.app_dir, implement_runner))

    async def fake_run_review_post(run_id: str, runtime_settings: Settings):
        calls.append(("post", run_id, runtime_settings.app_dir, None))

    monkeypatch.setattr("pawchestrator.server.run_review", fake_run_review)
    monkeypatch.setattr("pawchestrator.server.run_review_post", fake_run_review_post)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/review/start",
            json={"owner": "owner", "repo": "repo", "pr_number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}/status", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id}
    assert calls == [
        ("review", run_id, tmp_path, None),
        ("post", run_id, tmp_path, None),
    ]
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["workflow_type"] == "review"
    assert payload["pr_number"] == 42


def test_repair_start_returns_run_id_and_schedules_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []

    async def fake_run_repair(run_id: str, runtime_settings: Settings):
        calls.append((run_id, runtime_settings.app_dir))

    monkeypatch.setattr("pawchestrator.server.run_repair", fake_run_repair)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/runs/repair/start",
            json={"owner": "owner", "repo": "repo", "pr_number": 42},
            headers=_token_headers(),
        )
        run_id = response.json()["run_id"]
        state_response = client.get(f"/runs/{run_id}/status", headers=_token_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id}
    assert calls == [(run_id, tmp_path)]
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["workflow_type"] == "repair"
    assert payload["pr_number"] == 42
    assert [stage["stage_name"] for stage in payload["stages"]] == ["repair", "push"]
    assert {stage["status"] for stage in payload["stages"]} == {"pending"}


def test_openapi_exposes_issue_grill_route(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/openapi.json", headers=_token_headers())

    assert response.status_code == 200
    assert "/issue/grill" in response.json()["paths"]
    assert "/issue/epic-architect" in response.json()["paths"]


def test_cors_allows_github_for_issue_start_and_runs(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        issue_response = client.options(
            "/issue/start",
            headers={
                "Origin": "https://github.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        runs_response = client.options(
            "/runs/run-123",
            headers={
                "Origin": "https://github.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        status_response = client.options(
            "/issue/owner/repo/42/status",
            headers={
                "Origin": "https://github.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert issue_response.status_code == 200
    assert runs_response.status_code == 200
    assert status_response.status_code == 200
    assert issue_response.headers["access-control-allow-origin"] == "https://github.com"
    assert runs_response.headers["access-control-allow-origin"] == "https://github.com"
    assert status_response.headers["access-control-allow-origin"] == "https://github.com"


def test_protected_routes_require_pairing_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)

    with TestClient(create_app(settings)) as client:
        missing_response = client.get("/runs/run-123")
        wrong_response = client.get(
            "/runs/run-123",
            headers={"X-Pawchestrator-Token": "wrong-token"},
        )

    assert missing_response.status_code == 403
    assert wrong_response.status_code == 403


def test_protected_routes_accept_stored_pairing_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123", headers=_token_headers())

    assert response.status_code == 200


def test_issue_status_returns_epic_confirm_setting(tmp_path: Path, monkeypatch) -> None:
    from pawchestrator.config import PipelineSettings

    settings = Settings(
        app_dir=tmp_path,
        pipeline=PipelineSettings(epic_confirm=True),
    )
    _seed_token(settings)

    async def fake_runner_health(_settings: Settings):
        return {"claude": {"available": True}}

    monkeypatch.setattr("pawchestrator.server.get_runner_health", fake_runner_health)

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json()["epic_confirm"] is True


def test_pr_review_state_returns_changes_requested_with_mock_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path == "/repos/owner/repo/issues/42/labels":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {"state": "APPROVED"},
                {"state": "CHANGES_REQUESTED"},
            ],
        )

    _patch_github_client(
        monkeypatch,
        GitHubIssueClient(
            "token",
            api_base="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/prs/owner/repo/42/review-state",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"state": "changes_requested"}
    assert [request.url.path for request in requests] == [
        "/repos/owner/repo/pulls/42/reviews",
        "/repos/owner/repo/issues/42/labels",
    ]


def test_pr_review_state_returns_approved_when_latest_review_approved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/owner/repo/issues/42/labels":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {"state": "COMMENTED"},
                {"state": "APPROVED"},
            ],
        )

    _patch_github_client(
        monkeypatch,
        GitHubIssueClient(
            "token",
            api_base="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/prs/owner/repo/42/review-state",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"state": "approved"}


def test_pr_review_state_returns_open_without_terminal_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/owner/repo/issues/42/labels":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"state": "COMMENTED"}])

    _patch_github_client(
        monkeypatch,
        GitHubIssueClient(
            "token",
            api_base="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/prs/owner/repo/42/review-state",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"state": "open"}


def test_pr_review_state_returns_changes_requested_from_pawchestrator_label(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/owner/repo/pulls/42/reviews":
            return httpx.Response(200, json=[{"state": "COMMENTED"}])
        if request.url.path == "/repos/owner/repo/issues/42/labels":
            return httpx.Response(
                200,
                json=[{"name": "pawchestrator:changes-requested"}],
            )
        return httpx.Response(404, json={"message": "not found"})

    _patch_github_client(
        monkeypatch,
        GitHubIssueClient(
            "token",
            api_base="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/prs/owner/repo/42/review-state",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {"state": "changes_requested"}


def test_pr_review_state_requires_pairing_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/prs/owner/repo/42/review-state")

    assert response.status_code == 403


def test_pair_rejects_wrong_origin(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.post("/pair", headers={"Origin": "https://example.com"})

    assert response.status_code == 403


def test_pair_returns_token_after_terminal_approval(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    with TestClient(create_app(settings)) as client:
        response = client.post("/pair", headers={"Origin": "https://github.com"})

    assert response.status_code == 200
    token = response.json()["token"]
    assert len(token) == 64
    assert token in save_and_load_tokens(settings)


def test_pair_allows_missing_origin_after_terminal_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    with TestClient(create_app(settings)) as client:
        response = client.post("/pair")

    assert response.status_code == 200
    token = response.json()["token"]
    assert len(token) == 64
    assert token in save_and_load_tokens(settings)


def test_pair_returns_403_after_terminal_denial(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)

    def deny(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", deny)

    with TestClient(create_app(settings)) as client:
        response = client.post("/pair", headers={"Origin": "https://github.com"})

    assert response.status_code == 403


def _insert_run_state(
    settings: Settings,
    *,
    status: str = "snapshot_complete",
    current_stage: str = "snapshot",
) -> None:
    import asyncio

    async def insert() -> None:
        await init_db(settings)
        async with aiosqlite.connect(settings.database_path) as db:
            await db.execute(
                """
                INSERT INTO workflow_runs (
                  id, owner, repo, issue_number, status, current_stage,
                  created_at, updated_at
                )
                VALUES (
                  'run-123', 'owner', 'repo', 42, ?, ?,
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
                )
                """,
                (status, current_stage),
            )
            await db.execute(
                """
                INSERT INTO workflow_stages (
                  id, run_id, stage_name, status, started_at, completed_at
                )
                VALUES (
                  'stage-123', 'run-123', 'snapshot', 'complete',
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
                )
                """
            )
            await db.execute(
                """
                INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
                VALUES (
                  'artifact-123', 'run-123', 'issue_snapshot',
                  '/tmp/issue.snapshot.json', '2026-05-23T00:00:01Z'
                )
                """
            )
            await db.commit()

    asyncio.run(insert())


def _seed_token(settings: Settings, token: str = "known-token") -> None:
    save_sessions(settings, {"tokens": [token]})


def _write_implementation_plan(
    settings: Settings,
    run_id: str,
    payload: dict[str, object],
) -> None:
    path = settings.app_dir / "runs" / run_id / "implementation_plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class _FakeSubIssueClient:
    def __init__(
        self,
        sub_issues: list[dict[str, object]],
        *,
        settings: Settings | None = None,
    ) -> None:
        self.sub_issues = sub_issues
        self.settings = settings
        self.fetched = False
        self.run_count_at_fetch: int | None = None

    async def fetch_sub_issues(self, _reference):
        self.fetched = True
        if self.settings is not None:
            async with aiosqlite.connect(self.settings.database_path) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM workflow_runs")
                row = await cursor.fetchone()
            self.run_count_at_fetch = int(row[0])
        return self.sub_issues


def _patch_sub_issue_client(monkeypatch, client: _FakeSubIssueClient) -> None:
    monkeypatch.setattr("pawchestrator.server.get_gh_token", lambda: "token")
    monkeypatch.setattr("pawchestrator.server.GitHubIssueClient", lambda _token: client)


def _patch_github_client(monkeypatch, client: GitHubIssueClient) -> None:
    monkeypatch.setattr("pawchestrator.server.get_gh_token", lambda: "token")
    monkeypatch.setattr("pawchestrator.server.GitHubIssueClient", lambda _token: client)


def _insert_repo_registration(settings: Settings, local_path: Path) -> None:
    import asyncio

    from pawchestrator.db import insert_repo_registration

    asyncio.run(
        insert_repo_registration(
            settings,
            owner="owner",
            repo="repo",
            local_path=local_path,
        )
    )


def _insert_failed_epic_resume_state(settings: Settings) -> None:
    import asyncio

    async def insert() -> None:
        await init_db(settings)
        async with aiosqlite.connect(settings.database_path) as db:
            await db.execute(
                """
                INSERT INTO workflow_runs (
                  id, owner, repo, issue_number, group_id, workflow_type, status,
                  current_stage, pr_url, epic_branch_mode, created_at, updated_at
                )
                VALUES (
                  'failed-epic', 'owner', 'repo', 42, 'resume-group', 'epic',
                  'epic_failed', 'epic', 'https://github.com/owner/repo/pull/42',
                  'epic-with-sub-issues', '2026-05-24T10:00:00Z',
                  '2026-05-24T10:00:04Z'
                )
                """
            )
            await db.execute(
                """
                INSERT INTO worktrees (
                  id, run_id, owner, repo, issue_number, branch, path,
                  created_at, updated_at
                )
                VALUES (
                  'resume-worktree', 'failed-epic', 'owner', 'repo', 42,
                  'paw/epic-42-resume', '/tmp/epic-42',
                  '2026-05-24T10:00:00Z', '2026-05-24T10:00:01Z'
                )
                """
            )
            await db.executemany(
                """
                INSERT INTO workflow_runs (
                  id, owner, repo, issue_number, group_id, workflow_type, status,
                  current_stage, pr_url, created_at, updated_at
                )
                VALUES (?, 'owner', 'repo', ?, 'resume-group', 'pipeline', ?, ?,
                        ?, ?, ?)
                """,
                [
                    (
                        "done-43",
                        43,
                        "completed",
                        "pr",
                        "https://github.com/owner/repo/pull/43",
                        "2026-05-24T10:00:01Z",
                        "2026-05-24T10:00:02Z",
                    ),
                    (
                        "failed-44",
                        44,
                        "failed",
                        "verify",
                        None,
                        "2026-05-24T10:00:02Z",
                        "2026-05-24T10:00:03Z",
                    ),
                ],
            )
            await db.commit()

    asyncio.run(insert())


def _token_headers(token: str = "known-token") -> dict[str, str]:
    return {"X-Pawchestrator-Token": token}


def save_and_load_tokens(settings: Settings) -> list[str]:
    import json

    with settings.sessions_path.open("r", encoding="utf-8") as sessions_file:
        return json.load(sessions_file)["tokens"]
