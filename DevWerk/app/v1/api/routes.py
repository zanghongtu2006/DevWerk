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
from app.core.global_settings import (
    GlobalSettings,
    global_settings_payload,
    restart_required_changes,
    save_global_settings,
)
from app.v1.domain import ConversationRequest, ExternalEventSignal, LoopApplyRequest, ProjectCreate, TaskCreate, TaskPlanCreate, WorkflowPlanCreate, WorkflowRevisionPublishRequest
from app.v1.context.memory import MemoryRecord
from app.v1.capabilities import (
    CapabilityContext,
)
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY
from app.v1.execution.states import runtime_status_catalog
from app.v1.protocols.transport.api_response_contracts import (AgentRunDetailResponse, BoardResponse,
    ConversationJobRecord, ConversationSubmitReceipt, HealthResponse, TaskDetailResponse)
from app.v1.protocols.tools.planning.capability_project_receipts import ProjectRecord, WorkflowRevisionReceipt
from app.v1.protocols.tools.planning.capability_loop_receipts import LoopApplyReceipt, LoopInspectReceipt, LoopSummaryReceipt
from app.v1.protocols.tools.planning.capability_contracts import TaskPlanRecord, WorkflowPlanRecord
from app.v1.protocols.tools.planning.capability_task_receipts import TaskRecord, TaskSuccessorReceipt
from app.v1.protocols.tools.planning.capability_task_receipts import ArtifactProjection
from app.v1.protocols.tools.core.capability_misc_receipts import EventReceipt
from app.v1.protocols.agent.run_record_contracts import AgentRunRecord, ColumnAttemptRecord, ColumnRunRecord
from app.v1.protocols.context.memory_output_contracts import MemoryFileReceipt
from app.v1.protocols.storage.repository_event_contracts import ExternalEventReceipt
from app.v1.protocols.storage.store_receipt_contracts import (ConversationMessageRecord,
    ConversationStateRecord, GovernanceDecisionRecord, KanbanProjectionRecord,
    ProjectQuiescenceRecord)
from app.v1.protocols.storage.store_receipt_contracts import AgentMessageRecord, ToolInvocationRecord
from app.v1.protocols.workflow.state_catalog_contracts import RuntimeStatusCatalog
from app.v1.protocols.tools.core.capability_catalog_contracts import CapabilityCatalogEntry
from app.core.global_settings_contracts import GlobalSettingsPayload, GlobalSettingsUpdatePayload


router = APIRouter()
_trace_log = logging.getLogger("devwerk.web.trace")
DEFAULT_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.default_page_size
DETAIL_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.detail_page_size
MAX_PAGE = DEFAULT_V1_RUNTIME_POLICY.service_limits.max_page_size


@router.get("/runtime-statuses", response_model=RuntimeStatusCatalog)
def runtime_statuses() -> dict[str, dict[str, object]]:
    return runtime_status_catalog()


@router.get("/settings", response_model=GlobalSettingsPayload)
def get_global_settings(request: Request) -> dict[str, Any]:
    return global_settings_payload(request.app.state.v1_global_settings)


@router.post("/settings", response_model=GlobalSettingsUpdatePayload)
def update_global_settings(
    payload: GlobalSettings,
    request: Request,
) -> dict[str, Any]:
    previous = request.app.state.v1_global_settings
    restart_changes = restart_required_changes(previous, payload)
    save_global_settings(request.app.state.v1_global_settings_path, payload)
    request.app.state.v1_global_settings = payload
    request.app.state.v1_conversation.global_settings = payload.model_dump(mode="json")
    restart_scheduled = bool(restart_changes) and request.app.state.v1_restart.schedule()
    return GlobalSettingsUpdatePayload.from_base(
        GlobalSettingsPayload.model_validate(global_settings_payload(payload)),
        changed=previous != payload,
        restart_changes=restart_changes,
        restart_scheduled=restart_scheduled,
    ).model_dump(mode="json")


def store(request: Request):
    return request.app.state.v1_store


def supervisor(request: Request):
    return request.app.state.v1_supervisor


def not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> dict[str, Any]:
    with store(request).connect() as db:
        db.execute("SELECT 1").fetchone()
    runtime_health = supervisor(request).health()
    gateway_health = request.app.state.v1_conversation.status()
    return HealthResponse(status="ok" if runtime_health["running"] and gateway_health["status"] == "running" else "degraded",
        supervisor="running" if runtime_health["running"] else "stopped",
        supervisor_health=runtime_health, conversation_gateway=gateway_health).model_dump(mode='json')


