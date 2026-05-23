"""FastAPI application for the local Pawchestrator backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from pawchestrator import __version__
from pawchestrator.config import LOCAL_HOST, Settings, load_settings
from pawchestrator.db import init_db


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_path = await init_db(runtime_settings)
        app.state.settings = runtime_settings
        app.state.database_path = database_path
        yield

    app = FastAPI(title="Pawchestrator", version=__version__, lifespan=lifespan)

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

    return app
