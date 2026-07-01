from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.plugin_manager import (
    get_global_plugin,
    import_global_plugin,
    list_global_plugins,
    set_global_plugin_enabled,
    upsert_global_plugin,
)


router = APIRouter(prefix="/plugins", tags=["Plugins"])


class PluginUpsertRequest(BaseModel):
    manifest: dict[str, Any] = Field(default_factory=dict)
    skills: dict[str, str] = Field(default_factory=dict)
    commands: dict[str, str] = Field(default_factory=dict)
    agents: dict[str, str] = Field(default_factory=dict)
    hooks: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None
    enabled: bool = True


class PluginEnabledRequest(BaseModel):
    enabled: bool = True


class PluginImportRequest(BaseModel):
    source_path: str


@router.get("")
def plugins_list():
    return {"ok": True, "plugins": list_global_plugins()}


@router.post("/import")
def plugins_import(req: PluginImportRequest):
    try:
        return {"ok": True, "plugin": import_global_plugin(req.source_path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{plugin_id}")
def plugins_get(plugin_id: str):
    try:
        return {"ok": True, "plugin": get_global_plugin(plugin_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{plugin_id}")
def plugins_put(plugin_id: str, req: PluginUpsertRequest):
    try:
        plugin = upsert_global_plugin(
            plugin_id,
            manifest=req.manifest,
            skills=req.skills,
            commands=req.commands,
            agents=req.agents,
            hooks=req.hooks,
            mcp=req.mcp,
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "plugin": plugin}


@router.patch("/{plugin_id}")
def plugins_patch(plugin_id: str, req: PluginEnabledRequest):
    try:
        return {"ok": True, "plugin": set_global_plugin_enabled(plugin_id, req.enabled)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
