from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

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
    action: str = "design"
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
    return {"ok": True, "project_id": project_id, "messages": messages}


@router.post("/projects/{project_id}/conversation")
def kanban_project_conversation_message(project_id: str, req: ProjectConversationRequest):
    action = str(req.action or "design").strip().lower().replace("-", "_")
    messages = _project_conversation_messages(req.messages, req.message)
    user_text = _latest_message_content(messages, role="user")
    if user_text:
        add_project_event(
            project_id,
            "project_conversation_message",
            {"role": "user", "content": user_text, "kind": action, "metadata": req.metadata},
        )

    if action in {"design", "save_design", "revise_workflow", "configure_project"}:
        design_req = WorkflowDesignRequest(
            messages=messages,
            current_workflow=req.current_workflow,
            current_agents=req.current_agents,
            save=req.save or action == "save_design",
        )
        result = kanban_design_project_workflow(project_id, design_req)
        add_project_event(
            project_id,
            "project_conversation_message",
            {
                "role": "assistant",
                "content": result.get("reply") or "Workflow draft updated.",
                "kind": action,
                "saved": result.get("saved", False),
            },
        )
        return {"ok": True, "project_id": project_id, "kind": "workflow_design", **result}

    if action in {"start_task", "run_task", "dispatch_task"}:
        if not user_text:
            raise HTTPException(status_code=400, detail="message is required to start a task")
        body = {
            "project_id": project_id,
            "mode": "agent",
            "interaction_mode": "auto",
            "messages": [{"role": "user", "content": user_text}],
            "workspace": req.workspace
            or {"root_id": project_id, "changed_files": [], "open_files": [], "tree_preview": "", "source_map": None},
            "metadata": {"source": "project_conversation", **req.metadata},
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
                "kind": action,
                "task_id": result.get("task_id"),
                "poll_url": result.get("poll_url"),
                "events_url": result.get("events_url"),
            },
        )
        return {"ok": started, "project_id": project_id, "kind": "task_started", **result}

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
    :root { color-scheme: light dark; --bg: #f5f7fb; --panel: #fff; --text: #182033; --muted: #687386; --line: #d7deea; --accent: #2068d8; --ok: #027a48; --danger: #b42318; }
    @media (prefers-color-scheme: dark) { :root { --bg: #20242a; --panel: #2b3038; --text: #eef3fb; --muted: #aeb8c8; --line: #454d59; --accent: #7ba8ff; --ok: #75d6a4; --danger: #ff9b91; } }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { min-height: 58px; display: flex; align-items: center; gap: 10px; padding: 10px 18px; border-bottom: 1px solid var(--line); background: var(--panel); flex-wrap: wrap; }
    h1 { margin: 0; font-size: 18px; }
    a { color: var(--accent); }
    input, textarea, select, button { font: inherit; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--text); }
    input, select { height: 34px; padding: 0 10px; }
    textarea { width: 100%; min-height: 120px; padding: 10px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size: 12px; }
    button { height: 34px; padding: 0 12px; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    main { padding: 18px; display: grid; grid-template-columns: minmax(320px, 420px) 1fr; gap: 14px; align-items: start; }
    .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; }
    .panel h2 { margin: 0 0 10px; font-size: 15px; }
    .grid { display: grid; gap: 10px; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1 1 180px; }
    .muted { color: var(--muted); }
    .ok { color: var(--ok); }
    .error { color: var(--danger); }
    .messages { display: grid; gap: 8px; max-height: 360px; overflow: auto; padding-right: 4px; }
    .message { border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; white-space: pre-wrap; }
    .message.user { background: color-mix(in srgb, var(--accent) 10%, var(--panel)); }
    .message.assistant { background: color-mix(in srgb, var(--bg) 72%, var(--panel)); }
    .tabs { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
    .tabs button.active { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--panel)); }
    .editor { display: none; }
    .editor.active { display: block; }
    .summary { display: grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); gap: 8px; margin-top: 10px; }
    .metric { border: 1px solid var(--line); border-radius: 7px; padding: 9px; }
    .metric b { display: block; font-size: 17px; margin-top: 2px; }
    @media (max-width: 980px) { main { grid-template-columns: 1fr; } .summary { grid-template-columns: repeat(2, 1fr); } }
  </style>
