"""Pawchestrator command-line entry point."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
import uvicorn

from pawchestrator.checkbox import check_checkbox
from pawchestrator.codegraph import sync_back_if_merged
from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST, load_settings
from pawchestrator.db import (
    create_epic_run,
    get_run_state,
    get_worktree_record,
    insert_repo_registration,
    list_repo_registrations,
    lookup_repo_path,
)
from pawchestrator.doctor import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    has_required_failures,
    run_checks,
)
from pawchestrator.grill import run_grill
from pawchestrator.github import (
    GitHubIssueClient,
    get_gh_token,
    parse_issue_url,
    parse_issue_shorthand,
)
from pawchestrator.epic import run_epic
from pawchestrator.implement import run_implement
from pawchestrator.issues import snapshot_issue
from pawchestrator.pipeline import run_pipeline
from pawchestrator.plan import run_plan
from pawchestrator.pr import run_pr
from pawchestrator.scout import run_scout
from pawchestrator.verify import run_verify

app = typer.Typer(add_completion=False, help="Local Pawchestrator backend tools.")
issue_app = typer.Typer(add_completion=False, help="GitHub issue tools.")
checkbox_app = typer.Typer(add_completion=False, help="GitHub issue checkbox tools.")
run_app = typer.Typer(add_completion=False, help="Workflow run tools.")
repo_app = typer.Typer(add_completion=False, help="Registered source repository tools.")
sessions_app = typer.Typer(add_completion=False, help="Pairing session tools.")
codegraph_app = typer.Typer(add_completion=False, help="CodeGraph index sync tools.")
app.add_typer(issue_app, name="issue")
app.add_typer(checkbox_app, name="checkbox")
app.add_typer(run_app, name="run")
app.add_typer(repo_app, name="repo")
app.add_typer(sessions_app, name="sessions")
app.add_typer(codegraph_app, name="codegraph")

GITHUB_REMOTE_RE = re.compile(
    r"(?:https://(?:[^@\s/]+@)?github\.com/|git@github\.com:)"
    r"(?P<owner>[^/\s:]+)/(?P<repo>[^/\s]+?)(?:\.git)?$"
)


@app.command()
def serve(
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Local backend port."),
    ] = DEFAULT_PORT,
) -> None:
    """Start the local FastAPI backend."""

    if not _port_available(port):
        typer.secho(
            (
                f"{LOCAL_HOST}:{port} is already in use. "
                "Stop the existing Pawchestrator serve process before restarting "
                "to ensure the live backend uses the current code."
            ),
            fg=typer.colors.YELLOW,
            err=True,
        )

    uvicorn.run(
        "pawchestrator.server:create_app",
        factory=True,
        host=LOCAL_HOST,
        port=port,
    )


@app.command()
def doctor(
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Backend port to check."),
    ] = DEFAULT_PORT,
) -> None:
    """Check required and optional local dependencies."""

    settings = load_settings()
    results = run_checks(settings, port=port)

    typer.echo("Pawchestrator Doctor")
    typer.echo("")
    for result in results:
        _print_result(result.label, result.status, result.message)

    if has_required_failures(results):
        raise typer.Exit(code=1)


@issue_app.command("snapshot")
def issue_snapshot(github_issue_url: str) -> None:
    """Fetch a GitHub issue and write an IssueSnapshot artifact."""

    settings = load_settings()
    try:
        result = asyncio.run(snapshot_issue(github_issue_url, settings))
    except Exception as error:
        typer.secho(f"Snapshot failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Snapshot: {result.artifact_path}")
    typer.echo(f"Issue: #{result.issue_number} - {result.title}")


@issue_app.command("start")
def issue_start(
    github_issue_url: str,
    repo_path: Annotated[
        Path | None,
        typer.Option(
            "--repo-path",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Source repository path for git worktree creation.",
        ),
    ] = None,
) -> None:
    """Run the full issue-to-PR pipeline for a GitHub issue."""

    settings = load_settings()
    try:
        result = asyncio.run(_start_issue_from_cli(github_issue_url, settings, repo_path))
    except Exception as error:
        typer.secho(f"Pipeline failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if getattr(result, "group_id", None):
        typer.echo(f"Epic group ID: {result.group_id}")
        for sub_run in result.sub_runs:
            typer.echo(f"Sub-issue #{sub_run.issue_number}: {sub_run.run_id}")
        return

    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Draft PR: {result.pr_url}")


@issue_app.command("grill")
def issue_grill(github_issue_url: str) -> None:
    """Run the Grill action for a GitHub issue."""

    settings = load_settings()
    try:
        result = asyncio.run(run_grill(github_issue_url, settings))
    except Exception as error:
        typer.secho(f"Grill failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.report.status}")
    typer.echo(f"Suggested criteria: {len(result.report.suggested_criteria)}")
    typer.echo(f"Unanswerable questions: {len(result.report.unanswerable_questions)}")
    typer.echo(f"Body updated: {result.report.body_updated}")
    typer.echo(f"Comment posted: {result.report.comment_posted}")
    typer.echo(f"Report: {result.artifact_path}")


async def _start_issue_from_cli(
    github_issue_url: str,
    settings,
    repo_path: Path | None,
):
    reference = parse_issue_url(github_issue_url)
    client = GitHubIssueClient(get_gh_token())
    sub_issues = await client.fetch_sub_issues(reference)
    if not sub_issues:
        return await run_pipeline(github_issue_url, settings, repo_path=repo_path)

    resolved_repo_path = repo_path
    if resolved_repo_path is None:
        resolved_repo_path = await lookup_repo_path(
            settings,
            owner=reference.owner,
            repo=reference.repo,
        )
    if resolved_repo_path is None:
        raise ValueError("Repo not registered - run `pawchestrator repo add <path>` first")

    group_id = str(uuid4())
    parent_run_id = str(uuid4())
    await create_epic_run(
        settings,
        run_id=parent_run_id,
        owner=reference.owner,
        repo=reference.repo,
        issue_number=reference.number,
        group_id=group_id,
    )
    return await run_epic(
        github_issue_url,
        settings,
        repo_path=resolved_repo_path.resolve(),
        group_id=group_id,
        parent_run_id=parent_run_id,
    )


@checkbox_app.command("check")
def checkbox_check(
    issue_ref: str,
    index: int,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Workflow run ID for durable run-scoped checkbox marks.",
        ),
    ] = None,
) -> None:
    """Check one in-scope checkbox in a GitHub issue body."""

    settings = load_settings()
    try:
        reference = parse_issue_shorthand(issue_ref)
        token = get_gh_token()
        client = GitHubIssueClient(token)
        changed = asyncio.run(
            check_checkbox(
                client,
                reference,
                index,
                settings.checkboxes.headings,
                run_id=run_id,
                db_path=settings.database_path,
            )
        )
    except Exception as error:
        typer.secho(f"Checkbox check failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    status = "checked" if changed else "already checked"
    typer.echo(f"Checkbox {index} {status}: {issue_ref}")


@repo_app.command("add")
def repo_add(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Local git repository path to register.",
        ),
    ],
) -> None:
    """Register a local GitHub repository clone."""

    try:
        owner, repo = _github_remote_owner_repo(path)
    except ValueError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    settings = load_settings()
    asyncio.run(
        insert_repo_registration(
            settings,
            owner=owner,
            repo=repo,
            local_path=path,
        )
    )
    typer.echo(f"Registered {owner}/{repo} -> {path}")


@repo_app.command("list")
def repo_list() -> None:
    """List registered local GitHub repository clones."""

    settings = load_settings()
    registrations = asyncio.run(list_repo_registrations(settings))
    for registration in registrations:
        typer.echo(
            f"{registration['owner']}/{registration['repo']} -> {registration['local_path']}"
        )


@sessions_app.command("clear")
def sessions_clear() -> None:
    """Revoke all browser pairing sessions."""

    settings = load_settings()
    if settings.sessions_path.exists():
        settings.sessions_path.unlink()
        typer.echo(f"Cleared pairing sessions: {settings.sessions_path}")
        return

    typer.echo("No pairing sessions to clear.")


@codegraph_app.command("sync")
def codegraph_sync_command(
    run_id: str,
    repo_path: Annotated[
        Path | None,
        typer.Option(
            "--repo-path",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Source repository path for merged CodeGraph sync-back.",
        ),
    ] = None,
) -> None:
    """Sync a merged run worktree CodeGraph index back to the source repo."""

    settings = load_settings()
    try:
        result = asyncio.run(_sync_codegraph_run(run_id, settings, repo_path=repo_path))
    except Exception as error:
        typer.secho(f"CodeGraph sync failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"{result.action}: {result.message}")
    typer.echo(f"Source: {result.source}")
    typer.echo(f"Destination: {result.destination}")


@run_app.command("scout")
def run_scout_command(run_id: str) -> None:
    """Run the RepoScout stage for an existing issue snapshot run."""

    settings = load_settings()
    try:
        result = asyncio.run(run_scout(run_id, settings))
    except Exception as error:
        typer.secho(f"Scout failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(json.dumps(result.report, indent=2, sort_keys=True))


@run_app.command("plan")
def run_plan_command(run_id: str) -> None:
    """Run the ImplementationPlan stage for an existing scout run."""

    settings = load_settings()
    try:
        result = asyncio.run(run_plan(run_id, settings))
    except Exception as error:
        typer.secho(f"Plan failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(result.plan["approach_summary"])
    for step in result.plan["steps"]:
        typer.echo(f"{step['order']}. {step['description']}")


@run_app.command("implement")
def run_implement_command(
    run_id: str,
    repo_path: Annotated[
        Path | None,
        typer.Option(
            "--repo-path",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Source repository path for git worktree creation.",
        ),
    ] = None,
) -> None:
    """Run the Implement stage for an existing implementation plan run."""

    settings = load_settings()
    try:
        result = asyncio.run(run_implement(run_id, settings, repo_path=repo_path))
    except Exception as error:
        typer.secho(f"Implement failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    files_changed = result.report["files_changed"]
    typer.echo(f"Worktree: {result.worktree_path}")
    typer.echo(f"Branch: {result.branch}")
    typer.echo(f"Changed files: {len(files_changed)}")
    for file_path in files_changed:
        typer.echo(f"- {file_path}")
    typer.echo(f"Report: {result.artifact_path}")


@run_app.command("verify")
def run_verify_command(run_id: str) -> None:
    """Run the Verify stage for an existing implement run."""

    settings = load_settings()
    try:
        result = asyncio.run(run_verify(run_id, settings))
    except Exception as error:
        typer.secho(f"Verify failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    for command in result.report["commands"]:
        name = command.get("name", command["command"])
        exit_code = command["exit_code"]
        marker = "PASS" if exit_code == 0 else "FAIL"
        typer.echo(f"[verify] {name}... exit {exit_code} {marker}")
    typer.echo(f"[verify] {result.report['status'].upper()}")


@run_app.command("pr")
def run_pr_command(run_id: str) -> None:
    """Create a draft GitHub pull request for a verified run."""

    settings = load_settings()
    try:
        result = asyncio.run(run_pr(run_id, settings))
    except Exception as error:
        typer.secho(f"PR failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(result.pr_url)


async def _sync_codegraph_run(run_id: str, settings, *, repo_path: Path | None = None):
    run_state = await get_run_state(settings, run_id)
    if run_state is None:
        raise ValueError(f"run not found: {run_id}")

    worktree = await get_worktree_record(settings, run_id=run_id)
    if worktree is None:
        raise RuntimeError(f"worktree record not found for run: {run_id}")

    source_repo_path = repo_path.resolve() if repo_path is not None else None
    if source_repo_path is None:
        source_repo_path = await lookup_repo_path(
            settings,
            owner=str(run_state["owner"]),
            repo=str(run_state["repo"]),
        )
        if source_repo_path is None:
            raise ValueError("Repo not registered - run `pawchestrator repo add <path>` first")
        source_repo_path = source_repo_path.resolve()

    return await sync_back_if_merged(
        settings,
        source_repo_path=source_repo_path,
        worktree_path=Path(str(worktree["path"])),
        branch=str(worktree["branch"]),
    )


def _print_result(label: str, status: str, message: str) -> None:
    colors = {
        STATUS_PASS: typer.colors.GREEN,
        STATUS_WARN: typer.colors.YELLOW,
        STATUS_FAIL: typer.colors.RED,
    }
    markers = {
        STATUS_PASS: "PASS",
        STATUS_WARN: "WARN",
        STATUS_FAIL: "FAIL",
    }
    marker = markers.get(status, status.upper())
    color = colors.get(status)
    typer.secho(f"{marker:<4} {label:<14} {message}", fg=color)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((LOCAL_HOST, port))
        except OSError:
            return False
    return True


def _github_remote_owner_repo(path: Path) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "remote", "-v"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Timed out while reading git remotes for {path}") from error

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        detail = f": {message.splitlines()[0]}" if message else ""
        raise ValueError(f"{path} is not a git repository{detail}")

    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        match = GITHUB_REMOTE_RE.match(parts[1])
        if match:
            return match.group("owner"), match.group("repo")

    raise ValueError(f"{path} has no github.com remote")
