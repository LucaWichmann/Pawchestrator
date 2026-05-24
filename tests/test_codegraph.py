import asyncio
import sqlite3
from pathlib import Path

from pawchestrator.codegraph import seed_worktree_index, sync_back_if_merged
from pawchestrator.config import Settings


def test_seed_worktree_index_copies_db_side_files_and_skips_wal_shm(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    _write_codegraph_index(source, "source")
    codegraph_dir = source / ".codegraph"
    (codegraph_dir / "codegraph.db-wal").write_text("stale wal", encoding="utf-8")
    (codegraph_dir / "codegraph.db-shm").write_text("stale shm", encoding="utf-8")

    result = asyncio.run(
        seed_worktree_index(
            Settings(app_dir=tmp_path),
            source_repo_path=source,
            worktree_path=worktree,
        )
    )

    assert result.action == "copied"
    assert _read_marker(worktree) == "source"
    assert (worktree / ".codegraph" / "config.json").read_text(encoding="utf-8") == "{}\n"
    assert (worktree / ".codegraph" / ".gitignore").read_text(encoding="utf-8") == "*.db\n"
    assert not (worktree / ".codegraph" / "codegraph.db-wal").exists()
    assert not (worktree / ".codegraph" / "codegraph.db-shm").exists()


def test_seed_worktree_index_noops_when_source_index_missing(tmp_path: Path) -> None:
    result = asyncio.run(
        seed_worktree_index(
            Settings(app_dir=tmp_path),
            source_repo_path=tmp_path / "source",
            worktree_path=tmp_path / "worktree",
        )
    )

    assert result.action == "skipped"
    assert not (tmp_path / "worktree" / ".codegraph").exists()


def test_sync_back_if_merged_skips_unmerged_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    _write_codegraph_index(worktree, "worktree")

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        if args[:2] == ["rev-parse", "--verify"]:
            return "", "", 0
        if args == ["branch", "--show-current"]:
            return "main\n", "", 0
        if args == ["status", "--porcelain"]:
            return "", "", 0
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return "", "", 1
        raise AssertionError(args)

    monkeypatch.setattr("pawchestrator.codegraph._run_git", fake_run_git)

    result = asyncio.run(
        sync_back_if_merged(
            Settings(app_dir=tmp_path),
            source_repo_path=source,
            worktree_path=worktree,
            branch="paw/issue-42-test",
        )
    )

    assert result.action == "skipped"
    assert not (source / ".codegraph" / "codegraph.db").exists()


def test_sync_back_if_merged_copies_when_branch_is_in_main(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    _write_codegraph_index(worktree, "worktree")

    async def fake_run_git(args: list[str], cwd: Path) -> tuple[str, str, int]:
        if args[:2] == ["rev-parse", "--verify"]:
            return "", "", 0
        if args == ["branch", "--show-current"]:
            return "main\n", "", 0
        if args == ["status", "--porcelain"]:
            return "", "", 0
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return "", "", 0
        raise AssertionError(args)

    monkeypatch.setattr("pawchestrator.codegraph._run_git", fake_run_git)

    result = asyncio.run(
        sync_back_if_merged(
            Settings(app_dir=tmp_path),
            source_repo_path=source,
            worktree_path=worktree,
            branch="paw/issue-42-test",
        )
    )

    assert result.action == "copied"
    assert _read_marker(source) == "worktree"


def _write_codegraph_index(repo_path: Path, marker: str) -> None:
    codegraph_dir = repo_path / ".codegraph"
    codegraph_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(codegraph_dir / "codegraph.db") as db:
        db.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        db.execute("INSERT INTO marker VALUES (?)", (marker,))
    (codegraph_dir / "config.json").write_text("{}\n", encoding="utf-8")
    (codegraph_dir / ".gitignore").write_text("*.db\n", encoding="utf-8")


def _read_marker(repo_path: Path) -> str:
    with sqlite3.connect(repo_path / ".codegraph" / "codegraph.db") as db:
        row = db.execute("SELECT value FROM marker").fetchone()
    return str(row[0])
