"""FastAPI application for the local Pawchestrator backend."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pawchestrator import __version__
from pawchestrator.config import LOCAL_HOST, Settings, load_settings
from pawchestrator.db import (
    get_latest_run_by_issue,
    get_run_state,
    init_db,
    is_repo_registered,
    mark_run_failed,
)
from pawchestrator.grill import run_grill
from pawchestrator.pipeline import run_pipeline
from pawchestrator.runners import get_runner_health
from pawchestrator.sessions import (
    _pair_lock,
    generate_token,
    load_sessions,
    save_sessions,
    token_exists,
)


class IssueStartRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


class IssueGrillRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


class PairResponse(BaseModel):
    token: str


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

    @app.middleware("http")
    async def require_pairing_token(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in {"/health", "/pair"}:
            return await call_next(request)

        token = request.headers.get("X-Pawchestrator-Token")
        if not token or not token_exists(runtime_settings, token):
            return JSONResponse({"detail": "invalid pairing token"}, status_code=403)

        return await call_next(request)

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

    @app.post("/pair")
    async def pair(request: Request) -> PairResponse:
        if not _is_pair_origin_allowed(request.headers.get("origin")):
            raise HTTPException(status_code=403, detail="origin not allowed")

        approved = await asyncio.get_running_loop().run_in_executor(
            None,
            _prompt_pairing,
        )
        if not approved:
            raise HTTPException(status_code=403, detail="pairing denied")

        token = generate_token()
        sessions = load_sessions(runtime_settings)
        sessions["tokens"].append(token)
        save_sessions(runtime_settings, sessions)
        return PairResponse(token=token)

    @app.get("/runs/{run_id}")
    async def run_state(run_id: str) -> dict[str, object]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        return state

    @app.get("/issue/{owner}/{repo}/{number}/status")
    async def issue_status(owner: str, repo: str, number: int) -> dict[str, object]:
        repo_registered, runners, pipeline, grill = await asyncio.gather(
            is_repo_registered(runtime_settings, owner=owner, repo=repo),
            get_runner_health(runtime_settings),
            get_latest_run_by_issue(
                runtime_settings,
                owner,
                repo,
                number,
                "pipeline",
            ),
            get_latest_run_by_issue(
                runtime_settings,
                owner,
                repo,
                number,
                "grill",
            ),
        )
        return {
            "backend_connected": True,
            "repo_registered": repo_registered,
            "runners": runners,
            "pipeline": pipeline,
            "grill": grill,
        }

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

    @app.post("/issue/grill")
    async def issue_grill(
        body: IssueGrillRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        url = f"https://github.com/{body.owner}/{body.repo}/issues/{body.number}"
        run_id = await _prepare_grill_run(url, runtime_settings)
        background_tasks.add_task(
            _run_grill_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return {"run_id": run_id}

    return app


def _is_pair_origin_allowed(origin: str | None) -> bool:
    return origin in {None, "https://github.com"}


def _prompt_pairing() -> bool:
    with _pair_lock:
        try:
            input(
                "Pairing request from github.com — "
                "press Enter to approve (Ctrl+C to deny)"
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return True


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
        )
    except Exception as error:
        await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] failed: {error}")


async def _run_grill_background(
    issue_url_value: str,
    settings: Settings,
    *,
    run_id: str,
) -> None:
    try:
        await run_grill(
            issue_url_value,
            settings,
            run_id=run_id,
        )
    except Exception as error:
        await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] grill failed: {error}")


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


async def _prepare_grill_run(issue_url_value: str, settings: Settings) -> str:
    from uuid import uuid4

    from pawchestrator.db import create_grill_run
    from pawchestrator.github import parse_issue_url

    reference = parse_issue_url(issue_url_value)
    run_id = str(uuid4())
    await create_grill_run(
        settings,
        run_id=run_id,
        owner=reference.owner,
        repo=reference.repo,
        issue_number=reference.number,
    )
    return run_id