@router.post("/projects", status_code=201, response_model=ProjectRecord)
def create_project(payload: ProjectCreate, request: Request) -> dict[str, Any]:
    base_dir = str(Path(payload.base_dir).expanduser().resolve())
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    try:
        return store(request).create_project(payload.name, payload.description, base_dir, payload.agent_instruction)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="project base_dir is already registered") from exc


@router.get("/projects", response_model=list[ProjectRecord])
def list_projects(request: Request) -> list[dict[str, Any]]:
    return store(request).list_projects()


@router.get("/projects/{project_id}", response_model=ProjectRecord)
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).get_project(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/capabilities", response_model=list[CapabilityCatalogEntry])
def project_capabilities(project_id: str, request: Request) -> list[dict[str, Any]]:
    """Discover the live, project-available capability contracts for Column Runtime."""
    try:
        project = store(request).get_project(project_id)
        context = CapabilityContext(project_id=project_id, project=project, store=store(request))
        return request.app.state.v1_registry.column_catalog(context)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/memory", response_model=list[MemoryFileReceipt])
def project_memory(
    project_id: str,
    request: Request,
    query: str | None = None,
    scope: str | None = None,
    scope_id: str | None = None,
    kind: list[str] = Query(default=[]),
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        if query is not None:
            return store(request).memory_search(
                project_id,
                query,
                scope=scope,
                scope_id=scope_id,
                kinds=kind,
                limit=None,
            )
        return store(request).memory_list(
            project_id,
            scope=scope,
            scope_id=scope_id,
            kinds=kind,
            include_inactive=include_inactive,
        )
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/memory", status_code=201, response_model=MemoryFileReceipt)
def write_project_memory(
    project_id: str,
    payload: MemoryRecord,
    request: Request,
) -> dict[str, Any]:
    try:
        store(request).get_project(project_id)
        return store(request).memory_write(project_id, payload.model_dump(mode="json"))
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/conversation", status_code=202,
             response_model=ConversationSubmitReceipt)
async def converse(project_id: str, payload: ConversationRequest, request: Request) -> dict[str, Any]:
    trace_json(
        _trace_log,
        "web.conversation_input",
        project_id=project_id,
        message=payload.message,
        start_task=payload.start_task,
    )
    try:
        result = await request.app.state.v1_conversation.submit(
            project_id,
            payload.message,
            payload.start_task,
            mode=payload.mode,
            user_action=payload.user_action.model_dump(mode='json') if payload.user_action else None,
        )
        trace_json(_trace_log, "web.conversation_output", project_id=project_id, output=result)
        return result
    except KeyError as exc:
        trace_json(_trace_log, "web.conversation_error", project_id=project_id, error=repr(exc))
        raise not_found(exc) from exc
    except ValueError as exc:
        trace_json(_trace_log, "web.conversation_error", project_id=project_id, error=repr(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation", response_model=list[ConversationMessageRecord])
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
            visible_only=True,
        )
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation-state", response_model=ConversationStateRecord)
def conversation_state(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).conversation_state(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/conversation-jobs/{job_id}",
            response_model=ConversationJobRecord)
def conversation_job(project_id: str, job_id: str, request: Request) -> dict[str, Any]:
    try:
        job = store(request).get_conversation_job(job_id)
        if job["project_id"] != project_id:
            raise KeyError(job_id)
        return job
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/workflow-revisions", status_code=201,
             response_model=WorkflowRevisionReceipt)
def publish_workflow_revision(project_id: str, payload: WorkflowRevisionPublishRequest, request: Request) -> dict[str, Any]:
    """Publish an initial or revised Workflow against a saved Workflow Plan."""
    try:
        store(request).get_project(project_id)
        return store(request).publish_workflow(project_id, payload.workflow, payload.workflow_plan_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/workflow", response_model=WorkflowRevisionReceipt)
def get_workflow(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).get_workflow(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/loops", response_model=list[LoopSummaryReceipt])
def loops(
    request: Request,
    category: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
) -> list[dict[str, Any]]:
    return store(request).list_loops(category=category, tag=tag, query=query, limit=limit)


@router.get("/loops/{loop_key}", response_model=LoopInspectReceipt)
def loop(loop_key: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).get_loop(loop_key)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/loop", status_code=201,
             response_model=LoopApplyReceipt)
def apply_loop(project_id: str, payload: LoopApplyRequest, request: Request) -> dict[str, Any]:
    try:
        result = store(request).apply_loop(project_id, payload.loop_key, payload.bindings)
        supervisor(request).wake()
        return result
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/automation/workflow-plans", status_code=201,
             response_model=WorkflowPlanRecord)
def create_workflow_plan(project_id: str, payload: WorkflowPlanCreate, request: Request) -> dict[str, Any]:
    try:
        return store(request).create_workflow_plan(project_id, payload.plan)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/workflow-plans", response_model=list[WorkflowPlanRecord])
def list_workflow_plans(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).list_workflow_plans(project_id, limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/task-plans", status_code=201,
             response_model=TaskPlanRecord)
def create_task_plan(project_id: str, payload: TaskPlanCreate, request: Request) -> dict[str, Any]:
    try:
        return store(request).create_task_plan(project_id, payload.plan)
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/task-plans", response_model=list[TaskPlanRecord])
def list_task_plans(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).list_task_plans(project_id, limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.post("/projects/{project_id}/automation/tasks", status_code=201,
             response_model=TaskSuccessorReceipt)
def create_task(project_id: str, payload: TaskCreate, request: Request) -> dict[str, Any]:
    try:
        task = store(request).materialize_task_plan(
            project_id,
            task_plan_id=payload.task_plan_id,
            proposed_task_ref=payload.proposed_task_ref,
        )
        supervisor(request).wake()
        return task
    except KeyError as exc:
        raise not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/automation/events", status_code=201,
             response_model=ExternalEventReceipt)
def signal_event(project_id: str, payload: ExternalEventSignal, request: Request) -> dict[str, Any]:
    try:
        event = store(request).record_external_event(project_id, payload.event_type, payload.correlation_key, payload.output)
        supervisor(request).wake()
        return event
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRecord])
def list_tasks(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE), cursor: str | None = None) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).list_tasks(project_id, limit, cursor)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/board", response_model=BoardResponse)
def board(project_id: str, request: Request) -> dict[str, Any]:
    try:
        workflow = store(request).get_workflow(project_id)
    except KeyError:
        workflow = None
    try:
        tasks = store(request).list_tasks(project_id, DETAIL_PAGE)
        return BoardResponse(project=store(request).get_project(project_id),
            workflow=workflow, tasks=tasks).model_dump(mode='json')
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(project_id: str, task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = TaskDetailResponse.model_validate(store(request).get_project_task(project_id, task_id))
        task.runs = [ColumnRunRecord.model_validate(item) for item in store(request).runs(project_id, task_id)]
        task.attempts = [ColumnAttemptRecord.model_validate(item) for item in store(request).attempts(project_id, task_id)]
        task.artifacts = [ArtifactProjection.model_validate(item) for item in store(request).artifacts(project_id, task_id)]
        task.agent_runs = [AgentRunRecord.model_validate(item) for item in store(request).agent_runs(
            project_id=project_id, task_id=task_id, limit=DETAIL_PAGE)]
        return task.model_dump(mode='json')
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/runs",
            response_model=list[ColumnRunRecord])
