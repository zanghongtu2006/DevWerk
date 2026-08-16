from __future__ import annotations

import sqlite3
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.debug_trace import trace_json
from app.v1.domain import ConversationRequest, ExternalEventSignal, OrchestrationPlanCreate, ProjectCreate, TaskCreate, WorkflowPublishRequest, WorkflowTemplateApplyRequest
from app.v1.capabilities import (
    CapabilityContext,
)
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY


router = APIRouter()
_trace_log = logging.getLogger("devwerk.web.trace")
DEFAULT_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.default_page_size
DETAIL_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.detail_page_size
MAX_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.max_page_size


def store(request: Request):
    return request.app.state.v1_store


def supervisor(request: Request):
    return request.app.state.v1_supervisor


def not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/capabilities")
def project_capabilities(project_id: str, request: Request) -> list[dict[str, Any]]:
    """Discover the live, project-available capability contracts for Column Runtime."""
    try:
        project = store(request).get_project(project_id)
        context = CapabilityContext(project_id=project_id, project=project, store=store(request))
        return request.app.state.v1_registry.column_catalog(context)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/conversation", status_code=202)
def converse(project_id: str, payload: ConversationRequest, request: Request) -> dict[str, Any]:
    trace_json(
        _trace_log,
        "web.conversation_input",
        project_id=project_id,
        message=payload.message,
        start_task=payload.start_task,
    )
    try:
        result = request.app.state.v1_conversation.submit(project_id, payload.message, payload.start_task)
        trace_json(_trace_log, "web.conversation_output", project_id=project_id, output=result)
        return result
    except KeyError as exc:
        trace_json(_trace_log, "web.conversation_error", project_id=project_id, error=repr(exc))
        raise not_found(exc) from exc
    except ValueError as exc:
        trace_json(_trace_log, "web.conversation_error", project_id=project_id, error=repr(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation")
def conversation(
    project_id: str,
    request: Request,
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    after_id: int | None = Query(None, ge=0),
    before_id: int | None = Query(None, ge=1),
) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).messages(
            project_id,
            limit,
            after_id=after_id,
            before_id=before_id,
        )
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation-state")
def conversation_state(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).conversation_state(project_id)
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
    """V1 automation endpoint; the customer Kanban remains read-only."""
    try:
        store(request).get_project(project_id)
        return store(request).publish_workflow(project_id, payload.workflow, payload.orchestration_plan_id)
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


@router.get("/workflow-templates")
def workflow_templates(
    request: Request,
    category: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
) -> list[dict[str, Any]]:
    return store(request).list_workflow_templates(category=category, tag=tag, query=query, limit=limit)


@router.get("/workflow-templates/{template_key}")
def workflow_template(template_key: str, request: Request, version: int | None = Query(None, ge=1)) -> dict[str, Any]:
    try:
        return store(request).get_workflow_template(template_key, version)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/workflow-template", status_code=201)
def apply_workflow_template(project_id: str, payload: WorkflowTemplateApplyRequest, request: Request) -> dict[str, Any]:
    try:
        result = store(request).apply_workflow_template(project_id, payload.template_key, payload.bindings, payload.version)
        supervisor(request).wake()
        return result
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/automation/orchestration-plans", status_code=201)
def create_orchestration_plan(project_id: str, payload: OrchestrationPlanCreate, request: Request) -> dict[str, Any]:
    try:
        return store(request).create_orchestration_plan(project_id, payload.plan)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/orchestration-plans")
def list_orchestration_plans(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).list_orchestration_plans(project_id, limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/tasks", status_code=201)
def create_task(project_id: str, payload: TaskCreate, request: Request) -> dict[str, Any]:
    try:
        task = store(request).create_task(
            project_id,
            payload.title,
            payload.brief,
            payload.input,
            payload.readiness.model_dump(mode="json"),
            orchestration_plan_id=payload.orchestration_plan_id,
            proposed_task_ref=payload.proposed_task_ref,
        )
        supervisor(request).wake()
        return task
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/automation/events", status_code=201)
def signal_event(project_id: str, payload: ExternalEventSignal, request: Request) -> dict[str, Any]:
    try:
        event = store(request).record_external_event(project_id, payload.event_type, payload.correlation_key, payload.output)
        supervisor(request).wake()
        return event
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE), cursor: str | None = None) -> list[dict[str, Any]]:
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
        tasks = store(request).list_tasks(project_id, DETAIL_PAGE)
        return {"project": store(request).get_project(project_id), "workflow": workflow, "tasks": tasks}
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = store(request).get_project_task(project_id, task_id)
        task["runs"] = store(request).runs(project_id, task_id)
        task["attempts"] = store(request).attempts(project_id, task_id)
        task["artifacts"] = store(request).artifacts(project_id, task_id)
        task["agent_runs"] = store(request).agent_runs(project_id=project_id, task_id=task_id, limit=DETAIL_PAGE)
        return task
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/runs")
def task_runs(project_id: str, task_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).runs(project_id, task_id, limit, after_sequence)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/artifacts")
def task_artifacts(project_id: str, task_id: str, request: Request, after: str = "", limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).artifacts(project_id, task_id, limit, after)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/events")
def task_events(project_id: str, task_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).events(task_id=task_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/events")
def project_events(project_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).events(project_id=project_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs")
def project_agent_runs(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).agent_runs(project_id=project_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/agent-runs")
def task_agent_runs(project_id: str, task_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).agent_runs(project_id=project_id, task_id=task_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs/{agent_run_id}")
def get_agent_run(project_id: str, agent_run_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> dict[str, Any]:
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


@router.get("/projects/{project_id}/quiescence")
def quiescence(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).project_quiescence(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/governance")
def governance(project_id: str, request: Request, limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
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
            events = store(request).events(project_id=project_id, after=cursor, limit=DETAIL_PAGE)
            if events:
                for event in events:
                    cursor = max(cursor, int(event["id"]))
                    yield f"id: {cursor}\nevent: project\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(store(request).policy.service_limits.event_poll_interval_seconds)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
