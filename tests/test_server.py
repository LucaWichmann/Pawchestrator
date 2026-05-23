from pathlib import Path

import aiosqlite
from fastapi.testclient import TestClient

from pawchestrator.config import LOCAL_HOST, Settings
from pawchestrator.db import init_db
from pawchestrator.server import create_app
from pawchestrator.sessions import save_sessions


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


def test_run_state_returns_run_stages_and_artifacts(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run_state(settings)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
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


def test_issue_start_returns_run_id_and_schedules_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    calls = []

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
    assert response.json() == {"run_id": run_id}
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


def test_issue_start_background_failure_does_not_raise(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

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

    assert issue_response.status_code == 200
    assert runs_response.status_code == 200
    assert issue_response.headers["access-control-allow-origin"] == "https://github.com"
    assert runs_response.headers["access-control-allow-origin"] == "https://github.com"


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


def test_pair_returns_403_after_terminal_denial(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)

    def deny(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", deny)

    with TestClient(create_app(settings)) as client:
        response = client.post("/pair", headers={"Origin": "https://github.com"})

    assert response.status_code == 403


def _insert_run_state(settings: Settings) -> None:
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
                  'run-123', 'owner', 'repo', 42, 'snapshot_complete', 'snapshot',
                  '2026-05-23T00:00:00Z', '2026-05-23T00:00:01Z'
                )
                """
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


def _token_headers(token: str = "known-token") -> dict[str, str]:
    return {"X-Pawchestrator-Token": token}


def save_and_load_tokens(settings: Settings) -> list[str]:
    import json

    with settings.sessions_path.open("r", encoding="utf-8") as sessions_file:
        return json.load(sessions_file)["tokens"]
