import asyncio
from pathlib import Path

from pawchestrator.config import Settings
from pawchestrator.db import init_db, list_tables


def test_init_db_creates_mvp0_tables(tmp_path: Path) -> None:
    settings = Settings(app_dir=tmp_path)

    database_path = asyncio.run(init_db(settings))

    assert database_path == tmp_path / "database.sqlite"
    assert asyncio.run(list_tables(database_path)) >= {
        "workflow_runs",
        "workflow_stages",
        "artifacts",
    }
