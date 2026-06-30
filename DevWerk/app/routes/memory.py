from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.memory_system import (
    approve_promotion_candidate,
    build_context_pack,
    create_agent_run,
    get_context_pack,
    handle_agent_writeback,
    list_promotion_candidates,
    read_project_memory_items,
    reject_promotion_candidate,
    upsert_memory_item,
)


router = APIRouter(prefix="/memory", tags=["Memory"])


class MemoryItemRequest(BaseModel):
    scope: str
    memory_type: str
    key: str = "latest"
    content: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "api"
    source_ref: dict[str, Any] = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    workflow_id: str = ""
    agent_role: str
    stage: str
    token_budget: int = 4096


class ContextPackRequest(BaseModel):
    workflow_id: str = ""
    agent_role: str
    stage: str
    token_budget: int = 4096
    run_id: str | None = None
    workspace: dict[str, Any] | None = None


class PromotionReviewRequest(BaseModel):
    note: str = ""


@router.post("/projects/{project_id}/tasks/{task_id}/items")
def memory_upsert_task_item(project_id: str, task_id: str, req: MemoryItemRequest):
    try:
        return upsert_memory_item(
            project_id=project_id,
            task_id=task_id,
            scope=req.scope,
            memory_type=req.memory_type,
            key=req.key,
            content=req.content,
            source_type=req.source_type,
            source_ref=req.source_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/items")
def memory_upsert_project_item(project_id: str, req: MemoryItemRequest):
    try:
        return upsert_memory_item(
            project_id=project_id,
            task_id=None,
            scope=req.scope,
            memory_type=req.memory_type,
            key=req.key,
            content=req.content,
            source_type=req.source_type,
            source_ref=req.source_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/items")
def memory_list_project_items(project_id: str, memory_type: str | None = Query(default=None)):
    try:
        return read_project_memory_items(project_id, memory_type=memory_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/tasks/{task_id}/runs")
def memory_create_agent_run(project_id: str, task_id: str, req: AgentRunRequest):
    return create_agent_run(
        project_id=project_id,
        task_id=task_id,
        workflow_id=req.workflow_id,
        agent_role=req.agent_role,
        stage=req.stage,
        token_budget=req.token_budget,
    )


@router.post("/projects/{project_id}/tasks/{task_id}/context")
def memory_build_context(project_id: str, task_id: str, req: ContextPackRequest):
    try:
        return build_context_pack(
            project_id=project_id,
            task_id=task_id,
            workflow_id=req.workflow_id,
            agent_role=req.agent_role,
            stage=req.stage,
            token_budget=req.token_budget,
            run_id=req.run_id,
            workspace=req.workspace,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/context-packs/{context_pack_id}")
def memory_get_context(context_pack_id: str):
    try:
        return get_context_pack(context_pack_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/writeback")
def memory_writeback(run_id: str, payload: dict[str, Any]):
    try:
        return handle_agent_writeback(run_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/candidates")
def memory_list_candidates(
    project_id: str,
    task_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return list_promotion_candidates(project_id=project_id, task_id=task_id, status=status)


@router.post("/projects/{project_id}/candidates/{candidate_id}/approve")
def memory_approve_candidate(project_id: str, candidate_id: str, req: PromotionReviewRequest | None = None):
    try:
        return approve_promotion_candidate(project_id, candidate_id, note=(req.note if req else ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/candidates/{candidate_id}/reject")
def memory_reject_candidate(project_id: str, candidate_id: str, req: PromotionReviewRequest | None = None):
    try:
        return reject_promotion_candidate(project_id, candidate_id, note=(req.note if req else ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
