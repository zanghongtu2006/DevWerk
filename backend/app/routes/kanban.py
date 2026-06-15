from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.kanban import (
    add_artifact,
    add_event,
    create_task,
    get_board,
    get_project,
    get_project_settings,
    get_task,
    list_events,
    list_columns,
    list_projects,
    replace_columns,
    update_project_settings,
    upsert_project,
    update_task,
)
from app.services.workflow import apply_workflow_action, current_workflow_state

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


@router.put("/projects/{project_id}/settings")
def kanban_update_project_settings(project_id: str, req: ProjectSettingsRequest):
    return update_project_settings(
        project_id,
        agents=req.agents,
        parameters=req.parameters,
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
        return apply_workflow_action(task_id, req.action, req.payload)
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


@ui_router.get("/kanban", response_class=HTMLResponse)
def kanban_ui():
    return HTMLResponse(KANBAN_HTML)


@ui_router.get("/dashboard", response_class=HTMLResponse)
def dashboard_ui():
    return HTMLResponse(DASHBOARD_HTML)


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
    .metric, .project-row, .column, .task, .settings-box { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
    .metric { padding: 12px; }
    .metric b { display: block; font-size: 22px; margin-top: 4px; }
    .muted { color: var(--muted); }
    .error { color: var(--danger); }
    .projects { grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); margin-bottom: 14px; }
    .project-row { padding: 12px; }
    .project-row h3 { margin: 0 0 6px; font-size: 15px; }
    .project-row footer { display: flex; gap: 8px; margin-top: 10px; }
    .board { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(250px, 1fr); gap: 12px; overflow-x: auto; padding-bottom: 12px; }
    .column { min-height: 380px; overflow: hidden; }
    .column h2 { margin: 0; padding: 10px 12px; font-size: 13px; display: flex; justify-content: space-between; border-bottom: 1px solid var(--line); }
    .tasks { display: grid; gap: 8px; padding: 10px; }
    .task { padding: 10px; }
    .task strong { display: block; margin-bottom: 5px; }
    .task p { margin: 0 0 8px; color: var(--muted); white-space: pre-wrap; }
    .task footer { display: flex; gap: 6px; flex-wrap: wrap; }
    .task footer button { height: 28px; padding: 0 8px; font-size: 12px; }
    .events { display: grid; gap: 8px; }
    .event-row { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 10px; }
    .event-head { display: flex; gap: 8px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }
    .event-title { font-weight: 650; }
    .event-meta { color: var(--muted); font-size: 12px; }
    .event-flow { margin-top: 6px; color: var(--muted); }
    .event-row details { margin-top: 8px; }
    .event-row pre { margin: 6px 0 0; overflow: auto; max-height: 260px; padding: 8px; border-radius: 7px; border: 1px solid var(--line); background: color-mix(in srgb, var(--bg) 80%, var(--panel)); font-size: 12px; }
    .settings-grid { grid-template-columns: repeat(2, minmax(260px, 1fr)); align-items: start; }
    .settings-box { padding: 12px; }
    .settings-box h3 { margin: 0 0 10px; font-size: 14px; }
    .wide { grid-column: 1 / -1; }
    @media (max-width: 900px) { .shell, .shell.collapsed { grid-template-columns: 1fr; } aside { position: sticky; top: 0; z-index: 2; border-right: 0; border-bottom: 1px solid var(--line); } nav { grid-template-columns: repeat(5, 1fr); } .stats, .settings-grid { grid-template-columns: 1fr; } .project-select { min-width: 0; width: 100%; margin-left: 0; } }
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
        <button data-view="events">E <span>Events</span></button>
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
          <div class="toolbar"><input id="newProjectId" placeholder="projectId" /><input id="newProjectName" placeholder="Project name" /><button id="createProject" class="primary">Save Project</button></div>
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
        <section id="view-events" class="view">
          <div class="toolbar"><input id="eventTaskId" placeholder="Filter task id" /><input id="eventLimit" placeholder="Limit" value="200" /><button id="loadEvents" class="primary">Load Events</button></div>
          <div id="eventList" class="events"></div>
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
    const state = { view: "stats", projects: [], projectId: "default", board: null, events: [] };
    async function api(path, options = {}) {
      const res = await fetch(path, { ...options, headers: { "Content-Type": "application/json", "X-DevWerk-Project-Id": state.projectId, ...(options.headers || {}) } });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(data.detail || text || `HTTP ${res.status}`);
      return data;
    }
    async function refreshAll() { clearError(); await loadProjects(); await Promise.all([loadStats(), loadBoard(), loadEvents(), loadGlobalSettings(), loadProjectSettings()]); renderActive(); }
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
      $("projectList").innerHTML = state.projects.map(p => { const s = p.stats || {}; return `<article class="project-row"><h3>${escapeHtml(p.name || p.id)}</h3><div class="muted">${escapeHtml(p.id)}</div><p>${escapeHtml(p.description || "")}</p><div class="muted">tasks ${s.tasks || 0} / requests ${s.request_count || 0} / tokens ${s.total_tokens || 0}</div><footer><button data-project="${escapeAttr(p.id)}" data-action="open-kanban">Open Kanban</button><button data-project="${escapeAttr(p.id)}" data-action="open-project-settings">Project Settings</button></footer></article>`; }).join("");
    }
    function renderBoard() {
      const data = state.board; if (!data) return;
      $("board").innerHTML = data.columns.map(col => `<article class="column"><h2><span>${escapeHtml(col.title)}</span><span class="muted">${col.tasks.length}</span></h2><div class="tasks">${col.tasks.map(task => renderTask(task, col, data.columns)).join("")}</div></article>`).join("");
    }
    function renderTask(task, col, columns) {
      const actions = [];
      if (task.status_key === "failed") actions.push(`<button data-task="${escapeAttr(task.id)}" data-action="retry">Retry</button>`);
      if (!["done", "failed"].includes(task.status_key)) actions.push(`<button data-task="${escapeAttr(task.id)}" data-action="abandon">Abandon</button>`);
      return `<article class="task"><strong>${escapeHtml(task.title)}</strong><p>${escapeHtml(task.description || "")}</p><small class="muted">${escapeHtml(task.status_key)} / ${escapeHtml(task.id)}</small><footer>${actions.join("")}</footer></article>`;
    }
    function renderEvents() {
      const rows = state.events || [];
      $("eventList").innerHTML = rows.map(event => {
        const payload = JSON.stringify(event.payload || {}, null, 2);
        const flow = event.from_status || event.to_status ? `${event.from_status || "-"} -> ${event.to_status || "-"}` : "no status change";
        const task = event.task_title ? `${event.task_title} / ${event.task_id}` : event.task_id;
        return `<article class="event-row"><div class="event-head"><span class="event-title">${escapeHtml(event.event_type)}</span><span class="event-meta">${escapeHtml(event.created_at)}</span></div><div class="event-flow">${escapeHtml(flow)}</div><div class="event-meta">${escapeHtml(task || "")}</div><details><summary>Details</summary><pre>${escapeHtml(payload)}</pre></details></article>`;
      }).join("") || `<div class="muted">No events</div>`;
    }
    function renderActive() { document.querySelectorAll(".view").forEach(v => v.classList.remove("active")); document.querySelector(`#view-${state.view}`).classList.add("active"); document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.view === state.view)); $("title").textContent = { stats: "Statistics", projects: "Projects", kanban: "Kanban", events: "Events", settings: "Settings" }[state.view]; }
    async function createProject() { const projectId = $("newProjectId").value.trim(); const name = $("newProjectName").value.trim() || projectId; if (!projectId) return; await api("/v1/kanban/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, name }) }); state.projectId = projectId; $("newProjectId").value = ""; $("newProjectName").value = ""; await refreshAll(); }
    async function createTask() { const title = $("taskTitle").value.trim(); if (!title) return; await api("/v1/kanban/tasks", { method: "POST", body: JSON.stringify({ project_id: state.projectId, title, description: $("taskDescription").value.trim() }) }); $("taskTitle").value = ""; $("taskDescription").value = ""; await refreshAll(); }
    async function saveGlobalSettings() { await api("/v1/settings", { method: "PUT", body: JSON.stringify({ llms: JSON.parse($("llmsJson").value || "{}"), routing: JSON.parse($("routingJson").value || "{}") }) }); await refreshAll(); }
    async function saveProjectSettings() { await api(`/v1/kanban/projects/${encodeURIComponent(state.projectId)}/settings`, { method: "PUT", body: JSON.stringify({ agents: JSON.parse($("projectAgentsJson").value || "{}"), parameters: JSON.parse($("projectParametersJson").value || "{}") }) }); await refreshAll(); }
    async function resetColumns() {
      await api("/v1/kanban/columns", { method: "PUT", body: JSON.stringify({ project_id: state.projectId, columns: [ { status_key: "draft", title: "Draft", position: 10, transition_to: ["context_indexed", "failed"] }, { status_key: "context_indexed", title: "Context Indexed", position: 20, transition_to: ["planned", "failed"] }, { status_key: "planned", title: "Planned", position: 30, transition_to: ["coding", "draft", "failed"] }, { status_key: "coding", title: "Coding", position: 40, transition_to: ["ready_to_apply", "planned", "failed"] }, { status_key: "ready_to_apply", title: "Ready To Apply", position: 50, transition_to: ["applied", "coding", "failed"] }, { status_key: "applied", title: "Applied", position: 60, transition_to: ["verified", "coding", "planned", "failed"] }, { status_key: "verified", title: "Verified", position: 70, transition_to: ["done", "applied", "failed"] }, { status_key: "done", title: "Done", position: 80, transition_to: [] }, { status_key: "failed", title: "Failed", position: 90, transition_to: ["draft"] } ] }) });
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
    $("projectList").onclick = async (event) => { const btn = event.target.closest("button[data-project]"); if (!btn) return; state.projectId = btn.dataset.project; state.view = btn.dataset.action === "open-project-settings" ? "projects" : "kanban"; await refreshAll().catch(showError); };
    $("board").onclick = async (event) => {
      const btn = event.target.closest("button[data-task]");
      if (!btn) return;
      if (btn.dataset.action === "retry") await api(`/v1/kanban/tasks/${btn.dataset.task}/actions`, { method: "POST", body: JSON.stringify({ action: "retry", payload: { reason: "user_requested_retry" } }) });
      if (btn.dataset.action === "abandon") await api(`/v1/kanban/tasks/${btn.dataset.task}/actions`, { method: "POST", body: JSON.stringify({ action: "abandon", payload: { reason: "user_abandoned_task" } }) });
      await refreshAll();
    };
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
