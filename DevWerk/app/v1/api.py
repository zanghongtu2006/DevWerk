from __future__ import annotations

import sqlite3
import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.v1.domain import ConversationRequest, ProjectCreate, TaskCreate, WorkflowPublishRequest
from app.v1.capabilities import validate_workflow_capabilities


router = APIRouter()


def store(request: Request):
    return request.app.state.v1_store


def supervisor(request: Request):
    return request.app.state.v1_supervisor


def not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


def require_control(request: Request) -> None:
    expected = str(request.app.state.v1_control_token)
    if not expected:
        raise HTTPException(status_code=503, detail="automation control plane is disabled until DEVWERK_CONTROL_TOKEN is configured")
    supplied = str(request.headers.get("X-DevWerk-Control-Token") or "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="automation control token is required")


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    with store(request).connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "runtime": "devwerk-v1", "supervisor": "running"}


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, request: Request) -> dict[str, Any]:
    base_dir = str(Path(payload.base_dir).expanduser().resolve())
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    try:
        return store(request).create_project(payload.name, payload.description, base_dir, payload.agent_instruction)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="project base_dir is already registered") from exc


@router.get("/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    return store(request).list_projects()


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).get_project(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/conversation", status_code=202)
def converse(project_id: str, payload: ConversationRequest, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.v1_conversation.submit(project_id, payload.message, payload.start_task)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation")
def conversation(project_id: str, request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).messages(project_id, limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/conversation-jobs/{job_id}")
def conversation_job(project_id: str, job_id: str, request: Request) -> dict[str, Any]:
    try:
        job = store(request).get_conversation_job(job_id)
        if job["project_id"] != project_id:
            raise KeyError(job_id)
        return job
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/workflow", status_code=201)
def publish_workflow(project_id: str, payload: WorkflowPublishRequest, request: Request) -> dict[str, Any]:
    """Control-plane endpoint for trusted automation, not a user Kanban edit API."""
    require_control(request)
    try:
        store(request).get_project(project_id)
        validate_workflow_capabilities(payload.workflow, request.app.state.v1_registry)
        return store(request).publish_workflow(project_id, payload.workflow)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/workflow")
def get_workflow(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).get_workflow(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/tasks", status_code=201)
def create_task(project_id: str, payload: TaskCreate, request: Request) -> dict[str, Any]:
    require_control(request)
    try:
        task = store(request).create_task(
            project_id,
            payload.title,
            payload.brief,
            payload.input,
            payload.readiness.model_dump(mode="json"),
            pending_timeout_seconds=payload.pending_timeout_seconds,
        )
        supervisor(request).wake()
        return task
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str, request: Request, limit: int = Query(100, ge=1, le=500), cursor: str | None = None) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).list_tasks(project_id, limit, cursor)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/board")
def board(project_id: str, request: Request) -> dict[str, Any]:
    try:
        workflow = store(request).get_workflow(project_id)
    except KeyError:
        workflow = None
    try:
        tasks = store(request).list_tasks(project_id, 200)
        return {"project": store(request).get_project(project_id), "workflow": workflow, "tasks": tasks}
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = store(request).get_project_task(project_id, task_id)
        task["runs"] = store(request).runs(project_id, task_id)
        task["artifacts"] = store(request).artifacts(project_id, task_id)
        task["agent_runs"] = store(request).agent_runs(project_id=project_id, task_id=task_id, limit=200)
        return task
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/runs")
def task_runs(project_id: str, task_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).runs(project_id, task_id, limit, after_sequence)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/artifacts")
def task_artifacts(project_id: str, task_id: str, request: Request, after: str = "", limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).artifacts(project_id, task_id, limit, after)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/events")
def task_events(project_id: str, task_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).events(task_id=task_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/events")
def project_events(project_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).events(project_id=project_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs")
def project_agent_runs(project_id: str, request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).agent_runs(project_id=project_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/agent-runs")
def task_agent_runs(project_id: str, task_id: str, request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).agent_runs(project_id=project_id, task_id=task_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs/{agent_run_id}")
def get_agent_run(project_id: str, agent_run_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    try:
        run = store(request).get_agent_run(project_id, agent_run_id)
        run["messages"] = store(request).agent_messages(project_id, agent_run_id, limit, after_sequence)
        run["tool_invocations"] = store(request).tool_invocations(project_id, agent_run_id, limit, after_sequence)
        return run
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/projection")
def projection(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).project_projection(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/governance")
def governance(project_id: str, request: Request, limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).governance_decisions(project_id, limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/stream")
async def project_stream(project_id: str, request: Request, after: int = Query(0, ge=0)) -> StreamingResponse:
    try:
        store(request).get_project(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc

    async def generate():
        cursor = after
        while not await request.is_disconnected():
            events = store(request).events(project_id=project_id, after=cursor, limit=200)
            if events:
                for event in events:
                    cursor = max(cursor, int(event["id"]))
                    yield f"id: {cursor}\nevent: project\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
