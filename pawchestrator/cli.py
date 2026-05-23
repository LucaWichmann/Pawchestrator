"""Pawchestrator command-line entry point."""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from pawchestrator.config import DEFAULT_PORT, LOCAL_HOST, load_settings
from pawchestrator.doctor import STATUS_FAIL, STATUS_PASS, STATUS_WARN, has_required_failures, run_checks

app = typer.Typer(add_completion=False, help="Local Pawchestrator backend tools.")


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
