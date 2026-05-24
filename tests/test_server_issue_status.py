import json
from pathlib import Path

import aiosqlite
from fastapi.testclient import TestClient

from pawchestrator.config import Settings
from pawchestrator.db import init_db, insert_repo_registration
from pawchestrator.server import create_app
from pawchestrator.sessions import save_sessions


def test_issue_status_returns_null_runs_when_no_run_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "backend_connected": True,
        "repo_registered": False,
        "runners": _healthy_runners(),
        "pipeline": None,
        "grill": None,
    }


def test_issue_status_returns_active_pipeline_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)
    _insert_pipeline_run(settings, status="plan_running", current_stage="plan")

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["pipeline"]["run_id"] == "pipeline-run"
    assert payload["pipeline"]["status"] == "plan_running"
    assert payload["pipeline"]["current_stage"] == "plan"
    assert payload["pipeline"]["workflow_type"] == "pipeline"
    assert payload["pipeline"]["stages"][0]["stage_name"] == "plan"
    assert payload["pipeline"]["warnings"] == []


def test_issue_status_returns_completed_pipeline_with_warnings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)
    _insert_pipeline_run(
        settings,
        status="completed",
        current_stage="pr",
        pr_url="https://github.com/owner/repo/pull/7",
        warning=True,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["pipeline"]["status"] == "completed"
    assert payload["pipeline"]["pr_url"] == "https://github.com/owner/repo/pull/7"
    assert payload["pipeline"]["warnings"] == [
        {
            "id": "warning-1",
            "run_id": "pipeline-run",
            "stage_name": "verify",
            "code": "tests_skipped",
            "message": "Tests were skipped",
            "created_at": "2026-05-24T10:00:03Z",
        }
    ]


def test_issue_status_reports_registered_repo(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)
    asyncio.run(
        insert_repo_registration(
            settings,
            owner="owner",
            repo="repo",
            local_path=tmp_path / "repo",
        )
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json()["repo_registered"] is True


def test_issue_status_returns_latest_grill_run_with_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)
    _insert_grill_run(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["grill"]["run_id"] == "grill-new"
    assert payload["grill"]["workflow_type"] == "grill"
    assert payload["grill"]["grill_report"] == {
        "schema": "pawchestrator.grill_report.v1",
        "status": "success",
        "suggested_criteria": ["Add status endpoint"],
    }
    assert "pr_url" not in payload["grill"]


def test_issue_status_reports_unregistered_repo(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(monkeypatch)

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json()["repo_registered"] is False


def test_issue_status_reports_missing_claude(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(
        monkeypatch,
        {
            "claude": {"available": False, "version": None},
            "codex": {"available": True, "version": "codex 1.2.3"},
        },
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json()["runners"]["claude"] == {
        "available": False,
        "version": None,
    }


def test_issue_status_reports_missing_codex(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _stub_runner_health(
        monkeypatch,
        {
            "claude": {"available": True, "version": "claude 1.2.3"},
            "codex": {"available": False, "version": None},
        },
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/issue/owner/repo/42/status",
            headers=_token_headers(),
        )

    assert response.status_code == 200
    assert response.json()["runners"]["codex"] == {
        "available": False,
        "version": None,
    }


def test_issue_status_requires_pairing_token(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(app_dir=tmp_path)
    _stub_runner_health(monkeypatch)

    with TestClient(create_app(settings)) as client:
        response = client.get("/issue/owner/repo/42/status")

    assert response.status_code == 403


def _insert_pipeline_run(
    settings: Settings,
    *,
    status: str,
    current_stage: str,
    pr_url: str | None = None,
    warning: bool = False,
) -> None:
    import asyncio

    async def insert() -> None:
        await init_db(settings)
        async with aiosqlite.connect(settings.database_path) as db:
            await db.execute(
                """
                INSERT INTO workflow_runs (
                  id, owner, repo, issue_number, workflow_type, status,
                  current_stage, pr_url, created_at, updated_at
                )
                VALUES (
                  'pipeline-run', 'owner', 'repo', 42, 'pipeline', ?, ?, ?,
                  '2026-05-24T10:00:00Z', '2026-05-24T10:00:02Z'
                )
                """,
                (status, current_stage, pr_url),
            )
            await db.execute(
                """
                INSERT INTO workflow_stages (
                  id, run_id, stage_name, status, started_at
                )
                VALUES (
                  'stage-1', 'pipeline-run', ?, 'running',
                  '2026-05-24T10:00:01Z'
                )
                """,
                (current_stage,),
            )
            if warning:
                await db.execute(
                    """
                    INSERT INTO run_warnings (
                      id, run_id, stage_name, code, message, created_at
                    )
                    VALUES (
                      'warning-1', 'pipeline-run', 'verify', 'tests_skipped',
                      'Tests were skipped', '2026-05-24T10:00:03Z'
                    )
                    """
                )
            await db.commit()

    asyncio.run(insert())


def _insert_grill_run(settings: Settings) -> None:
    import asyncio

    async def insert() -> None:
        await init_db(settings)
        report_path = settings.app_dir / "runs" / "grill-new" / "grill_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "schema": "pawchestrator.grill_report.v1",
                    "status": "success",
                    "suggested_criteria": ["Add status endpoint"],
                }
            ),
            encoding="utf-8",
        )
        async with aiosqlite.connect(settings.database_path) as db:
            await db.executemany(
                """
                INSERT INTO workflow_runs (
                  id, owner, repo, issue_number, workflow_type, status,
                  current_stage, created_at, updated_at
                )
                VALUES (?, 'owner', 'repo', 42, 'grill', 'grill_complete',
                        'grill', ?, ?)
                """,
                [
                    (
                        "grill-old",
                        "2026-05-24T09:00:00Z",
                        "2026-05-24T09:00:01Z",
                    ),
                    (
                        "grill-new",
                        "2026-05-24T10:00:00Z",
                        "2026-05-24T10:00:01Z",
                    ),
                ],
            )
            await db.execute(
                """
                INSERT INTO workflow_stages (
                  id, run_id, stage_name, status, completed_at
                )
                VALUES (
                  'grill-stage', 'grill-new', 'grill', 'complete',
                  '2026-05-24T10:00:01Z'
                )
                """
            )
            await db.execute(
                """
                INSERT INTO artifacts (id, run_id, artifact_type, file_path, created_at)
                VALUES (
                  'grill-artifact', 'grill-new', 'grill_report', ?,
                  '2026-05-24T10:00:01Z'
                )
                """,
                (str(report_path),),
            )
            await db.commit()

    asyncio.run(insert())


def _stub_runner_health(
    monkeypatch,
    runners: dict[str, dict[str, object]] | None = None,
) -> None:
    async def fake_runner_health(settings: Settings) -> dict[str, dict[str, object]]:
        return runners or _healthy_runners()

    monkeypatch.setattr("pawchestrator.server.get_runner_health", fake_runner_health)


def _healthy_runners() -> dict[str, dict[str, object]]:
    return {
        "claude": {"available": True, "version": "claude 1.2.3"},
        "codex": {"available": True, "version": "codex 1.2.3"},
    }


def _seed_token(settings: Settings, token: str = "known-token") -> None:
    save_sessions(settings, {"tokens": [token]})


def _token_headers(token: str = "known-token") -> dict[str, str]:
    return {"X-Pawchestrator-Token": token}
