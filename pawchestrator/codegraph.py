"""CodeGraph index copy helpers for issue worktrees."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pawchestrator.config import Settings

DEFAULT_BASE_BRANCH = "main"
CODEGRAPH_DB_NAME = "codegraph.db"
CODEGRAPH_SIDE_FILES = ("config.json", ".gitignore")


@dataclass(frozen=True)
class CodeGraphSyncResult:
    action: str
    source: Path
    destination: Path
    message: str


async def seed_worktree_index(
    settings: Settings,
    *,
    source_repo_path: Path,
    worktree_path: Path,
) -> CodeGraphSyncResult:
    """Copy the source repo CodeGraph index into a worktree when useful."""

    if not settings.codegraph.enabled:
        return _result("skipped", source_repo_path, worktree_path, "CodeGraph sync disabled")

    source_dir = _codegraph_dir(settings, source_repo_path)
    destination_dir = _codegraph_dir(settings, worktree_path)
    source_db = source_dir / CODEGRAPH_DB_NAME
    destination_db = destination_dir / CODEGRAPH_DB_NAME

    if not _usable_db(source_db):
        return _result("skipped", source_dir, destination_dir, "source CodeGraph index missing")

    if destination_db.exists() and _mtime_ns(destination_db) >= _mtime_ns(source_db):
        _copy_side_files(source_dir, destination_dir)
        return _result("skipped", source_dir, destination_dir, "worktree CodeGraph index up to date")

    _copy_codegraph_dir(source_dir, destination_dir)
    return _result("copied", source_dir, destination_dir, "seeded worktree CodeGraph index")


async def sync_back_if_merged(
    settings: Settings,
    *,
    source_repo_path: Path,
    worktree_path: Path,
    branch: str,
) -> CodeGraphSyncResult:
    """Copy a worktree CodeGraph index back only after branch HEAD is in main."""

    if not settings.codegraph.enabled:
        return _result("skipped", worktree_path, source_repo_path, "CodeGraph sync disabled")

    worktree_dir = _codegraph_dir(settings, worktree_path)
    source_dir = _codegraph_dir(settings, source_repo_path)
    worktree_db = worktree_dir / CODEGRAPH_DB_NAME

    if not _usable_db(worktree_db):
        return _result("skipped", worktree_dir, source_dir, "worktree CodeGraph index missing")

    if not await _branch_exists(source_repo_path, branch):
        return _result("skipped", worktree_dir, source_dir, f"branch not found: {branch}")

    clean, message = await _source_clean_for_main_refresh(source_repo_path)
    if not clean:
        return _result("skipped", worktree_dir, source_dir, message)

    if not await _branch_is_merged(source_repo_path, branch):
        return _result("skipped", worktree_dir, source_dir, f"branch not merged into {DEFAULT_BASE_BRANCH}")

    _copy_codegraph_dir(worktree_dir, source_dir)
    return _result("copied", worktree_dir, source_dir, "synced merged CodeGraph index back to source")


def _copy_codegraph_dir(source_dir: Path, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    _backup_sqlite_db(source_dir / CODEGRAPH_DB_NAME, destination_dir / CODEGRAPH_DB_NAME)
    _copy_side_files(source_dir, destination_dir)
    _remove_stale_sqlite_sidecars(destination_dir / CODEGRAPH_DB_NAME)


def _backup_sqlite_db(source_db: Path, destination_db: Path) -> None:
    destination_db.parent.mkdir(parents=True, exist_ok=True)
    temp_db = destination_db.with_name(f"{destination_db.name}.tmp")
    if temp_db.exists():
        temp_db.unlink()

    source_uri = source_db.resolve().as_uri()
    source = sqlite3.connect(f"{source_uri}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(temp_db)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    temp_db.replace(destination_db)


def _copy_side_files(source_dir: Path, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    for name in CODEGRAPH_SIDE_FILES:
        source_file = source_dir / name
        if source_file.exists() and source_file.is_file():
            shutil.copy2(source_file, destination_dir / name)


def _remove_stale_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(f"{db_path.name}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _usable_db(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _mtime_ns(path: Path) -> int:
    return path.stat().st_mtime_ns


def _codegraph_dir(settings: Settings, repo_path: Path) -> Path:
    return repo_path / settings.codegraph.directory


def _result(action: str, source: Path, destination: Path, message: str) -> CodeGraphSyncResult:
    return CodeGraphSyncResult(
        action=action,
        source=source,
        destination=destination,
        message=message,
    )


async def _branch_exists(cwd: Path, branch: str) -> bool:
    _stdout, _stderr, exit_code = await _run_git(
        ["rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd,
    )
    return exit_code == 0


async def _branch_is_merged(cwd: Path, branch: str) -> bool:
    _stdout, _stderr, exit_code = await _run_git(
        ["merge-base", "--is-ancestor", branch, DEFAULT_BASE_BRANCH],
        cwd,
    )
    return exit_code == 0


async def _source_clean_for_main_refresh(cwd: Path) -> tuple[bool, str]:
    current_branch, _stderr, exit_code = await _run_git(["branch", "--show-current"], cwd)
    if exit_code != 0:
        return False, "could not read source branch"
    if current_branch.strip() != DEFAULT_BASE_BRANCH:
        return True, "source not on main"

    status, _stderr, exit_code = await _run_git(["status", "--porcelain"], cwd)
    if exit_code != 0:
        return False, "could not read source worktree status"
    if status.strip():
        return False, "source repo main has uncommitted changes; skip CodeGraph sync-back"
    return True, "source repo main clean"


async def _run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return stdout, stderr, proc.returncode or 0