def task_runs(project_id: str, task_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).runs(project_id, task_id, limit, after_sequence)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/artifacts",
            response_model=list[ArtifactProjection])
def task_artifacts(project_id: str, task_id: str, request: Request, after: str = "", limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).artifacts(project_id, task_id, limit, after)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/events",
            response_model=list[EventReceipt])
def task_events(project_id: str, task_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).events(task_id=task_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/events", response_model=list[EventReceipt])
def project_events(
    project_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE),
    recent: bool = False,
) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        if recent:
            return store(request).recent_events(project_id, limit)
        return store(request).events(project_id=project_id, after=after, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs", response_model=list[AgentRunRecord])
def project_agent_runs(project_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project(project_id)
        return store(request).agent_runs(project_id=project_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/tasks/{task_id}/agent-runs",
            response_model=list[AgentRunRecord])
def task_agent_runs(project_id: str, task_id: str, request: Request, limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)) -> list[dict[str, Any]]:
    try:
        store(request).get_project_task(project_id, task_id)
        return store(request).agent_runs(project_id=project_id, task_id=task_id, limit=limit)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/agent-runs/{agent_run_id}", response_model=AgentRunDetailResponse)
def get_agent_run(project_id: str, agent_run_id: str, request: Request, after_sequence: int = Query(0, ge=0), limit: int = Query(DETAIL_PAGE, ge=1, le=MAX_PAGE)) -> dict[str, Any]:
    try:
        run = AgentRunDetailResponse.model_validate(store(request).get_agent_run(project_id, agent_run_id))
        run.messages = [AgentMessageRecord.model_validate(item) for item in store(request).agent_messages(
            project_id, agent_run_id, limit, after_sequence)]
        run.tool_invocations = [ToolInvocationRecord.model_validate(item) for item in store(request).tool_invocations(
            project_id, agent_run_id, limit, after_sequence)]
        return run.model_dump(mode='json')
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/projection", response_model=KanbanProjectionRecord)
def projection(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).project_projection(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/quiescence", response_model=ProjectQuiescenceRecord)
def quiescence(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store(request).project_quiescence(project_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/projects/{project_id}/governance", response_model=list[GovernanceDecisionRecord])
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
