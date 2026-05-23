"""FastAPI application for the local Pawchestrator backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from pawchestrator import __version__
from pawchestrator.config import LOCAL_HOST, Settings, load_settings
from pawchestrator.db import get_run_state, init_db, mark_run_failed
from pawchestrator.pipeline import run_pipeline


class IssueStartRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_path = await init_db(runtime_settings)
        app.state.settings = runtime_settings
        app.state.database_path = database_path
        yield

    app = FastAPI(title="Pawchestrator", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://github.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "name": "pawchestrator",
            "version": __version__,
            "status": "ok",
            "database": {
                "status": "ok",
                "path": str(runtime_settings.database_path),
            },
            "bind": {
                "host": LOCAL_HOST,
                "localhost_only": True,
            },
        }

    @app.get("/runs/{run_id}")
    async def run_state(run_id: str) -> dict[str, object]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        return state

    @app.post("/issue/start")
    async def issue_start(
        body: IssueStartRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        url = f"https://github.com/{body.owner}/{body.repo}/issues/{body.number}"
        run_id = await _prepare_pipeline_run(url, runtime_settings)
        background_tasks.add_task(
            _run_pipeline_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return {"run_id": run_id}

    return app


async def _run_pipeline_background(
    issue_url_value: str,
    settings: Settings,
    *,
    run_id: str,
) -> None:
    try:
        await run_pipeline(
            issue_url_value,
            settings,
            run_id=run_id,
            allow_empty_commit=True,
        )
    except Exception as error:
        await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] failed: {error}")


async def _prepare_pipeline_run(issue_url_value: str, settings: Settings) -> str:
    from uuid import uuid4

    from pawchestrator.db import create_pipeline_run
    from pawchestrator.github import parse_issue_url

    reference = parse_issue_url(issue_url_value)
    run_id = str(uuid4())
    await create_pipeline_run(
        settings,
        run_id=run_id,
        owner=reference.owner,
        repo=reference.repo,
        issue_number=reference.number,
    )
    return run_id
