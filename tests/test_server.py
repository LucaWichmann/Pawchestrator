from pathlib import Path

from fastapi.testclient import TestClient

from pawchestrator.config import LOCAL_HOST, Settings
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
