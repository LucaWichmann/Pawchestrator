from pathlib import Path

import aiosqlite
from fastapi.testclient import TestClient

from pawchestrator.config import LOCAL_HOST, Settings
from pawchestrator.db import init_db
from pawchestrator.server import create_app


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

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-123")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "run-123"
    assert payload["status"] == "snapshot_complete"
    assert payload["stages"][0]["stage_name"] == "snapshot"
    assert payload["artifacts"][0]["artifact_type"] == "issue_snapshot"


def test_run_state_returns_404_for_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/missing")

    assert response.status_code == 404


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
