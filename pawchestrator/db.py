"""SQLite initialization for Pawchestrator."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from pawchestrator.config import Settings, ensure_app_dir

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  repo TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  current_stage TEXT,
  pr_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_stages (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  stage_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id),
  artifact_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


async def init_db(settings: Settings) -> Path:
    """Create app directory and initialize the MVP 0 SQLite schema."""

    ensure_app_dir(settings)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    return settings.database_path


async def list_tables(database_path: Path) -> set[str]:
    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        rows = await cursor.fetchall()
    return {row[0] for row in rows}
