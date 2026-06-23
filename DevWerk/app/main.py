"""
DevWerk Backend — FastAPI application entry point.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Ensure the project root is on the import path so 'app' resolves.
sys.path.insert(0, str(__file__.rsplit("/", 2)[0]))

from app.core.config import settings
from app.core.logging import configure_logging, configure_logging_from_env
from app.mcp_server import create_mcp_server
from app.routes.workflows import _start_workflow_thread, router as workflow_router, workflow_worker_age
from app.routes.kanban import router as kanban_router
from app.routes.kanban import ui_router as kanban_ui_router
from app.routes.settings import router as settings_router
from app.services.kanban import init_kanban_db
from app.services.usage import clear_request, finish_request, init_usage_db, start_request
from app.services.workflow_supervisor import WorkflowSupervisor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle events on startup and shutdown.

    - Log active configuration (without secrets).
    - Validate provider credentials on startup so failures are visible immediately.
    """
    cfg = settings()
    configure_logging(cfg)
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
    supervisor = WorkflowSupervisor(
        start_workflow=_start_workflow_thread,
        active_worker_age=workflow_worker_age,
        config=cfg,
    )
    if bool(getattr(cfg, "workflow_supervisor_enabled", True)):
        supervisor.start()

    if cfg.app_env == "production" and not cfg.is_production:
        log.warning("Running in production but APP_ENV is not 'production'!")

    async with app.state.devwerk_mcp.session_manager.run():
        try:
            yield
        finally:
            supervisor.stop()

    log.info("DevWerk shutting down.")


def create_app() -> FastAPI:
    configure_logging_from_env()
    devwerk_mcp, mcp_http_app = create_mcp_server()
    app = FastAPI(
        title="DevWerk API",
        description="AI-driven workflow and agent runtime for engineering capability providers.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.devwerk_mcp = devwerk_mcp

    # Allow local capability providers to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],   # tighten in production via ALLOWED_ORIGINS env var
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def usage_tracking_middleware(request: Request, call_next):
        log = logging.getLogger("devwerk.request")
        started = time.monotonic()
        if not request.url.path.startswith("/v1/"):
            response = await call_next(request)
            log.debug(
                "request path=%s method=%s status=%s duration_ms=%s",
                request.url.path,
                request.method,
                response.status_code,
                int((time.monotonic() - started) * 1000),
            )
            return response

        project_id = request.headers.get("X-DevWerk-Project-Id") or request.query_params.get("project_id")
        ctx = start_request(project_id, route=request.url.path, action=request.method)
        log.debug(
            "request start method=%s path=%s project_id=%s query=%s",
            request.method,
            request.url.path,
            project_id,
            str(request.query_params),
        )
        try:
            response = await call_next(request)
            finish_request(ctx, status_code=response.status_code, success=response.status_code < 500)
            log.debug(
                "request end method=%s path=%s project_id=%s status=%s duration_ms=%s",
                request.method,
                request.url.path,
                ctx.project_id,
                response.status_code,
                int((time.monotonic() - started) * 1000),
            )
            return response
        except Exception as exc:  # noqa: BLE001
            finish_request(ctx, status_code=500, success=False, error_type=type(exc).__name__)
            log.exception(
                "request failed method=%s path=%s project_id=%s duration_ms=%s",
                request.method,
                request.url.path,
                ctx.project_id,
                int((time.monotonic() - started) * 1000),
            )
            raise
        finally:
            clear_request()

    app.include_router(workflow_router, prefix="/v1", tags=["Workflows"])
    app.include_router(kanban_router, prefix="/v1", tags=["Kanban"])
    app.include_router(settings_router, prefix="/v1", tags=["Settings"])
    app.include_router(kanban_ui_router)
    # Mount last so existing FastAPI routes win and /mcp is served without a redirect.
    app.mount("/", mcp_http_app, name="mcp")

    return app


app = create_app()


if __name__ == "__main__":
    # Allow:  python app/main.py
    # Equivalent to: uvicorn app.main:app --reload --port 8000
    cfg = settings()
    configure_logging(cfg)
    uvicorn.run(
        "app.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=cfg.reload,
        log_level=cfg.log_level.lower(),
        access_log=cfg.uvicorn_access_log,
    )
