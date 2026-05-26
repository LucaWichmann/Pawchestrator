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
    create_epic_run,
    fail_stale_runs_on_startup,
    get_latest_epic_run_by_issue,
    get_latest_grill_run_by_issue,
    get_latest_run_by_issue,
    get_run_state,
    init_db,
    is_repo_registered,
    lookup_repo_path,
    mark_run_failed,
)
from pawchestrator.epic import run_epic
from pawchestrator.github import GitHubIssueClient, get_gh_token, parse_issue_url
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


class PipelineStartResponse(BaseModel):
    type: str = "pipeline"
    run_id: str


class EpicSubRunResponse(BaseModel):
    issue_number: int
    title: str = ""
    run_id: str = ""


class EpicStartResponse(BaseModel):
    type: str = "epic"
    run_id: str
    group_id: str
    sub_runs: list[EpicSubRunResponse]


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_path = await init_db(runtime_settings)
        await fail_stale_runs_on_startup(runtime_settings)
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
        repo_registered, runners, pipeline, grill, epic = await asyncio.gather(
            is_repo_registered(runtime_settings, owner=owner, repo=repo),
            get_runner_health(runtime_settings),
            get_latest_run_by_issue(
                runtime_settings,
                owner,
                repo,
                number,
                "pipeline",
            ),
            get_latest_grill_run_by_issue(runtime_settings, owner, repo, number),
            get_latest_epic_run_by_issue(runtime_settings, owner, repo, number),
        )
        return {
            "backend_connected": True,
            "repo_registered": repo_registered,
            "runners": runners,
            "pipeline": None if epic is not None else pipeline,
            "grill": grill,
            "epic": epic,
            "epic_confirm": runtime_settings.pipeline.epic_confirm,
        }

    @app.get("/prs/{owner}/{repo}/{number}/review-state")
    async def pr_review_state(owner: str, repo: str, number: int) -> dict[str, str]:
        client = GitHubIssueClient(get_gh_token())
        state = await client.fetch_pr_review_state(owner, repo, number)
        return {"state": state}

    @app.post("/issue/start")
    async def issue_start(
        body: IssueStartRequest,
        background_tasks: BackgroundTasks,
    ) -> PipelineStartResponse | EpicStartResponse:
        url = f"https://github.com/{body.owner}/{body.repo}/issues/{body.number}"
        reference = parse_issue_url(url)
        client = GitHubIssueClient(get_gh_token())
        sub_issues = await client.fetch_sub_issues(reference)
        if sub_issues:
            from uuid import uuid4

            repo_path = await lookup_repo_path(
                runtime_settings,
                owner=body.owner,
                repo=body.repo,
            )
            if repo_path is None:
                raise HTTPException(
                    status_code=400,
                    detail="repo not registered - run `pawchestrator repo add <path>` first",
                )

            group_id = str(uuid4())
            parent_run_id = str(uuid4())
            await create_epic_run(
                runtime_settings,
                run_id=parent_run_id,
                owner=body.owner,
                repo=body.repo,
                issue_number=body.number,
                group_id=group_id,
            )
            background_tasks.add_task(
                _run_epic_background,
                url,
                runtime_settings,
                repo_path=repo_path.resolve(),
                group_id=group_id,
                parent_run_id=parent_run_id,
            )
            return EpicStartResponse(
                run_id=parent_run_id,
                group_id=group_id,
                sub_runs=[
                    EpicSubRunResponse(
                        issue_number=int(sub_issue["number"]),
                        title=str(sub_issue.get("title") or ""),
                    )
                    for sub_issue in sub_issues
                ],
            )

        run_id = await _prepare_pipeline_run(url, runtime_settings)
        background_tasks.add_task(
            _run_pipeline_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return PipelineStartResponse(run_id=run_id)

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


async def _run_epic_background(
    issue_url_value: str,
    settings: Settings,
    *,
    repo_path,
    group_id: str,
    parent_run_id: str,
) -> None:
    try:
        await run_epic(
            issue_url_value,
            settings,
            repo_path=repo_path,
            group_id=group_id,
            parent_run_id=parent_run_id,
        )
    except Exception as error:
        print(f"[epic {parent_run_id}] failed: {error}")


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
