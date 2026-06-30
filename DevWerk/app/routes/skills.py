from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.skill_manager import (
    effective_skill_catalog,
    get_global_skill,
    get_project_skill,
    list_global_skills,
    list_project_skills,
    set_project_skill_enabled,
    upsert_global_skill,
    upsert_project_skill,
)


router = APIRouter(prefix="/skills", tags=["Skills"])


class SkillUpsertRequest(BaseModel):
    skill_md: str = ""
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillEnabledRequest(BaseModel):
    enabled: bool = True


@router.get("")
def skills_list():
    return {"ok": True, "entrypoint": "SKILL.md", "skills": list_global_skills()}


@router.get("/{skill_id}")
def skills_get(skill_id: str):
    try:
        return {"ok": True, "skill": get_global_skill(skill_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{skill_id}")
def skills_put(skill_id: str, req: SkillUpsertRequest):
    try:
        return {"ok": True, "skill": upsert_global_skill(skill_id, req.skill_md, req.metadata)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/catalog")
def project_skill_catalog(project_id: str):
    return effective_skill_catalog(project_id)


@router.get("/projects/{project_id}")
def project_skills_list(project_id: str):
    return {"ok": True, "project_id": project_id, "entrypoint": "SKILL.md", "skills": list_project_skills(project_id)}


@router.get("/projects/{project_id}/{skill_id}")
def project_skills_get(project_id: str, skill_id: str):
    try:
        return {"ok": True, "project_id": project_id, "skill": get_project_skill(project_id, skill_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/projects/{project_id}/{skill_id}")
def project_skills_put(project_id: str, skill_id: str, req: SkillUpsertRequest):
    try:
        skill = upsert_project_skill(
            project_id,
            skill_id,
            req.skill_md,
            enabled=req.enabled,
            metadata=req.metadata,
        )
        return {"ok": True, "project_id": project_id, "skill": skill}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/projects/{project_id}/{skill_id}")
def project_skills_patch(project_id: str, skill_id: str, req: SkillEnabledRequest):
    try:
        return {"ok": True, "project_id": project_id, "skill": set_project_skill_enabled(project_id, skill_id, req.enabled)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
