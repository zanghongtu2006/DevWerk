from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

from app.routes.web_ui import render_web_ui
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
from app.services.memory_system import read_task_memory
from app.services.session_store import read_project_memory, record_project_memory
from app.services.skill_manager import (
    effective_skill_catalog,
    get_project_skill,
    list_project_skills,
    upsert_project_skill,
)
from app.services.plugin_manager import get_plugin_command, list_enabled_plugin_commands
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


class ProjectMdRequest(BaseModel):
    content: str = ""


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


class ProjectSkillRequest(BaseModel):
    skill_md: str = ""
    enabled: bool = True
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


@router.websocket("/projects/{project_id}/stream")
async def kanban_project_stream(websocket: WebSocket, project_id: str):
    await websocket.accept()
    seen: set[str] = set()
    try:
        initial_events = list_events(project_id=project_id, limit=80).get("events", [])
        seen.update(str(event.get("id") or "") for event in initial_events if event.get("id"))
        await websocket.send_json(
            {
                "type": "snapshot",
                "project_id": project_id,
                "board": get_board(project_id),
                "events": initial_events,
            }
        )
        while True:
            events = list_events(project_id=project_id, limit=80).get("events", [])
            unseen = [event for event in reversed(events) if str(event.get("id") or "") not in seen]
            if unseen:
                seen.update(str(event.get("id") or "") for event in events if event.get("id"))
                await websocket.send_json(
                    {
                        "type": "events",
                        "project_id": project_id,
                        "events": unseen,
                        "board": get_board(project_id),
                    }
                )
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


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


@router.get("/projects/{project_id}/project-md")
def kanban_get_project_md(project_id: str):
    return {"ok": True, "project_id": project_id, "content": _project_md(project_id)}


@router.put("/projects/{project_id}/project-md")
def kanban_update_project_md(project_id: str, req: ProjectMdRequest):
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = dict(settings.get("parameters") or {}) if isinstance(settings, dict) else {}
    parameters["project_md"] = req.content
    update_project_settings(project_id, parameters=parameters)
    add_project_event(
        project_id,
        "project_md_updated",
        {"summary": "Project.MD updated", "chars": len(req.content or "")},
    )
    return kanban_get_project_md(project_id)


@router.get("/projects/{project_id}/skills")
def kanban_get_project_skills(project_id: str):
    return {
        "ok": True,
        "project_id": project_id,
        "entrypoint": "SKILL.md",
        "skills": list_project_skills(project_id),
        "catalog": effective_skill_catalog(project_id),
    }


