from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

from app.services.llm_factory import get_llm_client
from app.services.kanban import (
    add_artifact,
    add_event,
    add_project_event,
    create_task,
    get_board,
    get_conversation,
    get_project,
    get_project_settings,
    get_project_workflow,
    get_task,
    list_events,
    list_columns,
    list_projects,
    replace_columns,
    update_project_workflow,
    update_project_settings,
    upsert_project,
    update_task,
    update_conversation,
)
from app.services.session_store import read_project_memory
from app.services.verification_policy import verification_failed, verification_has_policy
from app.services.workflow import apply_workflow_action, current_workflow_state
from app.services.workflow_designer import design_project_workflow

router = APIRouter(prefix="/kanban", tags=["Kanban"])
ui_router = APIRouter(tags=["Kanban UI"])


class ColumnIn(BaseModel):
    status_key: str
    title: str
    position: int = 0
    wip_limit: int | None = None
    transition_to: list[str] = Field(default_factory=list)


class ColumnsReplaceRequest(BaseModel):
    project_id: str | None = None
    columns: list[ColumnIn]


class ProjectUpsertRequest(BaseModel):
    project_id: str | None = None
    name: str | None = None
    description: str = ""


class ProjectSettingsRequest(BaseModel):
    agents: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None


class TaskCreateRequest(BaseModel):
    project_id: str | None = None
    title: str
    description: str = ""
    status_key: str | None = None
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    metadata: dict[str, Any] | None = None
    archived: bool | None = None


class EventCreateRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ArtifactCreateRequest(BaseModel):
    artifact_type: str
    path: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinitionRequest(BaseModel):
    workflow: dict[str, Any] = Field(default_factory=dict)


class WorkflowDesignRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    current_workflow: dict[str, Any] | None = None
    current_agents: dict[str, Any] | None = None
    save: bool = False


class ProjectConversationRequest(BaseModel):
    action: str = "message"
    message: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    current_workflow: dict[str, Any] | None = None
    current_agents: dict[str, Any] | None = None
    save: bool = False
    workspace: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/board")
def kanban_board(project_id: str | None = None):
    return get_board(project_id)


@router.get("/projects")
def kanban_projects():
    return {"ok": True, "projects": list_projects()}


@router.get("/events")
def kanban_events(project_id: str | None = None, task_id: str | None = None, limit: int = 200):
    return list_events(project_id=project_id, task_id=task_id, limit=limit)


@router.post("/projects")
def kanban_upsert_project(req: ProjectUpsertRequest):
    return upsert_project(project_id=req.project_id, name=req.name, description=req.description)


@router.get("/projects/{project_id}")
def kanban_get_project(project_id: str):
    return get_project(project_id)


@router.get("/projects/{project_id}/settings")
def kanban_get_project_settings(project_id: str):
    return get_project_settings(project_id)


@router.get("/projects/{project_id}/memory")
def kanban_get_project_memory(project_id: str):
    return {"ok": True, "project_id": project_id, "memory": read_project_memory(project_id)}


@router.get("/projects/{project_id}/workflow")
def kanban_get_project_workflow(project_id: str):
    return get_project_workflow(project_id)


