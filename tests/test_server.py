from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import httpx
from fastapi.testclient import TestClient

from pawchestrator.config import LOCAL_HOST, Settings
from pawchestrator.db import init_db
from pawchestrator.github import GitHubIssueClient
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
        calls.append((run_id, runtime_settings.app_dir, implement_runner))

    monkeypatch.setattr("pawchestrator.server.run_review", fake_run_review)

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
    assert calls == [(run_id, tmp_path, None)]
    payload = state_response.json()
    assert payload["id"] == run_id
    assert payload["workflow_type"] == "review"
    assert payload["pr_number"] == 42


def test_openapi_exposes_issue_grill_route(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/openapi.json", headers=_token_headers())

    assert response.status_code == 200
    assert "/issue/grill" in response.json()["paths"]


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
        "/repos/owner/repo/pulls/42/reviews"
    ]


def test_pr_review_state_returns_approved_when_latest_review_approved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    def handler(_request: httpx.Request) -> httpx.Response:
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

    def handler(_request: httpx.Request) -> httpx.Response:
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


def _token_headers(token: str = "known-token") -> dict[str, str]:
    return {"X-Pawchestrator-Token": token}


def save_and_load_tokens(settings: Settings) -> list[str]:
    import json

    with settings.sessions_path.open("r", encoding="utf-8") as sessions_file:
        return json.load(sessions_file)["tokens"]
