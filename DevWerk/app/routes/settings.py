from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.global_settings import get_global_settings, update_global_settings

router = APIRouter(prefix="/settings", tags=["Settings"])


class GlobalSettingsRequest(BaseModel):
    llms: dict[str, Any] | None = Field(default=None)
    routing: dict[str, Any] | None = Field(default=None)


@router.get("")
def settings_get():
    return get_global_settings()


@router.put("")
def settings_update(req: GlobalSettingsRequest):
    try:
        return update_global_settings(req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