@router.get("/projects/{project_id}/skills/{skill_id}")
def kanban_get_project_skill(project_id: str, skill_id: str):
    try:
        return {"ok": True, "project_id": project_id, "skill": get_project_skill(project_id, skill_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/projects/{project_id}/skills/{skill_id}")
def kanban_put_project_skill(project_id: str, skill_id: str, req: ProjectSkillRequest):
    try:
        skill = upsert_project_skill(
            project_id,
            skill_id,
            req.skill_md,
            enabled=req.enabled,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_project_event(project_id, "project_skill_updated", {"skill_id": skill.get("id"), "enabled": skill.get("enabled")})
    return {"ok": True, "project_id": project_id, "skill": skill}


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


@router.get("/projects/{project_id}/slash-commands")
def kanban_project_slash_commands(project_id: str):
    builtin = [
        {
            "command": "/goal",
            "id": "goal",
            "source": "builtin",
            "summary": "Record or update the project goal in Project.MD.",
            "argument_hint": "project objective",
        },
        {
            "command": "/learn",
            "id": "learn",
            "source": "builtin",
            "summary": "Record reusable project knowledge in Project.MD and project memory.",
            "argument_hint": "reusable rule",
        },
        {
            "command": "/distill",
            "id": "distill",
            "source": "builtin",
            "summary": "Compact recent project conversation into Project.MD and project memory.",
            "argument_hint": "compact this project context",
        },
    ]
    plugin_commands = []
    for item in list_enabled_plugin_commands():
        if not isinstance(item, dict):
            continue
        frontmatter = item.get("frontmatter") if isinstance(item.get("frontmatter"), dict) else {}
        plugin_commands.append(
            {
                "command": str(item.get("slash") or f"/{item.get('command_id')}"),
                "id": str(item.get("command_id") or ""),
                "source": "plugin",
                "plugin_id": str(item.get("plugin_id") or ""),
                "summary": str(item.get("summary") or item.get("id") or item.get("command_id") or ""),
                "argument_hint": str(item.get("argument_hint") or frontmatter.get("argument-hint") or ""),
                "allowed_tools": item.get("allowed_tools") if isinstance(item.get("allowed_tools"), list) else [],
                "model": str(item.get("model") or ""),
                "frontmatter": frontmatter,
            }
        )
    return {"ok": True, "project_id": project_id, "commands": [*builtin, *plugin_commands]}


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

    slash = _parse_project_slash_command(user_text)
    if slash is not None and action in {"message", "send"}:
        return _handle_project_slash_command(
            project_id,
            command=slash["command"],
            argument=slash["argument"],
            messages=messages,
            metadata={**req.metadata, "plugin_command": slash.get("plugin_command")},
        )

    if action in {"goal", "learn", "distill"}:
        return _handle_project_slash_command(
            project_id,
            command=action,
            argument=user_text,
            messages=messages,
            metadata=req.metadata,
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
    structured_memory = read_task_memory(task_id)
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
            "structured": structured_memory,
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
    retry_status = str((get_task(task_id).get("task") or {}).get("status_key") or "")
    update_conversation(
        task_id,
        state="queued",
        active_column=retry_status or None,
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


def _parse_project_slash_command(text: str) -> dict[str, str] | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    command = head.strip().lower().replace("-", "_")
    if command not in {"goal", "learn", "distill"}:
        plugin_command = head.strip().lower()
        if ":" in plugin_command or "." in plugin_command:
            return {"command": "plugin_command", "plugin_command": plugin_command, "argument": rest.strip()}
        raise HTTPException(status_code=400, detail=f"unsupported slash command: /{command}")
    return {"command": command, "argument": rest.strip()}


def _handle_project_slash_command(
    project_id: str,
    *,
    command: str,
    argument: str,
    messages: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    normalized = str(command or "").strip().lower().replace("-", "_")
    if normalized == "goal":
        if not argument.strip():
            raise HTTPException(status_code=400, detail="/goal requires goal text")
        updated = _upsert_project_md_section(project_id, "Project Goal", argument.strip())
        reply = "Project goal recorded in Project.MD."
        payload = {"command": "goal", "goal": argument.strip(), "project_md_chars": len(updated)}
    elif normalized == "learn":
        if not argument.strip():
            raise HTTPException(status_code=400, detail="/learn requires a note to learn")
        updated = _append_project_md_bullet(project_id, "Learned Notes", argument.strip())
        record_project_memory(
            project_id=project_id,
            task_id="project-command",
            phase_output={
                "phase": "project_conversation",
                "agent": "project-agent",
                "status_key": "project",
                "summary": argument.strip(),
                "inputs": {"slash_command": "/learn"},
                "outputs": {"rules": [argument.strip()]},
                "warnings": [],
                "decision": "approve",
                "next_action": "reply",
            },
        )
        reply = "Project learning recorded in Project.MD and project memory."
        payload = {"command": "learn", "note": argument.strip(), "project_md_chars": len(updated)}
    elif normalized == "distill":
        distillation = _distill_project_conversation(project_id, messages, argument)
        updated = _upsert_project_md_section(project_id, "Distilled Context", distillation)
        record_project_memory(
            project_id=project_id,
            task_id="project-command",
            phase_output={
                "phase": "project_conversation",
                "agent": "project-agent",
                "status_key": "project",
                "summary": distillation,
                "inputs": {"slash_command": "/distill"},
                "outputs": {"rules": [distillation]},
                "warnings": [],
                "decision": "approve",
                "next_action": "reply",
            },
        )
        reply = "Project conversation distilled into Project.MD and project memory."
        payload = {"command": "distill", "summary": distillation, "project_md_chars": len(updated)}
    elif normalized == "plugin_command":
        payload = _handle_project_plugin_command_payload(
            project_id,
            command_id=str(metadata.get("plugin_command") or ""),
            argument=argument,
            metadata=metadata,
        )
        reply = payload["reply"]
    else:
        raise HTTPException(status_code=400, detail=f"unsupported slash command: /{normalized}")

    add_project_event(project_id, "project_slash_command", {"command": normalized, **payload, "metadata": metadata})
    add_project_event(
        project_id,
        "project_conversation_message",
        {"role": "assistant", "content": reply, "kind": f"slash_{normalized}", **payload},
    )
    return {
        "ok": True,
        "project_id": project_id,
        "kind": "slash_command",
        "command": normalized,
        "reply": reply,
        "payload": payload,
    }


def _handle_project_plugin_command_payload(
    project_id: str,
    *,
    command_id: str,
    argument: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    try:
        command = get_plugin_command(command_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    summary = command.get("summary") or command.get("id") or command_id
    content = str(command.get("content") or "")
    instructions = str(command.get("body") or content)
    payload = {
        "command": command.get("command_id") or command_id,
        "plugin_id": command.get("plugin_id"),
        "command_id": command.get("id"),
        "argument": argument.strip(),
        "summary": summary,
        "frontmatter": command.get("frontmatter") if isinstance(command.get("frontmatter"), dict) else {},
        "instructions": instructions,
        "content": content,
        "metadata": metadata,
    }
    add_project_event(project_id, "project_plugin_command", payload)
    return {
        **payload,
        "reply": f"Plugin command loaded: /{payload['command']}. Use the command instructions as project context for the next workflow decision.",
    }


def _upsert_project_md_section(project_id: str, heading: str, content: str) -> str:
    current = _project_md(project_id)
    updated = _replace_markdown_section(current, heading, str(content or "").strip())
    _store_project_md(project_id, updated, summary=f"{heading} updated")
    return updated


def _append_project_md_bullet(project_id: str, heading: str, content: str) -> str:
    current = _project_md(project_id)
    bullet = f"- {str(content or '').strip()}"
    existing = _section_content(current, heading)
    next_content = "\n".join([line for line in [existing.strip(), bullet] if line]).strip()
    updated = _replace_markdown_section(current, heading, next_content)
    _store_project_md(project_id, updated, summary=f"{heading} updated")
    return updated


def _store_project_md(project_id: str, content: str, *, summary: str) -> None:
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = dict(settings.get("parameters") or {}) if isinstance(settings, dict) else {}
    parameters["project_md"] = content
    update_project_settings(project_id, parameters=parameters)
    add_project_event(project_id, "project_md_updated", {"summary": summary, "chars": len(content or "")})


def _replace_markdown_section(markdown: str, heading: str, content: str) -> str:
    lines = str(markdown or "").splitlines()
    marker = f"## {heading}"
    start = next((idx for idx, line in enumerate(lines) if line.strip().lower() == marker.lower()), None)
    replacement = [marker, str(content or "").strip(), ""]
    if start is None:
        base = str(markdown or "").rstrip()
        return "\n".join([base, "", *replacement]).strip() + "\n"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break
    return "\n".join([*lines[:start], *replacement, *lines[end:]]).strip() + "\n"


def _section_content(markdown: str, heading: str) -> str:
    lines = str(markdown or "").splitlines()
    marker = f"## {heading}"
    start = next((idx for idx, line in enumerate(lines) if line.strip().lower() == marker.lower()), None)
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break
    return "\n".join(lines[start + 1 : end]).strip()


def _distill_project_conversation(project_id: str, messages: list[dict[str, Any]], instruction: str) -> str:
    conversation = [
        f"{str(item.get('role') or 'message')}: {str(item.get('content') or '').strip()}"
        for item in messages[-12:]
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    memory = read_project_memory(project_id)
    parts = [
        f"Project: {project_id}",
        f"Instruction: {instruction.strip()}" if instruction.strip() else "",
        "Recent conversation:",
        *conversation,
        "Reusable memory:",
        f"Frameworks: {', '.join(str(x) for x in (memory.get('frameworks') or [])[-8:]) or '-'}",
        f"Rules: {'; '.join(str(x) for x in (memory.get('rules') or [])[-8:]) or '-'}",
    ]
    return "\n".join(part for part in parts if part).strip()


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
                "The chat also supports deterministic slash commands: /goal records a project goal, "
                "/learn records reusable project knowledge, and /distill compacts the conversation into Project.MD. "
                "Skill management is based on SKILL.md entries at global and project scope. "
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
        if status_key in _project_terminal_statuses(project_id):
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


def _project_terminal_statuses(project_id: str) -> set[str]:
    workflow = get_project_workflow(project_id).get("workflow") or {}
    actions = workflow.get("actions") if isinstance(workflow.get("actions"), dict) else {}
    terminals: set[str] = set()
    for action in ("workflow_done", "complete", "completed", "fail", "abandon"):
        rule = actions.get(action) if isinstance(actions, dict) else None
        if isinstance(rule, dict):
            target = str(rule.get("to") or "").strip().lower()
            if target:
                terminals.add(target)
    return terminals


def _project_md(project_id: str) -> str:
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = settings.get("parameters") if isinstance(settings, dict) else {}
    value = parameters.get("project_md") if isinstance(parameters, dict) else None
    if isinstance(value, str) and value.strip():
        return value
    workflow = get_project_workflow(project_id).get("workflow") or {}
    agents = settings.get("agents") if isinstance(settings, dict) else {}
    agent_names = ", ".join(sorted(agents.keys())) if isinstance(agents, dict) and agents else "project-agent, context-indexer"
    return "\n".join(
        [
            f"# Project.MD: {project_id}",
            "",
            "## Project Intent",
            "Describe the project goal, domain, users, and operating constraints here.",
            "",
            "## Workflow Contract",
            "All work must enter through the project conversation and be executed by the Kanban workflow engine.",
            f"Current workflow: {workflow.get('name') or 'not configured'}",
            "",
            "## Agent Contract",
            "Workflow nodes spawn temporary agents according to the project workflow and route settings.",
            f"Configured project agents: {agent_names}",
            "",
            "## Constraints",
            "- Preserve source safety snapshots when a capability provider applies code.",
            "- Record workflow events, artifacts, and decisions for auditability.",
            "- Use project memory and task memory as context; do not bypass Kanban.",
            "",
            "## Debug Policy",
            "When a task stalls or fails, inspect workflow events, artifacts, client tool results, and project memory before retrying.",
        ]
    )


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
    return HTMLResponse(render_web_ui("kanban"))


@ui_router.get("/dashboard", response_class=HTMLResponse)
def dashboard_ui():
    return HTMLResponse(render_web_ui("projects"))


@ui_router.get("/workbench", response_class=HTMLResponse)
def workbench_ui():
    return HTMLResponse(render_web_ui("overview"))


@ui_router.get("/tasks", response_class=HTMLResponse)
def tasks_ui():
    return HTMLResponse(render_web_ui("tasks"))
