# app/main.py
from __future__ import annotations

from fastapi import FastAPI

from app.routes.ide import router as ide_router

def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ide_router)
    return app

app = create_app()
