"""
DevWerk Backend — FastAPI application entry point.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Ensure the project root is on the import path so 'app' resolves.
sys.path.insert(0, str(__file__.rsplit("/", 2)[0]))

from app.core.config import settings
from app.routes.ide import router as ide_router
from app.routes.kanban import router as kanban_router
from app.routes.kanban import ui_router as kanban_ui_router
from app.routes.settings import router as settings_router
from app.services.kanban import init_kanban_db
from app.services.usage import clear_request, finish_request, init_usage_db, start_request


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle events on startup and shutdown.

    - Log active configuration (without secrets).
    - Validate provider credentials on startup so failures are visible immediately.
    """
    cfg = settings()
    log = logging.getLogger("devwerk")

    log.info("DevWerk starting — APP_ENV=%s, DEFAULT_API=%s", cfg.app_env, cfg.llm_provider_name)

    # Log sanitised provider config (hide API keys).
    def _safe(v: str | None) -> str:
        if v is None:
            return "<not set>"
        if len(v) > 8:
            return v[:4] + "****" + v[-4:]
        return "****"

    llm = cfg.get_llm_config("coder")
    safe_config = {k: _safe(v) if k == "api_key" else v for k, v in llm.items()}
    log.info("Active LLM config: %s", safe_config)
    init_usage_db()
    init_kanban_db()

    if cfg.app_env == "production" and not cfg.is_production:
        log.warning("Running in production but APP_ENV is not 'production'!")

    yield

    log.info("DevWerk shutting down.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DevWerk API",
        description="AI-driven CodeOps backend for IDE integration.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Allow IDE plugins (typically localhost) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],   # tighten in production via ALLOWED_ORIGINS env var
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def usage_tracking_middleware(request: Request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)

        project_id = request.headers.get("X-DevWerk-Project-Id") or request.query_params.get("project_id")
        ctx = start_request(project_id, route=request.url.path, action=request.method)
        try:
            response = await call_next(request)
            finish_request(ctx, status_code=response.status_code, success=response.status_code < 500)
            return response
        except Exception as exc:  # noqa: BLE001
            finish_request(ctx, status_code=500, success=False, error_type=type(exc).__name__)
            raise
        finally:
            clear_request()

    app.include_router(ide_router, prefix="/v1", tags=["IDE"])
    app.include_router(kanban_router, prefix="/v1", tags=["Kanban"])
    app.include_router(settings_router, prefix="/v1", tags=["Settings"])
    app.include_router(kanban_ui_router)

    return app


app = create_app()


if __name__ == "__main__":
    # Allow:  python app/main.py
    # Equivalent to: uvicorn app.main:app --reload --port 8000
    cfg = settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=cfg.reload,
    )
