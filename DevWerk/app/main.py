"""DevWerk V1 FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logging import configure_logging, configure_logging_from_env
from app.services.usage import init_usage_db
from app.v1.api import router as v1_router
from app.v1.capabilities import build_core_registry
from app.v1.conversation import ConversationAgent
from app.v1.runtime import RuntimeSupervisor
from app.v1.store import V1Store
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicyLoader


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()
    configure_logging(cfg)
    log = logging.getLogger("devwerk")
    log.info("DevWerk V1 starting app_env=%s db=%s", cfg.app_env, cfg.devwerk_db_path)
    init_usage_db()
    policy = DEFAULT_V1_RUNTIME_POLICY
    platform_policy = PlatformPolicyLoader(Path(__file__).resolve().parents[1] / "DEVWERK.md").load()
    registry = build_core_registry(policy)
    store = V1Store(cfg.devwerk_db_path, policy, registry=registry)
    platform_policy = store.register_platform_policy(platform_policy)
    supervisor = RuntimeSupervisor(store, registry)
    app.state.v1_store = store
    app.state.v1_registry = registry
    conversation = ConversationAgent(store, registry, on_task_created=supervisor.wake, policy=policy, platform_policy=platform_policy)
    app.state.v1_conversation = conversation
    app.state.v1_supervisor = supervisor
    if cfg.workflow_supervisor_enabled:
        supervisor.start()
    try:
        yield
    finally:
        conversation.stop()
        supervisor.stop()
        log.info("DevWerk V1 stopped")


def create_app() -> FastAPI:
    configure_logging_from_env()
    app = FastAPI(
        title="DevWerk V1 API",
        description="Conversation-led multi-agent workflow runtime",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def disable_v1_web_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/web/static/") or request.url.path in {
            "/", "/workbench", "/dashboard", "/kanban", "/tasks", "/events"
        }:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    app.include_router(v1_router, prefix="/v1", tags=["DevWerk V1"])
    web_root = Path(__file__).resolve().parent / "web"
    app.mount("/web/static", StaticFiles(directory=web_root / "static"), name="web-static")

    def index() -> FileResponse:
        return FileResponse(web_root / "templates" / "dashboard.html")

    for route, name in (
        ("/", "web-root"),
        ("/workbench", "web-overview"),
        ("/dashboard", "web-projects"),
        ("/kanban", "web-kanban"),
        ("/tasks", "web-tasks"),
        ("/events", "web-events"),
    ):
        app.add_api_route(route, index, methods=["GET"], include_in_schema=False, name=name)

    return app


app = create_app()


if __name__ == "__main__":
    cfg = settings()
    configure_logging(cfg)
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=cfg.reload, log_level=cfg.log_level.lower(), access_log=cfg.uvicorn_access_log)