@router.put("/projects/{project_id}/workflow")
def kanban_update_project_workflow(project_id: str, req: WorkflowDefinitionRequest):
    try:
        return update_project_workflow(project_id, req.workflow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/workflow/design")
def kanban_design_project_workflow(project_id: str, req: WorkflowDesignRequest):
    try:
        result = design_project_workflow(
            project_id=project_id,
            messages=req.messages,
            current_workflow=req.current_workflow,
            current_agents=req.current_agents,
        )
        if req.save:
            update_project_workflow(project_id, result["workflow"])
            update_project_settings(project_id, agents=result["agents"])
            result["saved"] = True
        else:
            result["saved"] = False
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/conversation")
def kanban_project_conversation(project_id: str, limit: int = 80):
    events = list_events(project_id=project_id, limit=limit).get("events", [])
    messages = []
    for event in reversed(events):
        if event.get("event_type") != "project_conversation_message":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        messages.append(
            {
                "role": payload.get("role") or "assistant",
                "content": payload.get("content") or "",
                "kind": payload.get("kind") or "message",
                "created_at": event.get("created_at"),
                "task_id": payload.get("task_id"),
            }
        )
    return {"ok": True, "project_id": project_id, "messages": messages, "active_task": _active_project_task(project_id)}


@router.post("/projects/{project_id}/conversation")
def kanban_project_conversation_message(project_id: str, req: ProjectConversationRequest):
    action = str(req.action or "message").strip().lower().replace("-", "_")
    messages = _project_conversation_messages(req.messages, req.message)
    user_text = _latest_message_content(messages, role="user")
    current_workflow, current_agents = _effective_project_workflow_agents(
        project_id,
        req.current_workflow,
        req.current_agents,
    )
    active_task = _active_project_task(project_id, req.metadata.get("active_task_id"))
    if user_text:
        add_project_event(
            project_id,
            "project_conversation_message",
            {"role": "user", "content": user_text, "kind": action, "metadata": req.metadata, "active_task_id": active_task.get("id") if active_task else None},
        )

    if action in {"message", "send"}:
        if not user_text:
            raise HTTPException(status_code=400, detail="message is required")
        decision = _ask_project_conversation_agent(
            project_id=project_id,
            messages=messages,
            current_workflow=current_workflow,
            current_agents=current_agents,
            active_task=active_task,
        )
        decision_action = str(decision.get("action") or "reply").strip().lower().replace("-", "_")
        if decision_action in {"design", "save_design", "revise_workflow", "configure_project"}:
            return _handle_project_workflow_design(
                project_id,
                messages=messages,
                current_workflow=current_workflow,
                current_agents=current_agents,
                save=bool(decision.get("save")) or decision_action == "save_design",
                event_kind=decision_action,
            )
        if decision_action in {"continue_task", "resume_task", "message_task"}:
            task_id = str(decision.get("task_id") or (active_task or {}).get("id") or "").strip()
            if not task_id:
                raise HTTPException(status_code=409, detail="project agent chose continue_task but no active task is available")
            task_message = str(decision.get("task_request") or decision.get("message") or user_text).strip()
            return _handle_project_task_continue(
                project_id,
                task_id=task_id,
                task_message=task_message,
                workspace=req.workspace,
                metadata={"project_agent_decision": decision, **req.metadata},
                event_kind=decision_action,
            )
        if decision_action in {"start_task", "run_task", "dispatch_task"}:
            task_message = str(decision.get("task_request") or decision.get("message") or user_text).strip()
            return _handle_project_task_dispatch(
                project_id,
                task_message=task_message,
                workspace=req.workspace,
                metadata={"project_agent_decision": decision, **req.metadata},
                event_kind=decision_action,
            )
        reply = str(decision.get("reply") or "Project conversation updated.")
        add_project_event(
            project_id,
            "project_conversation_message",
            {"role": "assistant", "content": reply, "kind": "reply", "decision": decision},
        )
        return {"ok": True, "project_id": project_id, "kind": "reply", "reply": reply, "decision": decision}

    if action in {"design", "save_design", "revise_workflow", "configure_project"}:
        return _handle_project_workflow_design(
            project_id,
            messages=messages,
            current_workflow=current_workflow,
            current_agents=current_agents,
            save=req.save or action == "save_design",
            event_kind=action,
        )

    if action in {"start_task", "run_task", "dispatch_task"}:
        if not user_text:
            raise HTTPException(status_code=400, detail="message is required to start a task")
        return _handle_project_task_dispatch(
            project_id,
            task_message=user_text,
            workspace=req.workspace,
            metadata=req.metadata,
            event_kind=action,
        )

    if action in {"continue_task", "resume_task", "message_task"}:
        task_id = str(req.metadata.get("task_id") or req.metadata.get("active_task_id") or (active_task or {}).get("id") or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="active task is required to continue a task")
        if not user_text:
            raise HTTPException(status_code=400, detail="message is required to continue a task")
        return _handle_project_task_continue(
            project_id,
            task_id=task_id,
            task_message=user_text,
            workspace=req.workspace,
            metadata=req.metadata,
            event_kind=action,
        )

    raise HTTPException(status_code=400, detail=f"unsupported project conversation action: {action}")


@router.put("/projects/{project_id}/settings")
def kanban_update_project_settings(project_id: str, req: ProjectSettingsRequest):
    return update_project_settings(
        project_id,
        agents=req.agents,
        parameters=req.parameters,
        workflow=req.workflow,
    )


@router.get("/columns")
def kanban_columns(project_id: str | None = None):
    return {"ok": True, "project_id": project_id or "default", "columns": list_columns(project_id)}


@router.put("/columns")
def kanban_replace_columns(req: ColumnsReplaceRequest):
    try:
        return replace_columns(req.project_id, [c.model_dump() for c in req.columns])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks")
def kanban_create_task(req: TaskCreateRequest):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    try:
        return create_task(
            project_id=req.project_id,
            title=req.title,
            description=req.description,
            status_key=req.status_key,
            priority=req.priority,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}")
def kanban_get_task(task_id: str):
    try:
        return get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/memory")
def kanban_get_task_memory(task_id: str):
    try:
        task = get_task(task_id).get("task") or {}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conversation = get_conversation(task_id) or {}
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    return {
        "ok": True,
        "task_id": task_id,
        "project_id": task.get("project_id"),
        "memory": {
            "task": {key: task.get(key) for key in ("id", "project_id", "title", "description", "status_key", "metadata")},
            "conversation": {
                "state": conversation.get("state"),
                "active_column": conversation.get("active_column"),
                "waiting_for": conversation.get("waiting_for"),
                "summary": conversation.get("summary") or "",
                "message_count": len(conversation.get("messages") or []),
            },
            "artifact_types": [
                str(artifact.get("artifact_type") or "")
                for artifact in artifacts[-80:]
                if isinstance(artifact, dict)
            ],
            "events": [
                {
                    "event_type": event.get("event_type"),
                    "from_status": event.get("from_status"),
                    "to_status": event.get("to_status"),
                    "created_at": event.get("created_at"),
                }
                for event in (task.get("events") or [])[-80:]
                if isinstance(event, dict)
            ],
        },
    }


@router.patch("/tasks/{task_id}")
def kanban_update_task(task_id: str, req: TaskUpdateRequest):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        return update_task(task_id, fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/workflow")
def kanban_task_workflow(task_id: str):
    try:
        return current_workflow_state(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/actions")
def kanban_task_action(task_id: str, req: WorkflowActionRequest):
    try:
        result_cursor = _latest_artifact_created_at(task_id, "workflow_result")
        result = apply_workflow_action(task_id, req.action, req.payload)
        resume_status = str((result.get("task") or {}).get("status_key") or "")
        resume = None if result.get("action_ignored") else _maybe_resume_after_apply_result(
            task_id, req.action, req.payload, result_cursor, resume_status
        )
        if resume is None and not result.get("action_ignored"):
            resume = _maybe_resume_after_retry(task_id, req.action, result_cursor)
        if resume:
            result["workflow_resume"] = resume
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/events")
def kanban_add_event(task_id: str, req: EventCreateRequest):
    if not req.event_type.strip():
        raise HTTPException(status_code=400, detail="event_type is required")
    try:
        return add_event(task_id, req.event_type, req.payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/artifacts")
def kanban_add_artifact(task_id: str, req: ArtifactCreateRequest):
    if not req.artifact_type.strip():
        raise HTTPException(status_code=400, detail="artifact_type is required")
    try:
        return add_artifact(
            task_id,
            artifact_type=req.artifact_type,
            path=req.path,
            payload=req.payload,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _maybe_resume_after_apply_result(
    task_id: str,
    action: str,
    payload: dict[str, Any],
    result_cursor: str | None,
    resume_status: str,
) -> dict[str, Any] | None:
    if str(action or "").strip().lower().replace("-", "_") != "apply_result":
        return None
    verification = payload.get("verification") if isinstance(payload, dict) else None
    apply_failed = not bool(payload.get("ok", True))
    verification_did_fail = verification_has_policy(verification) and verification_failed(verification)
    if not apply_failed and not verification_did_fail:
        return None

    body = _latest_artifact_payload(task_id, "workflow_request_body")
    if not body:
        add_event(task_id, "workflow_resume_skipped", {"reason": "missing_workflow_request_body"})
        return None

    body = dict(body)
    body["task_id"] = task_id
    body["resume_status"] = resume_status
    reason = "apply_failed" if apply_failed else "verification_failed"
    if apply_failed:
        body["client_feedback"] = {
            "kind": "apply_failed",
            "summary": str(payload.get("error_message") or "Client failed to apply generated changes."),
            "changed_paths": payload.get("changed_paths") or [],
        }
    else:
        body["verification_feedback"] = dict(verification or {})
        body["verification_feedback"]["applied_changed_paths"] = payload.get("changed_paths") or []
    body.setdefault("messages", [])

    add_artifact(
        task_id,
        artifact_type="client_feedback" if apply_failed else "verification_feedback",
        payload=body.get("client_feedback") or {"verification": verification},
    )
    add_event(
        task_id,
        "workflow_resume_queued",
        {
            "reason": reason,
            "result_after": result_cursor,
        },
    )
    from app.routes.workflows import _start_workflow_thread  # local import avoids router import cycle

    _start_workflow_thread(task_id, body)
    query = f"?result_after={quote(result_cursor or '')}" if result_cursor else ""
    return {
        "ok": True,
        "reason": reason,
        "poll_url": f"/v1/workflows/{task_id}{query}",
        "events_url": f"/v1/workflows/{task_id}/events{query}",
        "result_after": result_cursor,
    }


def _maybe_resume_after_retry(task_id: str, action: str, result_cursor: str | None) -> dict[str, Any] | None:
    if str(action or "").strip().lower().replace("-", "_") != "retry":
        return None
    body = _latest_artifact_payload(task_id, "workflow_request_body")
    if not body:
        add_event(task_id, "workflow_retry_failed", {"reason": "missing_workflow_request_body"})
        apply_workflow_action(task_id, "fail", {"phase": "retry", "reason": "missing workflow request body"})
        return None

    retry_nonce = str(uuid.uuid4())
    body = dict(body)
    body["task_id"] = task_id
    body["retry_nonce"] = retry_nonce
    body.pop("resume_action", None)
    body.pop("resume_status", None)
    body.pop("client_feedback", None)
    body.pop("verification_feedback", None)
    conversation = get_conversation(task_id, include_messages=False) or {}
    retry_metadata = dict(conversation.get("metadata") or {})
    retry_metadata["retry_nonce"] = retry_nonce
    update_conversation(
        task_id,
        state="queued",
        active_column="draft",
        waiting_for=None,
        metadata=retry_metadata,
    )
    add_event(
        task_id,
        "workflow_retry_queued",
        {"retry_nonce": retry_nonce, "result_after": result_cursor},
    )
    from app.routes.workflows import _start_workflow_thread

    _start_workflow_thread(task_id, body)
    query = f"?result_after={quote(result_cursor or '')}" if result_cursor else ""
    return {
        "ok": True,
        "reason": "retry",
        "retry_nonce": retry_nonce,
        "poll_url": f"/v1/workflows/{task_id}{query}",
        "events_url": f"/v1/workflows/{task_id}/events{query}",
        "result_after": result_cursor,
    }


def _latest_artifact_payload(task_id: str, artifact_type: str) -> dict[str, Any] | None:
    task = get_task(task_id).get("task") or {}
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            payload = artifact.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _latest_artifact_created_at(task_id: str, artifact_type: str) -> str | None:
    try:
        task = get_task(task_id).get("task") or {}
    except KeyError:
        return None
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            return str(artifact.get("created_at") or "") or None
    return None


def _ask_project_conversation_agent(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    current_workflow: dict[str, Any] | None,
    current_agents: dict[str, Any] | None,
    active_task: dict[str, Any] | None,
) -> dict[str, Any]:
    prompt = [
        {
            "role": "system",
            "content": (
                "You are DevWerk's project conversation agent. Return one JSON object only. "
                "You help users create and maintain projects, Kanban workflows, state machines, "
                "agent definitions, and task dispatch. DevWerk is Kanban-driven: executable work "
                "must be executed or continued as a workflow task, not bypassed. Decide one action: reply, "
                "design, save_design, start_task, or continue_task. Use design/save_design when the user asks to "
                "create or change project workflow, columns, state machine, agents, or capabilities. "
                "Use continue_task when the user's message belongs to the active non-terminal task, "
                "for example extra guidance, compile feedback, rework, or follow-up details. Use start_task "
                "when the user begins a distinct work item or the active task is terminal/unrelated. "
                "Support coding and non-coding projects such as writing, research, review, and revision. "
                "JSON shape: {action, reply, save, task_id, task_request, notes}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "project_id": project_id,
                    "current_workflow": current_workflow or {},
                    "current_agents": current_agents or {},
                    "active_task": active_task or None,
                    "conversation": messages[-16:],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        decision = get_llm_client("project").chat_json(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"project LLM agent failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(decision, dict):
        raise HTTPException(status_code=502, detail="project LLM agent returned a non-object response")
    return _normalize_project_agent_decision(decision)


def _normalize_project_agent_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize provider fallbacks so raw JSON text does not leak into chat bubbles."""
    candidate_texts = [
        str(decision.get("raw_text") or "").strip(),
        str(decision.get("reply") or "").strip(),
        str(decision.get("content") or "").strip(),
    ]
    for text in candidate_texts:
        parsed = _extract_project_agent_json(text)
        if parsed is not None:
            decision = {**decision, **parsed}
            break

    reply = str(decision.get("reply") or "").strip()
    parsed_reply = _extract_project_agent_json(reply)
    if parsed_reply is not None:
        decision = {**decision, **parsed_reply}
        reply = str(decision.get("reply") or "").strip()

    if _looks_like_json_payload(reply):
        action = str(decision.get("action") or "reply").strip().lower().replace("-", "_")
        decision["reply"] = _default_project_agent_reply(action)
    return decision


def _extract_project_agent_json(text: str) -> dict[str, Any] | None:
    if not text or "{" not in text:
        return None
    decoder = json.JSONDecoder()
    start = 0
    while True:
        idx = text.find("{", start)
        if idx < 0:
            return None
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            start = idx + 1
            continue
        if isinstance(parsed, dict) and any(key in parsed for key in ("action", "reply", "save", "task_request", "workflow", "agents")):
            return parsed
        start = idx + 1


def _looks_like_json_payload(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and (
        stripped.startswith("{")
        or stripped.startswith("[")
        or ("\"action\"" in stripped and "\"reply\"" in stripped)
    )


def _default_project_agent_reply(action: str) -> str:
    if action in {"start_task", "run_task", "dispatch_task"}:
        return "Starting the workflow task."
    if action in {"continue_task", "resume_task", "message_task"}:
        return "Continuing the active workflow task."
    if action in {"design", "save_design", "revise_workflow", "configure_project"}:
        return "Updating the project workflow design."
    return "Project conversation updated."


def _effective_project_workflow_agents(
    project_id: str,
    current_workflow: dict[str, Any] | None,
    current_agents: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = current_workflow if isinstance(current_workflow, dict) and current_workflow.get("columns") else None
    agents = current_agents if isinstance(current_agents, dict) and current_agents else None
    if workflow is None:
        workflow = get_project_workflow(project_id).get("workflow") or {}
    if agents is None:
        settings_payload = get_project_settings(project_id)
        settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
        agents = settings.get("agents") if isinstance(settings, dict) else {}
    return workflow or {}, agents or {}


def _active_project_task(project_id: str, preferred_task_id: object = None) -> dict[str, Any] | None:
    preferred = str(preferred_task_id or "").strip()
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    events = list_events(project_id=project_id, limit=200).get("events", [])
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        task_id = str(payload.get("task_id") or payload.get("active_task_id") or "").strip()
        if task_id and task_id not in candidates:
            candidates.append(task_id)
    for task_id in candidates:
        try:
            task = get_task(task_id).get("task") or {}
        except KeyError:
            continue
        status_key = str(task.get("status_key") or "")
        if status_key in {"done", "failed"}:
            continue
        if str(task.get("project_id") or "") != str(project_id):
            continue
        return {
            "id": task.get("id"),
            "project_id": task.get("project_id"),
            "title": task.get("title"),
            "description": task.get("description"),
            "status_key": status_key,
            "updated_at": task.get("updated_at"),
        }
    return None


def _handle_project_workflow_design(
    project_id: str,
    *,
    messages: list[dict[str, Any]],
    current_workflow: dict[str, Any] | None,
    current_agents: dict[str, Any] | None,
    save: bool,
    event_kind: str,
) -> dict[str, Any]:
    design_req = WorkflowDesignRequest(
        messages=messages,
        current_workflow=current_workflow,
        current_agents=current_agents,
        save=save,
    )
    result = kanban_design_project_workflow(project_id, design_req)
    add_project_event(
        project_id,
        "project_conversation_message",
        {
            "role": "assistant",
            "content": result.get("reply") or "Workflow draft updated.",
            "kind": event_kind,
            "saved": result.get("saved", False),
        },
    )
    return {"ok": True, "project_id": project_id, "kind": "workflow_design", **result}


def _handle_project_task_dispatch(
    project_id: str,
    *,
    task_message: str,
    workspace: dict[str, Any] | None,
    metadata: dict[str, Any],
    event_kind: str,
) -> dict[str, Any]:
    body = {
        "project_id": project_id,
        "mode": "agent",
        "interaction_mode": "auto",
        "messages": [{"role": "user", "content": task_message}],
        "workspace": workspace
        or {"root_id": project_id, "changed_files": [], "open_files": [], "tree_preview": "", "source_map": None},
        "metadata": {"source": "project_conversation", **metadata},
    }
    from app.routes import workflows as workflow_routes

    result = workflow_routes.start_workflow_payload(body)
    started = bool(result.get("ok", True))
    content = (
        f"Task started: {result.get('task_id') or 'unknown'}"
        if started
        else f"Task dispatch failed: {result.get('error_message') or result.get('error_code') or 'unknown error'}"
    )
    add_project_event(
        project_id,
        "project_conversation_message",
        {
            "role": "assistant",
            "content": content,
            "kind": event_kind,
            "task_id": result.get("task_id"),
            "poll_url": result.get("poll_url"),
            "events_url": result.get("events_url"),
        },
    )
    return {"ok": started, "project_id": project_id, "kind": "task_started", **result}


def _handle_project_task_continue(
    project_id: str,
    *,
    task_id: str,
    task_message: str,
    workspace: dict[str, Any] | None,
    metadata: dict[str, Any],
    event_kind: str,
) -> dict[str, Any]:
    from app.routes import workflows as workflow_routes

    incoming: dict[str, Any] = {
        "action": "message",
        "message": task_message,
        "metadata": {"source": "project_conversation", **metadata},
    }
    if workspace is not None:
        incoming["workspace"] = workspace
    result = workflow_routes.continue_workflow_payload(task_id, incoming)
    ok = bool(result.get("ok", True))
    content = (
        f"Task continued: {task_id}"
        if ok
        else f"Task continuation failed: {result.get('error_message') or result.get('error_code') or 'unknown error'}"
    )
    add_project_event(
        project_id,
        "project_conversation_message",
        {
            "role": "assistant",
            "content": content,
            "kind": event_kind,
            "task_id": task_id,
            "poll_url": result.get("poll_url"),
            "events_url": result.get("events_url"),
        },
    )
    return {"ok": ok, "project_id": project_id, "kind": "task_continued", **result}


def _project_conversation_messages(messages: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    out = [item for item in messages if isinstance(item, dict) and str(item.get("content") or "").strip()]
    text = str(message or "").strip()
    if text and (not out or str(out[-1].get("content") or "").strip() != text):
        out.append({"role": "user", "content": text})
    return out


def _latest_message_content(messages: list[dict[str, Any]], *, role: str) -> str:
    expected = role.strip().lower()
    for message in reversed(messages or []):
        if str(message.get("role") or "").strip().lower() == expected:
            text = str(message.get("content") or "").strip()
            if text:
                return text
    return ""


@ui_router.get("/kanban", response_class=HTMLResponse)
def kanban_ui():
    return HTMLResponse(KANBAN_HTML)


@ui_router.get("/dashboard", response_class=HTMLResponse)
def dashboard_ui():
    return HTMLResponse(DASHBOARD_HTML)


@ui_router.get("/workbench", response_class=HTMLResponse)
def workbench_ui():
    return HTMLResponse(WORKBENCH_HTML)


WORKBENCH_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DevWerk Workbench</title>
  <style>
    :root { color-scheme: light dark; --bg: #f6f7fa; --panel: #fff; --panel-soft: #f0f3f8; --text: #182033; --muted: #687386; --line: #d7deea; --accent: #2068d8; --danger: #b42318; --user: #e7f0ff; --assistant: #fff; }
    @media (prefers-color-scheme: dark) { :root { --bg: #1f2329; --panel: #2b3038; --panel-soft: #242932; --text: #eef3fb; --muted: #aeb8c8; --line: #454d59; --accent: #7ba8ff; --danger: #ff9b91; --user: #243a5f; --assistant: #303641; } }
    * { box-sizing: border-box; }
    html, body { height: 100%; overflow: hidden; }
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    a { color: var(--accent); text-decoration: none; }
    button, input, textarea { font: inherit; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--text); }
    button { height: 34px; padding: 0 12px; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    input { height: 34px; padding: 0 10px; width: 100%; }
    textarea { width: 100%; min-height: 56px; max-height: 220px; padding: 12px 14px; resize: vertical; line-height: 1.45; }
    .app { height: 100vh; min-height: 0; display: grid; grid-template-columns: minmax(220px, var(--sidebar-width, 360px)) 6px minmax(0, 1fr); overflow: hidden; }
    aside { border-right: 1px solid var(--line); background: var(--panel); display: flex; flex-direction: column; min-height: 0; height: 100vh; overflow: hidden; }
    .brand { height: 56px; padding: 0 14px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); gap: 10px; }
    .brand h1 { margin: 0; font-size: 16px; }
    .sidebar-body { padding: 12px; display: grid; gap: 12px; overflow: auto; min-height: 0; align-content: start; }
    .new-project { display: grid; gap: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
    .project-list { display: grid; gap: 6px; }
    .project-card { text-align: left; height: auto; min-height: 58px; padding: 9px 10px; display: grid; gap: 3px; border-color: transparent; background: transparent; }
    .project-card.active { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: color-mix(in srgb, var(--accent) 10%, var(--panel)); }
    .project-card b, .project-card span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .splitter { cursor: col-resize; background: color-mix(in srgb, var(--line) 65%, transparent); min-width: 6px; }
    .splitter:hover, .splitter.dragging { background: color-mix(in srgb, var(--accent) 42%, var(--line)); }
    main { min-width: 0; min-height: 0; height: 100vh; display: grid; grid-template-rows: 56px minmax(0, 1fr) auto; overflow: hidden; }
    .chat-header { border-bottom: 1px solid var(--line); background: var(--panel); display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 18px; }
    .chat-title { min-width: 0; }
    .chat-title b, .chat-title span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .chat-title span { color: var(--muted); font-size: 12px; }
    .messages { min-height: 0; padding: 24px clamp(18px, 5vw, 72px); overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
    .empty { align-self: center; max-width: 640px; margin-top: 12vh; color: var(--muted); text-align: center; }
    .message { max-width: min(760px, 88%); display: grid; gap: 5px; }
    .message.user { align-self: flex-end; }
    .message.assistant { align-self: flex-start; }
    .bubble { border: 1px solid var(--line); border-radius: 16px; padding: 12px 14px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--assistant); }
    .message.user .bubble { background: var(--user); border-color: color-mix(in srgb, var(--accent) 28%, var(--line)); }
    .meta { color: var(--muted); font-size: 12px; padding: 0 4px; }
    .composer { border-top: 1px solid var(--line); background: var(--panel); padding: 14px clamp(18px, 5vw, 72px); display: grid; gap: 8px; }
    .composer-box { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: end; }
    .status-row { min-height: 20px; display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
    .muted { color: var(--muted); }
    .error { color: var(--danger); }
    @media (max-width: 880px) {
      html, body { overflow: auto; }
      .app { height: auto; min-height: 100vh; grid-template-columns: 1fr; grid-template-rows: minmax(220px, 42vh) auto; overflow: visible; }
      .splitter { display: none; }
      aside { min-height: 0; height: auto; max-height: 42vh; border-right: 0; border-bottom: 1px solid var(--line); }
      main { min-height: 58vh; height: 58vh; grid-template-rows: 52px minmax(0, 1fr) auto; }
      .messages { padding: 16px; }
      .composer { padding: 12px 16px; }
      .message { max-width: 96%; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <h1>DevWerk</h1>
        <a href="/dashboard">Dashboard</a>
      </div>
      <div class="sidebar-body">
        <section class="new-project">
          <input id="projectName" placeholder="New project name" />
          <input id="projectId" placeholder="project id (optional)" />
          <button id="createProject" class="primary">New Project</button>
        </section>
        <section>
          <div class="status-row"><span class="muted">Projects</span><button id="refresh">Refresh</button></div>
          <div id="projectList" class="project-list"></div>
        </section>
      </div>
    </aside>
    <div id="splitter" class="splitter" role="separator" aria-orientation="vertical" aria-label="Resize project list"></div>
    <main>
      <header class="chat-header">
        <div class="chat-title">
          <b id="activeProjectName">Project</b>
          <span id="activeProjectId">default</span>
        </div>
        <span id="taskStatus" class="muted"></span>
      </header>
      <div id="messages" class="messages"></div>
      <section class="composer">
        <div class="composer-box">
          <textarea id="prompt" placeholder="Message DevWerk about this project, workflow, or task."></textarea>
          <button id="send" class="primary">Send</button>
        </div>
        <div class="status-row">
          <span id="status" class="muted"></span>
          <span id="error" class="error"></span>
        </div>
      </section>
    </main>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(window.location.search);
    const isNewProjectMode = params.get("new") === "1";
    const initialProjectId = params.get("project_id") || params.get("projectId") || (isNewProjectMode ? createDraftProjectId() : "default");
    const initialProjectName = params.get("project_name") || "";
    const state = { projectId: initialProjectId, projects: [], messages: [], activeTask: null, taskTimer: null, busy: false };

    async function api(path, options = {}) {
      const res = await fetch(path, { ...options, headers: { "Content-Type": "application/json", "X-DevWerk-Project-Id": state.projectId, ...(options.headers || {}) } });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(data.detail || text || `HTTP ${res.status}`);
      return data;
    }

    async function refresh() {
      clearError();
      const data = await api("/v1/kanban/projects");
      state.projects = data.projects || [];
      const selectedExists = state.projects.some(p => p.id === state.projectId);
      if (!selectedExists && !isNewProjectMode) state.projectId = state.projects[0]?.id || "default";
      renderProjects();
      await loadProjectConversation();
    }

    async function createProject() {
      clearError();
      const name = $("projectName").value.trim() || initialProjectName || "Untitled Project";
      const projectId = $("projectId").value.trim() || createDraftProjectId();
      await api("/v1/kanban/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, name }) });
      state.projectId = projectId;
      setWorkbenchUrl(projectId, name);
      $("projectName").value = "";
      $("projectId").value = "";
      await refresh();
      if (!state.messages.length) {
        $("prompt").value = `Create the DevWerk project design for "${name}". Define the workflow, state machine, default agent behavior, context policy, retry/failure behavior, task dispatch rules, and external capabilities.`;
      }
    }

    async function loadProjectConversation() {
      const data = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`);
      state.messages = normalizeMessages(data.messages || []);
      state.activeTask = data.active_task || null;
      renderMessages();
      renderHeader();
    }

    async function sendProjectMessage() {
      clearError();
      const content = $("prompt").value.trim();
      if (!content) return;
      state.messages.push({ role: "user", content, kind: "message" });
      $("prompt").value = "";
      renderMessages();
      setBusy(true);
      try {
        const result = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`, {
          method: "POST",
          body: JSON.stringify({
            action: "message",
            message: content,
            messages: state.messages,
            metadata: { active_task_id: state.activeTask?.id || state.activeTask?.task_id || null }
          })
        });
        if (result.task_id) state.activeTask = { id: result.task_id, status_key: result.status_key || "queued" };
        await loadProjectConversation();
        const hasServerAssistant = result.task_id
          ? state.messages.some(message => message.role === "assistant" && message.task_id === result.task_id)
          : state.messages.some(message => message.role === "assistant" && displayMessageContent(message) === assistantText(result));
        if (!hasServerAssistant) {
          state.messages.push({ role: "assistant", content: assistantText(result), kind: result.kind || "reply", task_id: result.task_id, transient: true });
          state.messages = normalizeMessages(state.messages);
          renderMessages();
        }
        renderHeader();
        if (result.poll_url) {
          pollTask(result.poll_url);
        } else {
          setStatus(result.kind || "Project conversation updated");
        }
      } finally {
        setBusy(false);
      }
    }

    async function pollTask(pollUrl) {
      if (!pollUrl) return;
      if (state.taskTimer) window.clearTimeout(state.taskTimer);
      const tick = async () => {
        try {
          const data = await api(pollUrl);
          const status = data.status_key || data.task?.status_key || "";
          state.activeTask = { id: data.task_id || state.activeTask?.id, status_key: status };
          renderHeader();
          setStatus(`Task ${state.activeTask.id || ""} ${status}`);
          if (data.result || ["done", "failed", "ready_to_apply"].includes(status)) {
            state.messages.push({ role: "assistant", content: `Task update: ${status || "result ready"}`, kind: "task_update", task_id: state.activeTask.id });
            state.messages = normalizeMessages(state.messages);
            renderMessages();
            return;
          }
          state.taskTimer = window.setTimeout(tick, 1800);
        } catch (err) {
          showError(err);
        }
      };
      await tick();
    }

    function renderProjects() {
      const projects = state.projects;
      $("projectList").innerHTML = projects.map(project => `
        <button class="project-card ${project.id === state.projectId ? "active" : ""}" data-project="${escAttr(project.id)}">
          <b>${esc(project.name || project.id)}</b>
          <span class="muted">${esc(project.id)}</span>
        </button>
      `).join("") || `<div class="muted">No projects yet.</div>`;
      renderHeader();
    }

    function renderHeader() {
      const project = state.projects.find(item => item.id === state.projectId) || { id: state.projectId, name: initialProjectName || state.projectId };
      $("activeProjectName").textContent = project.name || project.id || "Project";
      $("activeProjectId").textContent = project.id || state.projectId || "default";
      $("taskStatus").textContent = state.activeTask?.id ? `Task ${state.activeTask.id} ${state.activeTask.status_key || ""}` : "";
    }

    function renderMessages() {
      if (!state.messages.length) {
        $("messages").innerHTML = `<div class="empty">Start with a project goal. DevWerk will design the workflow, maintain the Kanban state machine, and start or continue tasks from the conversation.</div>`;
        return;
      }
      $("messages").innerHTML = state.messages.map(message => `
        <article class="message ${escAttr(message.role || "assistant")}">
          <div class="meta">${esc(message.role || "assistant")}${message.task_id ? ` / task ${esc(message.task_id)}` : ""}</div>
          <div class="bubble">${esc(displayMessageContent(message))}</div>
        </article>
      `).join("");
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function normalizeMessages(messages) {
      const seen = new Set();
      const out = [];
      for (const message of messages) {
        const content = displayMessageContent(message).trim();
        if (!content) continue;
        const key = [
          message.role || "assistant",
          message.kind || "message",
          message.task_id || "",
          content
        ].join("\u0001");
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ ...message, content });
      }
      return out;
    }

    function displayMessageContent(message) {
      const content = String(message?.content ?? "");
      const parsed = parseJsonDecision(content);
      if (parsed) {
        if (parsed.reply && !looksLikeJson(parsed.reply)) return String(parsed.reply);
        if (parsed.action === "start_task") return "Starting the workflow task.";
        if (parsed.action === "continue_task") return "Continuing the active workflow task.";
        if (parsed.action === "save_design" || parsed.action === "design") return "Updating the project workflow design.";
        return "Project conversation updated.";
      }
      return content;
    }

    function parseJsonDecision(text) {
      const value = String(text || "").trim();
      if (!looksLikeJson(value)) return null;
      const start = value.indexOf("{");
      const end = firstJsonObjectEnd(value, start);
      if (start < 0 || end <= start) return null;
      try {
        const parsed = JSON.parse(value.slice(start, end + 1));
        return parsed && typeof parsed === "object" && ("action" in parsed || "reply" in parsed) ? parsed : null;
      } catch (_) {
        return null;
      }
    }

    function firstJsonObjectEnd(text, start) {
      if (start < 0) return -1;
      let depth = 0;
      let inString = false;
      let escaped = false;
      for (let i = start; i < text.length; i += 1) {
        const ch = text[i];
        if (inString) {
          if (escaped) {
            escaped = false;
          } else if (ch === "\\") {
            escaped = true;
          } else if (ch === "\"") {
            inString = false;
          }
          continue;
        }
        if (ch === "\"") {
          inString = true;
        } else if (ch === "{") {
          depth += 1;
        } else if (ch === "}") {
          depth -= 1;
          if (depth === 0) return i;
        }
      }
      return -1;
    }

    function looksLikeJson(value) {
      const text = String(value || "").trim();
      return text.startsWith("{") || text.startsWith("[") || (text.includes('"action"') && text.includes('"reply"'));
    }

    function assistantText(result) {
      if (result.reply) return result.reply;
      if (result.kind === "workflow_design") return result.saved ? "Project workflow updated." : "Workflow draft created.";
      if (result.kind === "task_continued") return `Continuing task ${result.task_id}.`;
      if (result.task_id) return `Task started: ${result.task_id}`;
      return "Project conversation updated.";
    }

    function setBusy(value) {
      state.busy = value;
      $("send").disabled = value;
      $("prompt").disabled = value;
      $("createProject").disabled = value;
      setStatus(value ? "Working..." : "");
    }
    function setStatus(text) { $("status").textContent = text || ""; }
    function clearError() { $("error").textContent = ""; }
    function showError(err) { $("error").textContent = err.message || String(err); setBusy(false); }
    function esc(value) { return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch])); }
    function escAttr(value) { return esc(value).replace(/`/g, "&#96;"); }
    function createDraftProjectId() {
      const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 17);
      return `project-${stamp}`;
    }
    function setWorkbenchUrl(projectId, name) {
      const next = new URL(window.location.href);
      next.searchParams.set("project_id", projectId);
      if (name) next.searchParams.set("project_name", name);
      history.replaceState(null, "", next.toString());
    }

    function initSplitter() {
      const splitter = $("splitter");
      let dragging = false;
      const stored = Number(localStorage.getItem("devwerk.workbench.sidebarWidth") || 0);
      if (stored) setSidebarWidth(stored);
      splitter.addEventListener("pointerdown", event => {
        dragging = true;
        splitter.classList.add("dragging");
        splitter.setPointerCapture(event.pointerId);
      });
      splitter.addEventListener("pointermove", event => {
        if (!dragging) return;
        setSidebarWidth(event.clientX);
      });
      splitter.addEventListener("pointerup", event => {
        if (!dragging) return;
        dragging = false;
        splitter.classList.remove("dragging");
        splitter.releasePointerCapture(event.pointerId);
        localStorage.setItem("devwerk.workbench.sidebarWidth", getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width").trim().replace("px", ""));
      });
      splitter.addEventListener("pointercancel", () => {
        dragging = false;
        splitter.classList.remove("dragging");
      });
    }

    function setSidebarWidth(value) {
      const width = Math.max(220, Math.min(Number(value) || 360, Math.floor(window.innerWidth * 0.55)));
      document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
    }

    $("projectList").onclick = async (event) => {
      const button = event.target.closest("button[data-project]");
      if (!button || state.busy) return;
      state.projectId = button.dataset.project;
      state.messages = [];
      setWorkbenchUrl(state.projectId, "");
      renderProjects();
      await loadProjectConversation().catch(showError);
    };
    $("createProject").onclick = () => createProject().catch(showError);
    $("refresh").onclick = () => refresh().catch(showError);
    $("send").onclick = () => sendProjectMessage().catch(showError);
    $("prompt").addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendProjectMessage().catch(showError);
      }
    });
    if (isNewProjectMode) {
      $("projectName").value = initialProjectName;
      $("projectId").value = initialProjectId;
    }
    initSplitter();
    renderMessages();
    refresh().catch(showError);
  </script>
</body>
</html>
"""


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DevWerk Dashboard</title>
  <style>
    :root { color-scheme: light dark; --bg: #f4f6f8; --panel: #ffffff; --text: #172033; --muted: #667085; --line: #d6dce6; --accent: #1f65d6; --accent-soft: #e9f1ff; --danger: #b42318; }
    @media (prefers-color-scheme: dark) { :root { --bg: #1f2329; --panel: #2a2f37; --text: #eef2f8; --muted: #aab3c2; --line: #444c58; --accent: #7aa7ff; --accent-soft: #263852; --danger: #ff9b91; } }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .shell { min-height: 100vh; display: grid; grid-template-columns: 240px 1fr; }
    .shell.collapsed { grid-template-columns: 64px 1fr; }
    aside { border-right: 1px solid var(--line); background: var(--panel); padding: 12px; overflow: hidden; }
    .brand { display: flex; align-items: center; justify-content: space-between; height: 36px; margin-bottom: 14px; font-weight: 700; }
    .shell.collapsed .brand span, .shell.collapsed nav button span { display: none; }
    nav { display: grid; gap: 6px; }
    nav button, .icon-btn { height: 34px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: var(--text); cursor: pointer; }
    nav button { text-align: left; padding: 0 10px; display: flex; align-items: center; gap: 10px; }
    nav button.active { background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent) 35%, transparent); color: var(--accent); }
    main { min-width: 0; }
    header { min-height: 58px; display: flex; align-items: center; gap: 10px; padding: 10px 18px; border-bottom: 1px solid var(--line); background: var(--panel); flex-wrap: wrap; }
    h1 { margin: 0; font-size: 18px; }
    input, textarea, select, button { font: inherit; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--text); }
    input, select { height: 34px; padding: 0 10px; }
    textarea { width: 100%; min-height: 112px; padding: 9px 10px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size: 12px; }
    button { height: 34px; padding: 0 12px; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .project-select { min-width: 260px; margin-left: auto; }
    .content { padding: 18px; }
    .view { display: none; }
    .view.active { display: block; }
    .toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
    .grid { display: grid; gap: 12px; }
    .stats { grid-template-columns: repeat(4, minmax(140px, 1fr)); }
    .metric, .project-row, .column, .task, .settings-box, .detail-card, .phase-card, .artifact-card { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
    .metric { padding: 12px; }
    .metric b { display: block; font-size: 22px; margin-top: 4px; }
    .muted { color: var(--muted); }
    .error { color: var(--danger); }
    .projects { grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); margin-bottom: 14px; }
    .project-row { padding: 14px; display: grid; gap: 8px; align-content: start; }
    .project-row h3 { margin: 0 0 6px; font-size: 15px; }
    .project-row p { margin: 0; min-height: 20px; color: var(--muted); overflow-wrap: anywhere; }
    .project-row footer { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 4px; padding-top: 10px; border-top: 1px solid var(--line); }
    .project-row footer button { width: 100%; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .board { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(250px, 1fr); gap: 12px; overflow-x: auto; padding-bottom: 12px; }
    .column { min-height: 380px; overflow: hidden; }
    .column h2 { margin: 0; padding: 10px 12px; font-size: 13px; display: flex; justify-content: space-between; border-bottom: 1px solid var(--line); }
    .tasks { display: grid; gap: 8px; padding: 10px; }
    .task { padding: 10px; }
    .task strong { display: block; margin-bottom: 5px; }
    .task p { margin: 0 0 8px; color: var(--muted); white-space: pre-wrap; }
    .task footer { display: flex; gap: 6px; flex-wrap: wrap; }
    .task footer button { height: 28px; padding: 0 8px; font-size: 12px; }
    .detail-layout { display: grid; grid-template-columns: minmax(280px, 360px) 1fr; gap: 12px; align-items: start; }
    .detail-card, .phase-card, .artifact-card { padding: 12px; }
    .detail-card h3, .phase-card h3, .artifact-card h3 { margin: 0 0 8px; font-size: 14px; }
    .detail-card p, .phase-card p { margin: 0 0 8px; white-space: pre-wrap; }
    .detail-section { display: grid; gap: 10px; }
    .phase-grid { display: grid; gap: 10px; }
    .phase-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
    .phase-title { font-weight: 700; }
    .pill { display: inline-flex; align-items: center; min-height: 22px; padding: 0 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
    .kv { display: grid; grid-template-columns: 120px 1fr; gap: 4px 10px; margin: 8px 0; font-size: 12px; }
    .kv b { color: var(--muted); font-weight: 600; }
    .detail-list { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
    .detail-list li { border: 1px solid var(--line); border-radius: 7px; padding: 7px 8px; color: var(--muted); overflow-wrap: anywhere; }
    .detail-pre, .phase-card pre, .artifact-card pre { margin: 8px 0 0; overflow: auto; max-height: 340px; padding: 8px; border-radius: 7px; border: 1px solid var(--line); background: color-mix(in srgb, var(--bg) 80%, var(--panel)); font-size: 12px; }
    .artifact-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
    .events { display: grid; gap: 8px; }
    .event-row { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 10px; }
    .event-head { display: flex; gap: 8px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }
    .event-title { font-weight: 650; }
    .event-meta { color: var(--muted); font-size: 12px; }
    .event-flow { margin-top: 6px; color: var(--muted); }
    .event-row details { margin-top: 8px; }
    .event-row pre { margin: 6px 0 0; overflow: auto; max-height: 260px; padding: 8px; border-radius: 7px; border: 1px solid var(--line); background: color-mix(in srgb, var(--bg) 80%, var(--panel)); font-size: 12px; }
    .memory-grid { grid-template-columns: 1fr 1fr; align-items: start; }
    .memory-box { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 12px; }
    .memory-box h3 { margin: 0 0 10px; font-size: 14px; }
    .memory-list { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
    .memory-list li { border: 1px solid var(--line); border-radius: 7px; padding: 7px 8px; color: var(--muted); }
    .settings-grid { grid-template-columns: repeat(2, minmax(260px, 1fr)); align-items: start; }
    .settings-box { padding: 12px; }
    .settings-box h3 { margin: 0 0 10px; font-size: 14px; }
    .wide { grid-column: 1 / -1; }
    @media (max-width: 900px) { .shell, .shell.collapsed { grid-template-columns: 1fr; } aside { position: sticky; top: 0; z-index: 2; border-right: 0; border-bottom: 1px solid var(--line); } nav { grid-template-columns: repeat(7, 1fr); } .stats, .settings-grid, .memory-grid, .detail-layout { grid-template-columns: 1fr; } .project-select { min-width: 0; width: 100%; margin-left: 0; } .projects { grid-template-columns: 1fr; } .project-row footer { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div id="shell" class="shell">
    <aside>
      <div class="brand"><span>DevWerk</span><button id="collapse" class="icon-btn" title="Toggle sidebar">=</button></div>
      <nav>
        <button data-view="stats" class="active">S <span>Statistics</span></button>
        <button data-view="projects">P <span>Projects</span></button>
        <button data-view="kanban">K <span>Kanban</span></button>
        <button data-view="details">D <span>Details</span></button>
        <button data-view="events">E <span>Events</span></button>
        <button data-view="memory">M <span>Memory</span></button>
        <button data-view="settings">G <span>Settings</span></button>
      </nav>
    </aside>
    <main>
      <header>
        <h1 id="title">Statistics</h1>
        <select id="projectSelect" class="project-select"></select>
        <button id="refresh">Refresh</button>
      </header>
      <section class="content">
        <p id="error" class="error"></p>
        <section id="view-stats" class="view active"><div id="statsGrid" class="grid stats"></div></section>
        <section id="view-projects" class="view">
          <div class="toolbar"><input id="newProjectId" placeholder="new projectId" /><input id="newProjectName" placeholder="Project name" /><button id="createProject" class="primary">New Project</button></div>
          <div id="projectList" class="grid projects"></div>
          <div id="projectSettingsPanel" class="grid settings-grid">
            <div class="settings-box wide"><h3 id="projectSettingsTitle">Project Settings</h3></div>
            <div class="settings-box"><h3>Agents</h3><textarea id="projectAgentsJson"></textarea></div>
            <div class="settings-box"><h3>Parameters</h3><textarea id="projectParametersJson"></textarea></div>
          </div>
          <div class="toolbar" style="margin-top:12px"><button id="saveProjectSettings" class="primary">Save Project Settings</button><button id="resetColumns">Reset Demo Kanban Columns</button></div>
        </section>
        <section id="view-kanban" class="view">
          <div class="toolbar"><input id="taskTitle" placeholder="Task title" /><input id="taskDescription" placeholder="Description" /><button id="createTask" class="primary">Create Task</button></div>
          <div id="board" class="board"></div>
        </section>
        <section id="view-details" class="view">
          <div class="toolbar"><input id="detailTaskId" placeholder="Task id" /><button id="loadTaskDetail" class="primary">Load Task</button><button id="detailRefresh">Refresh Detail</button></div>
          <div id="taskDetail" class="detail-section"><div class="muted">Select a task from Kanban or paste a task id.</div></div>
        </section>
        <section id="view-events" class="view">
          <div class="toolbar"><input id="eventTaskId" placeholder="Filter task id" /><input id="eventLimit" placeholder="Limit" value="200" /><button id="loadEvents" class="primary">Load Events</button></div>
          <div id="eventList" class="events"></div>
        </section>
        <section id="view-memory" class="view">
          <div class="toolbar"><button id="loadMemory" class="primary">Load Memory</button><span id="memoryUpdated" class="muted"></span></div>
          <div class="grid memory-grid">
            <div class="memory-box"><h3>Frameworks</h3><ul id="memoryFrameworks" class="memory-list"></ul></div>
            <div class="memory-box"><h3>Paths</h3><ul id="memoryPaths" class="memory-list"></ul></div>
            <div class="memory-box"><h3>Commands</h3><ul id="memoryCommands" class="memory-list"></ul></div>
            <div class="memory-box"><h3>Recent Phase Summaries</h3><ul id="memorySummaries" class="memory-list"></ul></div>
            <div class="memory-box wide"><h3>Raw Project Memory</h3><textarea id="projectMemoryJson" readonly style="min-height:280px"></textarea></div>
          </div>
        </section>
        <section id="view-settings" class="view">
          <div class="grid settings-grid">
            <div class="settings-box"><h3>LLM Catalog</h3><textarea id="llmsJson" style="min-height:360px"></textarea></div>
            <div class="settings-box"><h3>Routing</h3><textarea id="routingJson" style="min-height:360px"></textarea></div>
          </div>
          <div class="toolbar" style="margin-top:12px"><button id="saveGlobalSettings" class="primary">Save Global Settings</button></div>
        </section>
      </section>
    </main>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    const state = { view: "stats", projects: [], projectId: "default", board: null, events: [], memory: null, taskDetail: null };
    async function api(path, options = {}) {
      const res = await fetch(path, { ...options, headers: { "Content-Type": "application/json", "X-DevWerk-Project-Id": state.projectId, ...(options.headers || {}) } });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(data.detail || text || `HTTP ${res.status}`);
      return data;
    }
    async function refreshAll() { clearError(); await loadProjects(); await Promise.all([loadStats(), loadBoard(), loadEvents(), loadMemory(), loadGlobalSettings(), loadProjectSettings()]); renderActive(); }
    async function loadProjects() {
      const data = await api("/v1/kanban/projects");
      state.projects = data.projects || [];
      if (!state.projects.some(p => p.id === state.projectId)) state.projectId = state.projects[0]?.id || "default";
      const select = $("projectSelect"); select.innerHTML = "";
      for (const p of state.projects) { const option = document.createElement("option"); option.value = p.id; option.textContent = `${p.name || p.id} (${p.id})`; option.selected = p.id === state.projectId; select.appendChild(option); }
      renderProjects();
    }
    async function loadStats() { const usage = await api(`/v1/usage/summary?project_id=${encodeURIComponent(state.projectId)}`); const project = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}`); renderStats(project.project, usage); }
    async function loadBoard() { state.board = await api(`/v1/kanban/board?project_id=${encodeURIComponent(state.projectId)}`); renderBoard(); }
    async function loadEvents() {
      const taskId = $("eventTaskId")?.value.trim() || "";
      const limit = $("eventLimit")?.value.trim() || "200";
      const query = new URLSearchParams({ project_id: state.projectId, limit });
      if (taskId) query.set("task_id", taskId);
      const data = await api(`/v1/kanban/events?${query.toString()}`);
      state.events = data.events || [];
      renderEvents();
    }
    async function loadMemory() {
      const data = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/memory`);
      state.memory = data.memory || {};
      renderMemory();
    }
    async function loadTaskDetail(taskId = "") {
      const id = (taskId || $("detailTaskId").value || "").trim();
      if (!id) { state.taskDetail = null; renderTaskDetail(); return; }
      const data = await api(`/v1/kanban/tasks/${encodeURIComponent(id)}`);
      state.taskDetail = data.task || {};
      $("detailTaskId").value = id;
      renderTaskDetail();
    }
    async function loadGlobalSettings() {
      const data = await api("/v1/settings"); const s = data.settings || {};
      $("llmsJson").value = JSON.stringify(s.llms || {}, null, 2);
      $("routingJson").value = JSON.stringify(s.routing || {}, null, 2);
    }
    async function loadProjectSettings() {
      const data = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/settings`);
      $("projectSettingsTitle").textContent = `Project Settings: ${state.projectId}`;
      $("projectAgentsJson").value = JSON.stringify(data.settings.agents || {}, null, 2);
      $("projectParametersJson").value = JSON.stringify(data.settings.parameters || {}, null, 2);
    }
    function renderStats(project, usage) {
      const s = project.stats || {}; const rows = [["Requests", usage.request_count ?? s.request_count ?? 0], ["LLM Calls", s.llm_calls ?? 0], ["Input Tokens", s.input_tokens ?? 0], ["Output Tokens", s.output_tokens ?? 0], ["Total Tokens", s.total_tokens ?? 0], ["Cached Input", s.cached_input_tokens ?? 0], ["Tasks", s.tasks ?? 0], ["Duration ms", s.duration_ms ?? 0]];
      $("statsGrid").innerHTML = rows.map(([label, value]) => `<div class="metric"><span class="muted">${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("");
    }
    function renderProjects() {
      $("projectList").innerHTML = state.projects.map(p => { const s = p.stats || {}; return `<article class="project-row"><h3>${escapeHtml(p.name || p.id)}</h3><div class="muted">${escapeHtml(p.id)}</div><p>${escapeHtml(p.description || "")}</p><div class="muted">tasks ${s.tasks || 0} / requests ${s.request_count || 0} / tokens ${s.total_tokens || 0}</div><footer><button data-project="${escapeAttr(p.id)}" data-action="open-kanban">Kanban</button><button data-project="${escapeAttr(p.id)}" data-action="design-project">Workflow</button><button data-project="${escapeAttr(p.id)}" data-action="open-project-settings">Settings</button></footer></article>`; }).join("");
    }
    function renderBoard() {
      const data = state.board; if (!data) return;
      $("board").innerHTML = data.columns.map(col => `<article class="column"><h2><span>${escapeHtml(col.title)}</span><span class="muted">${col.tasks.length}</span></h2><div class="tasks">${col.tasks.map(task => renderTask(task, col, data.columns)).join("")}</div></article>`).join("");
    }
    function renderTask(task, col, columns) {
      const actions = [];
      actions.push(`<button data-task="${escapeAttr(task.id)}" data-action="detail">Details</button>`);
      if (task.status_key === "failed") actions.push(`<button data-task="${escapeAttr(task.id)}" data-action="retry">Retry</button>`);
      if (!["done", "failed"].includes(task.status_key)) actions.push(`<button data-task="${escapeAttr(task.id)}" data-action="abandon">Abandon</button>`);
      return `<article class="task"><strong>${escapeHtml(task.title)}</strong><p>${escapeHtml(task.description || "")}</p><small class="muted">${escapeHtml(task.status_key)} / ${escapeHtml(task.id)}</small><footer>${actions.join("")}</footer></article>`;
    }
    function renderTaskDetail() {
      const task = state.taskDetail;
      if (!task) {
        $("taskDetail").innerHTML = `<div class="muted">Select a task from Kanban or paste a task id.</div>`;
        return;
      }
      const artifacts = task.artifacts || [];
      const events = task.events || [];
      const conversation = task.conversation || {};
      const columnRuns = task.column_runs || [];
      const revisions = task.revisions || [];
      const phaseOutputs = artifacts
        .filter(a => a.artifact_type === "workflow_phase_output" && a.payload)
        .map(a => ({ ...a.payload, created_at: a.created_at }));
      const keyArtifacts = latestArtifacts(artifacts, [
        "code_context_summary",
        "context_bundle",
        "plan_bundle",
        "code_change_bundle",
        "review_bundle",
        "workflow_result",
        "apply_result",
        "agent_message"
      ]);
      const paths = collectTaskPaths(artifacts);
      $("taskDetail").innerHTML = `
        <div class="detail-layout">
          <aside class="detail-card">
            <h3>${escapeHtml(task.title || task.id)}</h3>
            <p class="muted">${escapeHtml(task.description || "")}</p>
            <div class="kv">
              <b>Status</b><span>${escapeHtml(task.status_key || "")}</span>
              <b>Task</b><span>${escapeHtml(task.id || "")}</span>
              <b>Project</b><span>${escapeHtml(task.project_id || "")}</span>
              <b>Priority</b><span>${escapeHtml(task.priority ?? 0)}</span>
              <b>Created</b><span>${escapeHtml(task.created_at || "")}</span>
              <b>Updated</b><span>${escapeHtml(task.updated_at || "")}</span>
              <b>Conversation</b><span>${escapeHtml(conversation.state || "-")}</span>
              <b>Waiting For</b><span>${escapeHtml(conversation.waiting_for || "-")}</span>
            </div>
            <h3>Changed / Planned Paths</h3>
            <ul class="detail-list">${paths.map(p => `<li>${escapeHtml(p)}</li>`).join("") || "<li>No paths recorded</li>"}</ul>
          </aside>
          <section class="detail-section">
            <div class="detail-card">
              <h3>Conversation</h3>
              <div class="kv"><b>Summary Version</b><span>${escapeHtml(conversation.summary_version ?? 0)}</span><b>Token Estimate</b><span>${escapeHtml(conversation.token_estimate ?? 0)}</span></div>
              <details><summary>Rolling Summary</summary><pre>${escapeHtml(conversation.summary || "No compressed summary")}</pre></details>
              <div class="events">${(conversation.messages || []).slice().reverse().map(message => `<article class="event-row"><div class="event-head"><span class="event-title">${escapeHtml(message.role)} / ${escapeHtml(message.message_type)}</span><span class="event-meta">#${escapeHtml(message.sequence)}</span></div><div>${escapeHtml(message.content)}</div></article>`).join("") || `<div class="muted">No conversation messages</div>`}</div>
            </div>
            <div class="detail-card">
              <h3>Column Runs</h3>
              <div class="artifact-grid">${columnRuns.map(run => `<article class="artifact-card"><div class="phase-head"><h3>${escapeHtml(run.status_key)} / ${escapeHtml(run.agent)}</h3><span class="pill">${escapeHtml(run.state)}</span></div><div class="kv"><b>Run</b><span>${escapeHtml(run.run_no)}</span><b>Started</b><span>${escapeHtml(run.created_at)}</span></div><pre>${escapeHtml(prettyJson(run.checkpoint || {}))}</pre></article>`).join("") || `<div class="muted">No column runs</div>`}</div>
            </div>
            <div class="detail-card">
              <h3>Candidate Revisions</h3>
              <div class="artifact-grid">${revisions.slice().reverse().map(revision => `<article class="artifact-card"><div class="phase-head"><h3>Revision ${escapeHtml(revision.sequence)}</h3><span class="pill">${escapeHtml(revision.state)}</span></div><p>${escapeHtml(revision.summary || "")}</p><pre>${escapeHtml(prettyJson({changed_paths: revision.changed_paths, verification: revision.verification}))}</pre></article>`).join("") || `<div class="muted">No revisions</div>`}</div>
            </div>
            <div class="detail-card">
              <h3>Agent Phases</h3>
              <div class="phase-grid">${phaseOutputs.map(renderPhaseOutput).join("") || `<div class="muted">No phase outputs recorded</div>`}</div>
            </div>
            <div class="detail-card">
              <h3>Artifacts</h3>
              <div class="artifact-grid">${keyArtifacts.map(renderArtifact).join("") || `<div class="muted">No artifacts recorded</div>`}</div>
            </div>
            <div class="detail-card">
              <h3>Task Events</h3>
              <div class="events">${events.slice().reverse().map(renderEventRow).join("") || `<div class="muted">No events</div>`}</div>
            </div>
          </section>
        </div>
      `;
    }
    function renderPhaseOutput(phase) {
      const warnings = phase.warnings || [];
      return `<article class="phase-card">
        <div class="phase-head"><span class="phase-title">${escapeHtml(phase.phase || "phase")} / ${escapeHtml(phase.agent || "agent")}</span><span class="pill">${escapeHtml(phase.status_key || "")}</span></div>
        <p>${escapeHtml(phase.summary || "")}</p>
        <div class="kv">
          <b>Decision</b><span>${escapeHtml(phase.decision || "-")}</span>
          <b>Next</b><span>${escapeHtml(phase.next_action || "-")}</span>
          <b>Session</b><span>${escapeHtml(phase.session_id || "-")}</span>
          <b>Created</b><span>${escapeHtml(phase.created_at || "-")}</span>
        </div>
        ${warnings.length ? `<ul class="detail-list">${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>` : ""}
        <details><summary>Inputs</summary><pre>${escapeHtml(prettyJson(phase.inputs || {}))}</pre></details>
        <details><summary>Outputs</summary><pre>${escapeHtml(prettyJson(phase.outputs || {}))}</pre></details>
      </article>`;
    }
    function renderArtifact(artifact) {
      return `<article class="artifact-card">
        <div class="phase-head"><h3>${escapeHtml(artifact.artifact_type)}</h3><span class="pill">${escapeHtml(artifact.created_at || "")}</span></div>
        <pre>${escapeHtml(prettyJson(artifact.payload || {}))}</pre>
      </article>`;
    }
    function renderEventRow(event) {
      const payload = JSON.stringify(event.payload || {}, null, 2);
      const flow = event.from_status || event.to_status ? `${event.from_status || "-"} -> ${event.to_status || "-"}` : "no status change";
      const task = event.task_title ? `${event.task_title} / ${event.task_id}` : event.task_id;
      return `<article class="event-row"><div class="event-head"><span class="event-title">${escapeHtml(event.event_type)}</span><span class="event-meta">${escapeHtml(event.created_at)}</span></div><div class="event-flow">${escapeHtml(flow)}</div><div class="event-meta">${escapeHtml(task || "")}</div><details><summary>Payload</summary><pre>${escapeHtml(payload)}</pre></details></article>`;
    }
    function renderEvents() {
      const rows = state.events || [];
      $("eventList").innerHTML = rows.map(renderEventRow).join("") || `<div class="muted">No events</div>`;
    }
    function renderMemory() {
      const memory = state.memory || {};
      $("memoryUpdated").textContent = memory.updated_at ? `Updated ${memory.updated_at}` : "No project memory yet";
      renderMemoryList("memoryFrameworks", memory.frameworks || [], item => item);
      renderMemoryList("memoryPaths", (memory.paths || []).slice(-30), item => item);
      renderMemoryList("memoryCommands", (memory.commands || []).slice(-20), item => item);
      renderMemoryList("memorySummaries", (memory.phase_summaries || []).slice(-20).reverse(), item => `${item.phase || ""} / ${item.status_key || ""} / ${item.summary || ""}`);
      $("projectMemoryJson").value = JSON.stringify(memory, null, 2);
    }
    function renderMemoryList(id, rows, labelFn) {
      $(id).innerHTML = rows.map(item => `<li>${escapeHtml(labelFn(item))}</li>`).join("") || `<li>No data</li>`;
    }
    function renderActive() { document.querySelectorAll(".view").forEach(v => v.classList.remove("active")); document.querySelector(`#view-${state.view}`).classList.add("active"); document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.view === state.view)); $("title").textContent = { stats: "Statistics", projects: "Projects", kanban: "Kanban", details: "Task Details", events: "Events", memory: "Memory", settings: "Settings" }[state.view]; }
    function openWorkbench(projectId, name, isNew = false) {
      const query = new URLSearchParams({ project_id: projectId });
      if (name) query.set("project_name", name);
      if (isNew) query.set("new", "1");
      window.location.href = `/workbench?${query.toString()}`;
    }
    function createDraftProjectId() {
      const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 17);
      return `project-${stamp}`;
    }
    async function createProject() { const typedProjectId = $("newProjectId").value.trim(); const projectId = typedProjectId || createDraftProjectId(); const name = $("newProjectName").value.trim() || (typedProjectId ? projectId : "Untitled Project"); state.projectId = projectId; openWorkbench(projectId, name, true); }
    async function createTask() { const title = $("taskTitle").value.trim(); if (!title) return; await api("/v1/kanban/tasks", { method: "POST", body: JSON.stringify({ project_id: state.projectId, title, description: $("taskDescription").value.trim() }) }); $("taskTitle").value = ""; $("taskDescription").value = ""; await refreshAll(); }
    async function saveGlobalSettings() { await api("/v1/settings", { method: "PUT", body: JSON.stringify({ llms: JSON.parse($("llmsJson").value || "{}"), routing: JSON.parse($("routingJson").value || "{}") }) }); await refreshAll(); }
    async function saveProjectSettings() { await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/settings`, { method: "PUT", body: JSON.stringify({ agents: JSON.parse($("projectAgentsJson").value || "{}"), parameters: JSON.parse($("projectParametersJson").value || "{}") }) }); await refreshAll(); }
    async function resetColumns() {
      await api("/v1/kanban/columns", { method: "PUT", body: JSON.stringify({ project_id: state.projectId, columns: [ { status_key: "draft", title: "Draft", position: 10, transition_to: ["context_indexed", "failed"] }, { status_key: "context_indexed", title: "Context Indexed", position: 20, transition_to: ["planned", "failed"] }, { status_key: "planned", title: "Planned", position: 30, transition_to: ["coding", "draft", "failed"] }, { status_key: "coding", title: "Coding", position: 40, transition_to: ["reviewed", "planned", "failed"] }, { status_key: "reviewed", title: "Reviewed", position: 45, transition_to: ["ready_to_apply", "coding", "planned", "failed"] }, { status_key: "ready_to_apply", title: "Ready To Apply", position: 50, transition_to: ["applied", "coding", "failed"] }, { status_key: "applied", title: "Applied", position: 60, transition_to: ["verified", "coding", "planned", "failed"] }, { status_key: "verified", title: "Verified", position: 70, transition_to: ["done", "applied", "failed"] }, { status_key: "done", title: "Done", position: 80, transition_to: [] }, { status_key: "failed", title: "Failed", position: 90, transition_to: ["draft"] } ] }) });
      await refreshAll();
    }
    document.querySelector("nav").onclick = (event) => { const btn = event.target.closest("button[data-view]"); if (!btn) return; state.view = btn.dataset.view; renderActive(); };
    $("collapse").onclick = () => $("shell").classList.toggle("collapsed");
    $("refresh").onclick = () => refreshAll().catch(showError);
    $("projectSelect").onchange = async (event) => { state.projectId = event.target.value; await refreshAll().catch(showError); };
    $("createProject").onclick = () => createProject().catch(showError);
    $("createTask").onclick = () => createTask().catch(showError);
    $("saveGlobalSettings").onclick = () => saveGlobalSettings().catch(showError);
    $("saveProjectSettings").onclick = () => saveProjectSettings().catch(showError);
    $("resetColumns").onclick = () => resetColumns().catch(showError);
    $("loadEvents").onclick = () => loadEvents().catch(showError);
    $("loadMemory").onclick = () => loadMemory().catch(showError);
    $("loadTaskDetail").onclick = () => loadTaskDetail().catch(showError);
    $("detailRefresh").onclick = () => loadTaskDetail().catch(showError);
    $("projectList").onclick = async (event) => { const btn = event.target.closest("button[data-project]"); if (!btn) return; state.projectId = btn.dataset.project; if (btn.dataset.action === "design-project") { const project = state.projects.find(p => p.id === state.projectId) || {}; openWorkbench(state.projectId, project.name || state.projectId, false); return; } state.view = btn.dataset.action === "open-project-settings" ? "projects" : "kanban"; await refreshAll().catch(showError); };
    $("board").onclick = async (event) => {
      const btn = event.target.closest("button[data-task]");
      if (!btn) return;
      if (btn.dataset.action === "detail") { state.view = "details"; renderActive(); await loadTaskDetail(btn.dataset.task).catch(showError); return; }
      if (btn.dataset.action === "retry") await api(`/v1/kanban/tasks/${btn.dataset.task}/actions`, { method: "POST", body: JSON.stringify({ action: "retry", payload: { reason: "user_requested_retry" } }) });
      if (btn.dataset.action === "abandon") await api(`/v1/kanban/tasks/${btn.dataset.task}/actions`, { method: "POST", body: JSON.stringify({ action: "abandon", payload: { reason: "user_abandoned_task" } }) });
      await refreshAll();
    };
    function latestArtifacts(artifacts, types) {
      const out = [];
      for (const type of types) {
        const matches = artifacts.filter(a => a.artifact_type === type);
        if (matches.length) out.push(matches[matches.length - 1]);
      }
      return out;
    }
    function collectTaskPaths(artifacts) {
      const paths = new Set();
      for (const artifact of artifacts || []) {
        const payload = artifact.payload || {};
        const files = payload.files || payload.outputs?.files || [];
        if (Array.isArray(files)) for (const item of files) if (item?.path) paths.add(item.path);
        const ops = payload.ops || payload.outputs?.ops || [];
        if (Array.isArray(ops)) for (const item of ops) if (item?.path) paths.add(item.path);
        const changed = payload.changed_paths || payload.outputs?.changed_files || payload.changed_files || [];
        if (Array.isArray(changed)) for (const item of changed) paths.add(String(item));
        const planFiles = payload.plan_files || [];
        if (Array.isArray(planFiles)) for (const item of planFiles) paths.add(String(item));
      }
      return [...paths].sort();
    }
    function prettyJson(value) { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }
    function clearError() { $("error").textContent = ""; }
    function showError(err) { $("error").textContent = err.message || String(err); }
    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch])); }
    function escapeAttr(value) { return escapeHtml(value).replace(/`/g, "&#96;"); }
    refreshAll().catch(showError);
  </script>
</body>
</html>
"""
KANBAN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DevWerk Kanban</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1d232f;
      --muted: #667085;
      --line: #d8dde7;
      --accent: #2864c7;
      --danger: #b42318;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #202327;
        --panel: #2a2e34;
        --text: #eef1f6;
        --muted: #aab2c0;
        --line: #414852;
        --accent: #7aa7ff;
        --danger: #ff9b91;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { font-size: 17px; margin: 0; font-weight: 650; }
    input, textarea, button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
    }
    input { height: 34px; padding: 0 10px; min-width: 260px; }
    button { height: 34px; padding: 0 12px; cursor: pointer; }
    button.primary { background: var(--accent); color: white; border-color: var(--accent); }
    main { padding: 16px; }
    .composer {
      display: grid;
      grid-template-columns: minmax(220px, 320px) 1fr auto;
      gap: 8px;
      margin-bottom: 14px;
    }
    .composer textarea {
      min-height: 34px;
      max-height: 90px;
      resize: vertical;
      padding: 7px 10px;
    }
    .board {
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(250px, 1fr);
      gap: 12px;
      overflow-x: auto;
      padding-bottom: 12px;
    }
    .column {
      min-height: 360px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: color-mix(in srgb, var(--panel) 88%, var(--bg));
      display: flex;
      flex-direction: column;
    }
    .column h2 {
      margin: 0;
      padding: 10px 12px;
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      border-bottom: 1px solid var(--line);
    }
    .cards { padding: 10px; display: grid; gap: 8px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
    }
    .card strong { display: block; margin-bottom: 5px; }
    .card p { margin: 0 0 8px; color: var(--muted); white-space: pre-wrap; }
    .card footer { display: flex; gap: 6px; flex-wrap: wrap; }
    .card footer button { height: 28px; padding: 0 8px; font-size: 12px; }
    .muted { color: var(--muted); }
    .error { color: var(--danger); margin-left: auto; }
    @media (max-width: 760px) {
      .composer { grid-template-columns: 1fr; }
      input { min-width: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <h1>DevWerk Kanban</h1>
    <input id="projectId" placeholder="projectId" value="default" />
    <button id="refresh">Refresh</button>
    <span id="status" class="muted"></span>
    <span id="error" class="error"></span>
  </header>
  <main>
    <section class="composer">
      <input id="title" placeholder="Task title" />
      <textarea id="description" placeholder="Description"></textarea>
      <button id="create" class="primary">Create</button>
    </section>
    <section id="board" class="board"></section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const board = $("board");
    const statusEl = $("status");
    const errorEl = $("error");

    async function api(path, options = {}) {
      const projectId = $("projectId").value.trim() || "default";
      const res = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          "X-DevWerk-Project-Id": projectId,
          ...(options.headers || {})
        }
      });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(data.detail || text || `HTTP ${res.status}`);
      return data;
    }

    async function loadBoard() {
      errorEl.textContent = "";
      statusEl.textContent = "Loading...";
      const projectId = $("projectId").value.trim() || "default";
      const data = await api(`/v1/kanban/board?project_id=${encodeURIComponent(projectId)}`);
      render(data);
      statusEl.textContent = `${data.columns.reduce((n, c) => n + c.tasks.length, 0)} tasks`;
    }

    function render(data) {
      board.innerHTML = "";
      for (const col of data.columns) {
        const el = document.createElement("article");
        el.className = "column";
        el.innerHTML = `<h2><span>${escapeHtml(col.title)}</span><span class="muted">${col.tasks.length}</span></h2>`;
        const cards = document.createElement("div");
        cards.className = "cards";
        for (const task of col.tasks) cards.appendChild(renderTask(task, col, data.columns));
        el.appendChild(cards);
        board.appendChild(el);
      }
    }

    function renderTask(task, col, columns) {
      const card = document.createElement("div");
      card.className = "card";
      const footer = document.createElement("footer");
      if (task.status_key === "failed") {
        const btn = document.createElement("button");
        btn.textContent = "Retry";
        btn.onclick = async () => {
          await api(`/v1/kanban/tasks/${task.id}/actions`, { method: "POST", body: JSON.stringify({ action: "retry", payload: { reason: "user_requested_retry" } }) });
          await loadBoard();
        };
        footer.appendChild(btn);
      }
      if (!["done", "failed"].includes(task.status_key)) {
        const btn = document.createElement("button");
        btn.textContent = "Abandon";
        btn.onclick = async () => {
          await api(`/v1/kanban/tasks/${task.id}/actions`, { method: "POST", body: JSON.stringify({ action: "abandon", payload: { reason: "user_abandoned_task" } }) });
          await loadBoard();
        };
        footer.appendChild(btn);
      }
      card.innerHTML = `
        <strong>${escapeHtml(task.title)}</strong>
        <p>${escapeHtml(task.description || "")}</p>
        <small class="muted">${escapeHtml(task.id)}</small>
      `;
      card.appendChild(footer);
      return card;
    }

    async function createTask() {
      const title = $("title").value.trim();
      if (!title) return;
      await api("/v1/kanban/tasks", {
        method: "POST",
        body: JSON.stringify({
          project_id: $("projectId").value.trim() || "default",
          title,
          description: $("description").value.trim()
        })
      });
      $("title").value = "";
      $("description").value = "";
      await loadBoard();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      }[ch]));
    }

    $("refresh").onclick = () => loadBoard().catch(showError);
    $("create").onclick = () => createTask().catch(showError);
    $("projectId").addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadBoard().catch(showError);
    });
    function showError(err) {
      errorEl.textContent = err.message || String(err);
      statusEl.textContent = "";
    }
    loadBoard().catch(showError);
  </script>
</body>
</html>
"""
