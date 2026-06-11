import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from pawchestrator.config import Settings
from pawchestrator.db import create_pipeline_run
from pawchestrator.server import create_app
from pawchestrator.sessions import save_sessions
from pawchestrator.stream_tokens import validate_stream_token


def test_mint_run_stream_token_returns_scoped_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)
    _insert_run(settings, "run-123")

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/run-123/stream-token", headers=_token_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["expires_in"] == 300
    assert validate_stream_token(payload["token"], "run-123") is True
    assert validate_stream_token(payload["token"], "other-run") is False


def test_mint_run_stream_token_returns_404_for_missing_run(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _seed_token(settings)

    with TestClient(create_app(settings)) as client:
        response = client.post("/runs/missing/stream-token", headers=_token_headers())

    assert response.status_code == 404


def test_mint_run_stream_token_requires_pairing_token(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)
    _insert_run(settings, "run-123")

    with TestClient(create_app(settings)) as client:
        missing_response = client.post("/runs/run-123/stream-token")
        invalid_response = client.post(
            "/runs/run-123/stream-token",
            headers=_token_headers("wrong-token"),
        )

    assert missing_response.status_code == 403
    assert invalid_response.status_code == 403


def _insert_run(settings: Settings, run_id: str) -> None:
    asyncio.run(
        create_pipeline_run(
            settings,
            run_id=run_id,
            owner="owner",
            repo="repo",
            issue_number=42,
        )
    )


def _seed_token(settings: Settings, token: str = "known-token") -> None:
    save_sessions(settings, {"tokens": [token]})


def _token_headers(token: str = "known-token") -> dict[str, str]:
    return {"X-Pawchestrator-Token": token}
