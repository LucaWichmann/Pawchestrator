"""Pawchestrator command-line entry point."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
import uvicorn

from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST, load_settings
from pawchestrator.doctor import STATUS_FAIL, STATUS_PASS, STATUS_WARN, has_required_failures, run_checks
from pawchestrator.issues import snapshot_issue
from pawchestrator.plan import run_plan
from pawchestrator.scout import run_scout

app = typer.Typer(add_completion=False, help="Local Pawchestrator backend tools.")
issue_app = typer.Typer(add_completion=False, help="GitHub issue tools.")
run_app = typer.Typer(add_completion=False, help="Workflow run tools.")
app.add_typer(issue_app, name="issue")
app.add_typer(run_app, name="run")


@app.command()
def serve(
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Local backend port."),
    ] = DEFAULT_PORT,
) -> None:
    """Start the local FastAPI backend."""

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
