"""FastAPI application for the local Pawchestrator backend."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from pawchestrator import __version__
from pawchestrator.approval_gate import signal_approval, signal_approval_decision
from pawchestrator.config import LOCAL_HOST, Settings, load_settings
from pawchestrator.db import (
    create_epic_run,
    create_repair_run,
    create_review_run,
    get_latest_epic_architect_run_by_issue,
    get_latest_epic_run_by_issue,
    get_latest_failed_epic_run_by_issue,
    get_latest_pipeline_runs_by_group_issue,
    get_latest_grill_run_by_issue,
    get_latest_run_by_issue,
    get_run_by_pr_number,
    get_run_state,
    init_db,
    is_repo_registered,
    lookup_repo_path,
    mark_run_completed,
    mark_run_failed,
)
from pawchestrator.epic import run_epic
from pawchestrator.epic_architect import run_epic_architect
from pawchestrator.epic_scout import run_epic_scout
from pawchestrator.github import (
    GitHubIssueClient,
    get_gh_token,
    parse_issue_url,
)
from pawchestrator.grill import run_grill
from pawchestrator.implement import run_repair
from pawchestrator.lifecycle import fail_stale_runs_on_startup, resume_pending_approvals
from pawchestrator.pipeline import run_pipeline
from pawchestrator.plan import append_plan_rejection
from pawchestrator.review import run_review
from pawchestrator.review import review_report_path
from pawchestrator.review_issues import format_and_create_issues
from pawchestrator.review_post import run_review_post
from pawchestrator.run_events import (
    _STREAM_SENTINEL,
    _run_stream_queues,
    close_run_stream,
    get_or_create_run_queue,
)
from pawchestrator.run_clean import auto_clean_runs
from pawchestrator.runners import get_runner_health
from pawchestrator.sessions import (
    _pair_lock,
    generate_token,
    load_sessions,
    save_sessions,
    token_exists,
)
from pawchestrator.stage_lifecycle import (
    StageFailedWithArtifact,
    StageSkipped,
    run_stage_lifecycle,
)
from pawchestrator.stream_tokens import STREAM_TOKEN_TTL, mint_stream_token


class IssueStartRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


class IssueGrillRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


class IssueEpicArchitectRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    number: int = Field(gt=0)


class ReviewStartRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_number: int = Field(gt=0)


class RepairStartRequest(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_number: int = Field(gt=0)


class PairResponse(BaseModel):
    token: str


class PlanRejectRequest(BaseModel):
    feedback: str = Field(min_length=1)


class PipelineStartResponse(BaseModel):
    type: str = "pipeline"
    run_id: str


class ReviewStartResponse(BaseModel):
    run_id: str


class RepairStartResponse(BaseModel):
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
    resumed: bool = False
    status: str | None = None
    mode: str | None = None
    branch: str | None = None
    pr_url: str | None = None


_active_run_tasks: dict[str, asyncio.Task[None]] = {}


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings()
    _debug_print_settings(runtime_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_path = await init_db(runtime_settings)
        await fail_stale_runs_on_startup(runtime_settings)
        try:
            await resume_pending_approvals(
                runtime_settings,
                GitHubIssueClient(get_gh_token()),
            )
        except Exception:
            pass
        await auto_clean_runs(runtime_settings)
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

    @app.get("/config")
    async def config() -> dict[str, object]:
        return {
            "pipeline": {
                "verify_repair_attempts": (
                    runtime_settings.pipeline.verify_repair_attempts
                ),
                "plan_approval_max_attempts": (
                    runtime_settings.pipeline.plan_approval_max_attempts
                ),
                "auto_clean": runtime_settings.pipeline.auto_clean,
                "smart_routing": runtime_settings.pipeline.smart_routing.model_dump(
                    mode="json"
                ),
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

    @app.get("/runs/{run_id}/status")
    async def run_status(run_id: str) -> dict[str, object]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        return state

    @app.get("/runs/{run_id}/stream")
    async def stream_run(run_id: str) -> StreamingResponse:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")

        queue = get_or_create_run_queue(run_id)

        async def event_generator() -> AsyncIterator[str]:
            try:
                while True:
                    event = await queue.get()
                    if event is _STREAM_SENTINEL:
                        break
                    if not isinstance(event, dict):
                        continue
                    yield (
                        f"event: {event['type']}\n"
                        f"data: {json.dumps(event['data'])}\n\n"
                    )
            finally:
                if _run_stream_queues.get(run_id) is queue:
                    _run_stream_queues.pop(run_id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    @app.get("/runs/{run_id}/plan")
    async def run_plan(run_id: str) -> dict[str, object]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")

        plan_path = (
            runtime_settings.app_dir / "runs" / run_id / "implementation_plan.json"
        )
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="implementation plan not found",
            ) from error

        file_operations = plan.get("file_operations") or [
            {"path": path, "type": "modify", "description": ""}
            for path in plan.get("files_to_modify", [])
        ]
        return {
            "approach_summary": plan["approach_summary"],
            "estimated_risk": plan.get("estimated_risk", "medium"),
            "file_operations": file_operations,
            "steps": plan.get("steps", []),
        }

    @app.post("/runs/{run_id}/approve")
    async def approve_run(run_id: str) -> dict[str, str]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        if state.get("status") != "awaiting_plan_approval":
            raise HTTPException(status_code=409, detail="run is not awaiting plan approval")
        if not signal_approval(run_id, approved=True):
            raise HTTPException(status_code=409, detail="approval gate is not active")
        return {"run_id": run_id, "decision": "approve"}

    @app.post("/runs/{run_id}/approve-epic")
    async def approve_epic_run(run_id: str) -> dict[str, str]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        if state.get("status") != "awaiting_epic_approval":
            raise HTTPException(status_code=409, detail="run is not awaiting epic approval")
        if not signal_approval_decision(run_id, "approve"):
            raise HTTPException(status_code=409, detail="approval gate is not active")
        return {"run_id": run_id, "decision": "approve"}

    @app.post("/runs/{run_id}/abort")
    async def abort_run(run_id: str) -> dict[str, str]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        task = _active_run_tasks.get(run_id)
        if task is None or task.done():
            raise HTTPException(status_code=404, detail="run not active")

        if state.get("status") == "awaiting_epic_approval":
            signal_approval_decision(run_id, "abort")
            return {
                "run_id": run_id,
                "status": "epic_architect_failed",
                "error": "aborted by user",
            }

        signal_approval(run_id, approved=False)
        task.cancel()
        await mark_run_failed(
            runtime_settings,
            run_id=run_id,
            error="aborted by user",
            current_stage=str(state.get("current_stage") or "plan"),
        )
        close_run_stream(run_id)
        return {"run_id": run_id, "status": "failed", "error": "aborted by user"}

    @app.post("/runs/{run_id}/stream-token")
    async def mint_run_stream_token(run_id: str) -> dict[str, object]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        token = mint_stream_token(run_id)
        return {"token": token, "expires_in": STREAM_TOKEN_TTL}

    @app.post("/runs/{run_id}/reject")
    async def reject_run(run_id: str, body: PlanRejectRequest) -> dict[str, str]:
        state = await get_run_state(runtime_settings, run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        if state.get("status") != "awaiting_plan_approval":
            raise HTTPException(status_code=409, detail="run is not awaiting plan approval")
        append_plan_rejection(runtime_settings, run_id, body.feedback)
        if not signal_approval_decision(run_id, "reject"):
            raise HTTPException(status_code=409, detail="approval gate is not active")
        return {"run_id": run_id, "decision": "reject"}

    @app.get("/issue/{owner}/{repo}/{number}/status")
    async def issue_status(owner: str, repo: str, number: int) -> dict[str, object]:
        (
            repo_registered,
            runners,
            pipeline,
            grill,
            epic_architect,
            epic,
        ) = await asyncio.gather(
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
            get_latest_epic_architect_run_by_issue(
                runtime_settings,
                owner,
                repo,
                number,
            ),
            get_latest_epic_run_by_issue(runtime_settings, owner, repo, number),
        )
        return {
            "backend_connected": True,
            "repo_registered": repo_registered,
            "runners": runners,
            "pipeline": None if epic is not None else pipeline,
            "grill": grill,
            "epic_architect": epic_architect,
            "epic": epic,
            "epic_confirm": runtime_settings.pipeline.epic_confirm,
        }

    @app.get("/prs/{owner}/{repo}/{number}/review-state")
    async def pr_review_state(owner: str, repo: str, number: int) -> dict[str, str]:
        client = GitHubIssueClient(get_gh_token())
        state = await client.fetch_pr_review_state(owner, repo, number)
        return {"state": state}

    @app.get("/pr/{owner}/{repo}/{number}/status")
    async def pr_status(owner: str, repo: str, number: int) -> dict[str, object]:
        review, repair = await asyncio.gather(
            get_run_by_pr_number(
                runtime_settings,
                owner=owner,
                repo=repo,
                pr_number=number,
                workflow_type="review",
            ),
            get_run_by_pr_number(
                runtime_settings,
                owner=owner,
                repo=repo,
                pr_number=number,
                workflow_type="repair",
            ),
        )
        return {
            "review": None if review is None else await get_run_state(runtime_settings, str(review["id"])),
            "repair": None if repair is None else await get_run_state(runtime_settings, str(repair["id"])),
        }

    @app.post("/issue/start")
    async def issue_start(
        body: IssueStartRequest,
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

            failed_epic = await get_latest_failed_epic_run_by_issue(
                runtime_settings,
                owner=body.owner,
                repo=body.repo,
                issue_number=body.number,
            )
            resumed = failed_epic is not None
            if failed_epic is None:
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
                latest_child_runs = {}
                mode = runtime_settings.pipeline.epic_branch_mode
                branch = None
                pr_url = None
            else:
                group_id = str(failed_epic["group_id"])
                parent_run_id = str(failed_epic["run_id"])
                latest_child_runs = await get_latest_pipeline_runs_by_group_issue(
                    runtime_settings,
                    group_id,
                )
                mode = str(failed_epic["mode"])
                branch = (
                    None
                    if failed_epic["branch"] is None
                    else str(failed_epic["branch"])
                )
                pr_url = (
                    None
                    if failed_epic["pr_url"] is None
                    else str(failed_epic["pr_url"])
                )
            _spawn_run_task(
                parent_run_id,
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
                        run_id=str(
                            (
                                latest_child_runs.get(int(sub_issue["number"]))
                                or {}
                            ).get("id")
                            or ""
                        ),
                    )
                    for sub_issue in sub_issues
                ],
                resumed=resumed,
                status="epic_running",
                mode=mode,
                branch=branch,
                pr_url=pr_url,
            )

        run_id = await _prepare_pipeline_run(url, runtime_settings)
        _spawn_run_task(
            run_id,
            _run_pipeline_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return PipelineStartResponse(run_id=run_id)

    @app.post("/issue/grill")
    async def issue_grill(
        body: IssueGrillRequest,
    ) -> dict[str, str]:
        url = f"https://github.com/{body.owner}/{body.repo}/issues/{body.number}"
        run_id = await _prepare_grill_run(url, runtime_settings)
        _spawn_run_task(
            run_id,
            _run_grill_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return {"run_id": run_id}

    @app.post("/issue/epic-architect")
    async def issue_epic_architect(
        body: IssueEpicArchitectRequest,
    ) -> dict[str, str]:
        url = f"https://github.com/{body.owner}/{body.repo}/issues/{body.number}"
        run_id = await _prepare_epic_architect_run(url, runtime_settings)
        _spawn_run_task(
            run_id,
            _run_epic_architect_background,
            url,
            runtime_settings,
            run_id=run_id,
        )
        return {"run_id": run_id}

    @app.post("/runs/review/start")
    async def review_start(
        body: ReviewStartRequest,
    ) -> ReviewStartResponse:
        from uuid import uuid4

        run_id = str(uuid4())
        await create_review_run(
            runtime_settings,
            run_id=run_id,
            owner=body.owner,
            repo=body.repo,
            pr_number=body.pr_number,
        )
        _spawn_run_task(
            run_id,
            _run_review_background,
            runtime_settings,
            run_id=run_id,
        )
        return ReviewStartResponse(run_id=run_id)

    @app.post("/runs/repair/start")
    async def repair_start(
        body: RepairStartRequest,
    ) -> RepairStartResponse:
        from uuid import uuid4

        run_id = str(uuid4())
        await create_repair_run(
            runtime_settings,
            run_id=run_id,
            owner=body.owner,
            repo=body.repo,
            pr_number=body.pr_number,
        )
        _spawn_run_task(
            run_id,
            _run_repair_background,
            runtime_settings,
            run_id=run_id,
        )
        return RepairStartResponse(run_id=run_id)

    @app.post("/runs/{run_id}/create-issues")
    async def create_review_issues(run_id: str) -> dict[str, object]:
        return await _create_review_issues(runtime_settings, run_id)

    return app


def _debug_print_settings(settings: Settings) -> None:
    if not settings.debug:
        return

    print("[pawchestrator:debug] config:", flush=True)
    print(
        json.dumps(settings.model_dump(mode="json"), indent=2, sort_keys=True),
        flush=True,
    )


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


def _spawn_run_task(
    task_run_id: str,
    func,
    *args,
    **kwargs,
) -> asyncio.Task[None]:
    task = asyncio.create_task(func(*args, **kwargs))
    _active_run_tasks[task_run_id] = task
    task.add_done_callback(
        lambda completed: _unregister_run_task(task_run_id, completed)
    )
    return task


def _unregister_run_task(run_id: str, completed: asyncio.Task[None]) -> None:
    if _active_run_tasks.get(run_id) is completed:
        _active_run_tasks.pop(run_id, None)

async def _create_review_issues(settings: Settings, run_id: str) -> dict[str, object]:
    state = await get_run_state(settings, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    if state.get("workflow_type") != "review":
        raise HTTPException(status_code=400, detail="run is not a review run")
    if not _stage_complete(state, "post"):
        raise HTTPException(status_code=409, detail="post stage is not complete")
    if _stage_status(state, "issues") != "pending":
        raise HTTPException(status_code=409, detail="issues stage is not pending")

    report = _load_review_report(settings, run_id)
    suggested_issues = report.get("suggested_issues")
    inline_comments = report.get("inline_comments")
    if not isinstance(suggested_issues, list) or not all(
        isinstance(issue, dict)
        and isinstance(issue.get("hint"), str)
        and isinstance(issue.get("file"), str)
        and isinstance(issue.get("line"), int)
        for issue in suggested_issues
    ):
        raise HTTPException(
            status_code=500,
            detail="review report suggested_issues must be a list of issue objects",
        )
    if not isinstance(inline_comments, list) or not all(
        isinstance(comment, dict)
        and isinstance(comment.get("file"), str)
        and isinstance(comment.get("line"), int)
        and isinstance(comment.get("body"), str)
        for comment in inline_comments
    ):
        raise HTTPException(
            status_code=500,
            detail="review report inline_comments must be a list of comment objects",
        )

    async def body(_log_path: Path) -> tuple[dict[str, object], Path]:
        created_issue_urls: list[str] = []
        artifact_path = _created_issues_report_path(settings, run_id)
        if not suggested_issues:
            raise StageSkipped(
                "No suggested issues.",
                {"created_issue_urls": created_issue_urls},
                artifact_path,
            )

        owner = str(state["owner"])
        repo = str(state["repo"])
        repo_path = await lookup_repo_path(settings, owner=owner, repo=repo)
        cwd = repo_path or Path.cwd()
        try:
            created_issue_urls = await format_and_create_issues(
                settings,
                run_id=run_id,
                cwd=cwd,
                owner=owner,
                repo=repo,
                pr_summary=str(report.get("summary") or ""),
                suggested_issues=suggested_issues,
                inline_comments=inline_comments,
                artifact_path=artifact_path,
                write_created_issues_report=_write_created_issues_report,
                created_issue_urls=created_issue_urls,
                repo_path=repo_path,
            )
        except Exception as error:
            raise StageFailedWithArtifact(
                str(error),
                {"created_issue_urls": created_issue_urls},
                artifact_path,
            ) from error
        return {"created_issue_urls": created_issue_urls}, artifact_path

    try:
        await run_stage_lifecycle(settings, run_id, "issues", body)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    result = await get_run_state(settings, run_id)
    return result or {}


def _stage_complete(state: dict[str, object], stage_name: str) -> bool:
    return _stage_status(state, stage_name) == "complete"


def _stage_status(state: dict[str, object], stage_name: str) -> str | None:
    stages = state.get("stages")
    if not isinstance(stages, list):
        return None
    for stage in stages:
        if isinstance(stage, dict) and stage.get("stage_name") == stage_name:
            status = stage.get("status")
            return str(status) if status is not None else None
    return None


def _load_review_report(settings: Settings, run_id: str) -> dict[str, object]:
    path = review_report_path(settings, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(status_code=409, detail="review report not found") from error
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="review report is invalid JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="review report must be an object")
    return payload


def _created_issues_report_path(settings: Settings, run_id: str) -> Path:
    return settings.app_dir / "runs" / run_id / "created_issues_report.json"


def _write_created_issues_report(path: Path, created_issue_urls: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"created_issue_urls": created_issue_urls}, indent=2) + "\n",
        encoding="utf-8",
    )


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
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] failed: {error}")
    finally:
        close_run_stream(run_id)


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
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"[epic {parent_run_id}] failed: {error}")
    finally:
        close_run_stream(parent_run_id)


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
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] grill failed: {error}")
    finally:
        close_run_stream(run_id)


async def _run_epic_architect_background(
    issue_url_value: str,
    settings: Settings,
    *,
    run_id: str,
) -> None:
    try:
        await run_epic_scout(issue_url_value, settings, run_id=run_id)
        await run_epic_architect(issue_url_value, settings, run_id=run_id)
        await mark_run_completed(
            settings,
            run_id=run_id,
            current_stage="epic_architect",
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        state = await get_run_state(settings, run_id)
        if (state or {}).get("status") != "epic_architect_failed":
            await mark_run_failed(settings, run_id=run_id)
        print(f"[run {run_id}] epic architect failed: {error}")
    finally:
        close_run_stream(run_id)


async def _run_review_background(
    settings: Settings,
    *,
    run_id: str,
) -> None:
    try:
        await run_review(run_id, settings)
        await run_review_post(run_id, settings)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"[run {run_id}] review failed: {error}")
    finally:
        close_run_stream(run_id)


async def _run_repair_background(
    settings: Settings,
    *,
    run_id: str,
) -> None:
    try:
        await run_repair(run_id, settings)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"[run {run_id}] repair failed: {error}")
    finally:
        close_run_stream(run_id)


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


async def _prepare_epic_architect_run(issue_url_value: str, settings: Settings) -> str:
    from uuid import uuid4

    from pawchestrator.db import create_epic_architect_run
    from pawchestrator.github import parse_issue_url

    reference = parse_issue_url(issue_url_value)
    run_id = str(uuid4())
    await create_epic_architect_run(
        settings,
        run_id=run_id,
        owner=reference.owner,
        repo=reference.repo,
        issue_number=reference.number,
    )
    return run_id

