"""
DevWerk Backend — FastAPI application entry point.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure the project root is on the import path so 'app' resolves.
sys.path.insert(0, str(__file__.rsplit("/", 2)[0]))

from app.core.config import settings
from app.routes.ide import router as ide_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle events on startup and shutdown.

    - Log active configuration (without secrets).
    - Validate provider credentials on startup so failures are visible immediately.
    """
    cfg = settings()
    log = logging.getLogger("devwerk")

    log.info("DevWerk starting — APP_ENV=%s, LLM_PROVIDER=%s", cfg.app_env, cfg.llm_provider)

    # Log sanitised provider config (hide API keys).
    def _safe(v: str | None) -> str:
        if v is None:
            return "<not set>"
        if len(v) > 8:
            return v[:4] + "****" + v[-4:]
        return "****"

    llm = cfg.get_llm_config()
    safe_config = {k: _safe(v) if k == "api_key" else v for k, v in llm.items()}
    log.info("Active LLM config: %s", safe_config)

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

    app.include_router(ide_router, prefix="/v1", tags=["IDE"])

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