</head>
<body>
  <header>
    <h1>DevWerk Workbench</h1>
    <select id="projectSelect" class="grow"></select>
    <input id="projectId" placeholder="new projectId" />
    <input id="projectName" placeholder="project name" />
    <button id="createProject" class="primary">Create Project</button>
    <button id="refresh">Refresh</button>
    <a href="/dashboard">Dashboard</a>
  </header>
  <main>
    <section class="panel grid">
      <h2>Project Conversation</h2>
      <div id="messages" class="messages"></div>
      <textarea id="prompt" placeholder="Describe the project, workflow change, or task to run."></textarea>
      <div class="row">
        <button id="send" class="primary">Design Project</button>
        <button id="save">Save Design</button>
        <button id="startTask">Start Task</button>
        <button id="load">Load Project Config</button>
      </div>
      <div id="status" class="muted"></div>
      <div id="error" class="error"></div>
    </section>
    <section class="panel">
      <div class="tabs">
        <button data-tab="workflow" class="active">Workflow JSON</button>
        <button data-tab="agents">Agent Overrides</button>
        <button data-tab="summary">Summary</button>
      </div>
      <section id="tab-workflow" class="editor active"><textarea id="workflowJson" style="min-height:620px"></textarea></section>
      <section id="tab-agents" class="editor"><textarea id="agentsJson" style="min-height:620px"></textarea></section>
      <section id="tab-summary" class="editor">
        <div id="summary" class="summary"></div>
        <pre id="summaryRaw" class="panel" style="overflow:auto; max-height:520px"></pre>
      </section>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(window.location.search);
    const isNewProjectMode = params.get("new") === "1";
    const initialProjectId = params.get("project_id") || params.get("projectId") || (isNewProjectMode ? createDraftProjectId() : "default");
    const initialProjectName = params.get("project_name") || "";
    const state = { projectId: initialProjectId, messages: [], summary: {}, activeTask: null, taskTimer: null };
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
      const projects = data.projects || [];
      const selectedExists = projects.some(p => p.id === state.projectId);
      if (!selectedExists && !isNewProjectMode) state.projectId = projects[0]?.id || "default";
      const options = projects.map(p => `<option value="${escAttr(p.id)}" ${p.id === state.projectId ? "selected" : ""}>${esc(p.name || p.id)} (${esc(p.id)})</option>`);
      if (!selectedExists && isNewProjectMode) options.unshift(`<option value="${escAttr(state.projectId)}" selected>${esc(initialProjectName || state.projectId)} (new project option)</option>`);
      $("projectSelect").innerHTML = options.join("");
      await loadProjectConversation();
      await loadProjectConfig();
    }
    async function createProject() {
      const projectId = $("projectId").value.trim() || state.projectId || createDraftProjectId();
      const name = $("projectName").value.trim() || projectId;
      await api("/v1/kanban/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, name }) });
      state.projectId = projectId;
      setWorkbenchUrl(projectId, name);
      seedProjectDesignPrompt(projectId, name);
      $("projectId").value = "";
      $("projectName").value = "";
      await refresh();
    }
    async function loadProjectConfig() {
      const workflow = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/workflow`);
      const settings = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/settings`);
      $("workflowJson").value = JSON.stringify(workflow.workflow || {}, null, 2);
      $("agentsJson").value = JSON.stringify((settings.settings || {}).agents || {}, null, 2);
      state.summary = { source: "loaded", workflow: workflow.workflow?.summary || null };
      renderSummary();
      setStatus(`Loaded ${state.projectId}`);
    }
    async function loadProjectConversation() {
      const data = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`);
      state.messages = data.messages || [];
      renderMessages();
    }
    async function sendDesign(save) {
      clearError();
      const content = $("prompt").value.trim();
      if (content) {
        state.messages.push({ role: "user", content });
        $("prompt").value = "";
      }
      renderMessages();
      setBusy(true);
      try {
        const payload = {
          messages: state.messages,
          current_workflow: JSON.parse($("workflowJson").value || "{}"),
          current_agents: JSON.parse($("agentsJson").value || "{}"),
          save: Boolean(save)
        };
        const result = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`, { method: "POST", body: JSON.stringify({ ...payload, action: save ? "save_design" : "design", message: content }) });
        state.messages.push({ role: "assistant", content: result.reply || "Workflow draft updated." });
        $("workflowJson").value = JSON.stringify(result.workflow || {}, null, 2);
        $("agentsJson").value = JSON.stringify(result.agents || {}, null, 2);
        state.summary = result.summary || {};
        renderMessages();
        renderSummary(result);
        setStatus(result.saved ? "Draft generated and saved" : "Draft generated");
      } finally {
        setBusy(false);
      }
    }
    async function startProjectTask() {
      clearError();
      const content = $("prompt").value.trim();
      if (!content) throw new Error("Describe the task before starting it.");
      state.messages.push({ role: "user", content });
      $("prompt").value = "";
      renderMessages();
      setBusy(true);
      try {
        const result = await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`, {
          method: "POST",
          body: JSON.stringify({ action: "start_task", message: content, messages: state.messages })
        });
        state.messages.push({ role: "assistant", content: result.task_id ? `Task started: ${result.task_id}` : (result.error_message || "Task dispatch failed.") });
        renderMessages();
        if (!result.ok) throw new Error(result.error_message || "Task dispatch failed.");
        state.activeTask = result;
        setStatus(`Task started ${result.task_id}`);
        pollTask(result.poll_url);
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
          const waitingFor = data.waiting_for || data.conversation?.waiting_for || "";
          setStatus(`Task ${data.task_id || state.activeTask?.task_id || ""} ${status}${waitingFor ? ` / waiting ${waitingFor}` : ""}`);
          if (data.result || ["done", "failed", "ready_to_apply"].includes(status)) {
            state.messages.push({ role: "assistant", content: `Task update: ${status || "result ready"}` });
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
    async function saveDraft() {
      clearError();
      await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/workflow`, { method: "PUT", body: JSON.stringify({ workflow: JSON.parse($("workflowJson").value || "{}") }) });
      await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/settings`, { method: "PUT", body: JSON.stringify({ agents: JSON.parse($("agentsJson").value || "{}") }) });
      setStatus("Saved workflow and agent overrides");
    }
    async function saveDesign() {
      if ($("prompt").value.trim()) {
        await sendDesign(true);
      } else {
        await saveDraft();
      }
    }
    function renderMessages() {
      $("messages").innerHTML = state.messages.map(m => `<div class="message ${escAttr(m.role)}"><b>${esc(m.role)}</b>\n${esc(m.content)}</div>`).join("") || `<div class="muted">Start by describing the process you want.</div>`;
      $("messages").scrollTop = $("messages").scrollHeight;
    }
    function renderSummary(raw) {
      const workflow = JSON.parse($("workflowJson").value || "{}");
      const agents = JSON.parse($("agentsJson").value || "{}");
      const columns = workflow.columns || [];
      const executable = columns.filter(c => c.job_template).map(c => c.status_key);
      const rows = [["Columns", columns.length], ["Executable", executable.length], ["Actions", Object.keys(workflow.actions || {}).length], ["Agent Overrides", Object.keys(agents).length]];
      $("summary").innerHTML = rows.map(([k, v]) => `<div class="metric"><span class="muted">${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
      $("summaryRaw").textContent = JSON.stringify(raw || { executable_columns: executable, actions: Object.keys(workflow.actions || {}).sort(), agents }, null, 2);
    }
    function setBusy(value) { $("send").disabled = value; $("save").disabled = value; $("startTask").disabled = value; $("load").disabled = value; setStatus(value ? "Working..." : $("status").textContent); }
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
    function seedProjectDesignPrompt(projectId, name) {
      if ($("prompt").value.trim()) return;
      const displayName = name || projectId;
      $("prompt").value = `Create the DevWerk project design for "${displayName}". Define workflow columns, state-machine actions, default project agent behavior, context policy, retry/failure behavior, task dispatch rules, and external capabilities. The project may be coding or non-coding.`;
    }
    $("projectSelect").onchange = async (event) => { state.projectId = event.target.value; state.messages = []; renderMessages(); await loadProjectConversation().catch(showError); await loadProjectConfig().catch(showError); };
    $("createProject").onclick = () => createProject().catch(showError);
    $("refresh").onclick = () => refresh().catch(showError);
    $("load").onclick = () => loadProjectConfig().catch(showError);
    $("send").onclick = () => sendDesign(false).catch(showError);
    $("save").onclick = () => saveDesign().catch(showError);
    $("startTask").onclick = () => startProjectTask().catch(showError);
    document.querySelector(".tabs").onclick = (event) => {
      const btn = event.target.closest("button[data-tab]");
      if (!btn) return;
      document.querySelectorAll(".tabs button").forEach(item => item.classList.toggle("active", item === btn));
      document.querySelectorAll(".editor").forEach(item => item.classList.remove("active"));
      $(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "summary") renderSummary();
    };
    if (isNewProjectMode) {
      $("projectId").value = initialProjectId;
      $("projectName").value = initialProjectName;
      seedProjectDesignPrompt(initialProjectId, initialProjectName);
    }
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
