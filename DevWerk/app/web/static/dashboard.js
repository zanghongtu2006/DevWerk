import { api, loadProjectBundle, loadTaskDetail, openProjectStream } from "./core/api.js";
import { routeFromPath, state, updateProjectUrl } from "./core/state.js";
import { pageSkeleton } from "./ui/components.js";
import {
  closeProjectDialog,
  renderProjectRail,
  setBusy,
  setConnection,
  setProjectContext,
  showProjectDialog,
  showToast,
  toggleProjectRail,
} from "./ui/shell.js";
import { renderOverview } from "./pages/overview.js";
import { renderProjects } from "./pages/projects.js";
import { renderKanban } from "./pages/kanban.js";
import { renderTasks } from "./pages/tasks.js";
import { renderEvents } from "./pages/events.js";

const page = document.getElementById("page");
let projectStream = null;
let activeBundleRequest = 0;

const renderers = {
  overview: renderOverview,
  projects: renderProjects,
  kanban: renderKanban,
  tasks: renderTasks,
  events: renderEvents,
};

function navigate(route) {
  const paths = { overview: "/workbench", projects: "/dashboard", kanban: "/kanban", tasks: "/tasks", events: "/events" };
  history.pushState({}, "", updateProjectUrl(paths[route] || "/workbench", state.projectId));
  state.route = route;
  state.taskDetail = null;
  state.agentDetail = null;
  renderNavigation();
  renderPage();
}

function renderNavigation() {
  document.querySelectorAll("a[data-route]").forEach((link) => {
    const active = link.dataset.route === state.route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.body.dataset.currentRoute = state.route;
}

function projectBundleSignature() {
  return JSON.stringify({
    project: state.project?.updated_at,
    messages: state.conversation.map((item) => [item.id, item.created_at]),
    tasks: (state.board?.tasks || []).map((item) => [item.id, item.status, item.current_column, item.updated_at]),
    workflow: state.board?.workflow?.id,
    events: state.events.map((item) => item.id),
  });
}

function renderPage() {
  const renderer = renderers[state.route] || renderOverview;
  page.innerHTML = renderer(state);
  bindPageActions();
  if (state.route === "projects") {
    const messages = document.getElementById("conversation-messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }
}

function renderLoading(route = state.route) {
  page.innerHTML = pageSkeleton(route);
}

function bindPageActions() {
  document.querySelectorAll("[data-open-route]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.openRoute));
  });
  document.querySelectorAll("[data-task-id]").forEach((button) => {
    button.addEventListener("click", () => selectTask(button.dataset.taskId));
  });
  document.querySelectorAll("[data-agent-run-id]").forEach((button) => {
    button.addEventListener("click", () => selectAgentRun(button.dataset.agentRunId));
  });
  const form = document.getElementById("conversation-form");
  if (form) form.addEventListener("submit", sendConversation);
}

async function boot() {
  state.route = routeFromPath(location.pathname);
  renderNavigation();
  setBusy(true, "正在连接 DevWerk…");
  renderLoading();
  try {
    const [health, projects] = await Promise.all([api.get("/health"), api.get("/projects")]);
    state.health = health;
    state.projects = projects;
    setConnection("online", "Runtime online");
    const urlProject = new URL(location.href).searchParams.get("project_id");
    state.projectId = projects.some((item) => item.id === urlProject) ? urlProject : projects[0]?.id || null;
    state.selectedTaskId = new URL(location.href).searchParams.get("task_id");
    renderProjectRail(state.projects, state.projectId);
    if (state.projectId) {
      history.replaceState({}, "", updateProjectUrl(location.pathname, state.projectId));
      await refreshBundle({ showLoading: true });
      if (state.route === "tasks" && state.selectedTaskId) await selectTask(state.selectedTaskId);
    } else {
      setProjectContext(null);
      renderPage();
    }
    startProjectStream();
  } catch (error) {
    state.error = error;
    setConnection("offline", "Backend unavailable");
    page.innerHTML = pageSkeleton("error", error.message);
  } finally {
    setBusy(false);
  }
}

async function refreshProjects() {
  state.projects = await api.get("/projects");
  renderProjectRail(state.projects, state.projectId, document.getElementById("project-search")?.value || "");
}

async function selectProject(projectId) {
  if (!projectId || projectId === state.projectId) return;
  state.projectId = projectId;
  state.taskDetail = null;
  state.agentDetail = null;
  updateProjectUrl(location.pathname, projectId, true);
  const projectSearch = document.getElementById("project-search");
  if (projectSearch) projectSearch.value = "";
  renderProjectRail(state.projects, projectId);
  toggleProjectRail(false);
  await refreshBundle({ showLoading: true });
  startProjectStream();
}

async function refreshBundle({ showLoading = false, background = false } = {}) {
  if (!state.projectId) return;
  const requestId = ++activeBundleRequest;
  const before = projectBundleSignature();
  if (showLoading) {
    renderLoading();
    setBusy(true, "正在加载项目上下文…");
  }
  try {
    const bundle = await loadProjectBundle(state.projectId);
    if (requestId !== activeBundleRequest) return;
    state.project = bundle.board.project;
    state.board = bundle.board;
    state.conversation = bundle.conversation;
    state.events = bundle.events;
    state.error = null;
    setProjectContext(state.project, state.board.tasks || []);
    const after = projectBundleSignature();
    const editing = document.activeElement?.id === "message-input";
    if (!background || (before !== after && !editing && !state.sending)) renderPage();
  } catch (error) {
    state.error = error;
    if (!background) {
      showToast(error.message, "error");
      renderPage();
    }
  } finally {
    if (showLoading) setBusy(false);
  }
}

async function selectTask(taskId) {
  if (!taskId) return;
  state.selectedTaskId = taskId;
  state.taskDetail = null;
  state.agentDetail = null;
  const url = new URL(location.href);
  url.searchParams.set("task_id", taskId);
  history.replaceState({}, "", url);
  const detail = document.getElementById("task-detail");
  if (detail) detail.innerHTML = pageSkeleton("task-detail");
  try {
    state.taskDetail = await loadTaskDetail(state.projectId, taskId);
    renderPage();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function selectAgentRun(agentRunId) {
  if (!agentRunId || !state.projectId) return;
  try {
    state.agentDetail = await api.get(`/projects/${state.projectId}/agent-runs/${agentRunId}`);
    renderPage();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function sendConversation(event) {
  event.preventDefault();
  const input = document.getElementById("message-input");
  const message = input?.value.trim();
  if (!message || !state.projectId || state.sending) return;
  state.sending = true;
  state.pendingMessage = message;
  state.draftMessage = "";
  input.value = "";
  renderPage();
  document.getElementById("message-input")?.focus();
  setBusy(true, "Conversation Agent 正在理解并安排工作…");
  try {
    await api.post(`/projects/${state.projectId}/conversation`, { message, start_task: true }, { timeout: 660_000 });
    state.pendingMessage = "";
    await refreshBundle();
    showToast("Conversation Agent 已接收并更新项目。", "success");
  } catch (error) {
    state.pendingMessage = "";
    state.draftMessage = message;
    showToast(error.message, "error");
    renderPage();
  } finally {
    state.sending = false;
    setBusy(false);
    renderPage();
  }
}

function startProjectStream() {
  if (projectStream) projectStream.close();
  if (!state.projectId) return;
  const cursor = state.events.reduce((value, item) => Math.max(value, Number(item.id) || 0), 0);
  projectStream = openProjectStream(state.projectId, cursor, applyProjectEvent, () => setConnection("degraded", "Event stream reconnecting"));
}

async function applyProjectEvent(event) {
  if (!state.projectId || state.events.some((item) => item.id === event.id)) return;
  state.events.push(event);
  if (state.events.length > 500) state.events.shift();
  try {
    if (event.type === "workflow.published" || !state.board) {
      const snapshot = await api.get(`/projects/${state.projectId}/projection`);
      state.board = { ...state.board, workflow: snapshot.projection.workflow, tasks: snapshot.projection.tasks, version: snapshot.version };
    } else if (event.task_id) {
      const task = await api.get(`/projects/${state.projectId}/tasks/${event.task_id}`);
      const tasks = [...(state.board.tasks || [])];
      const index = tasks.findIndex((item) => item.id === task.id);
      if (index >= 0) tasks[index] = task;
      else tasks.unshift(task);
      state.board = { ...state.board, tasks: tasks.slice(0, 100), version: Math.max(state.board.version || 0, Number(event.id) || 0) };
    }
    setProjectContext(state.project, state.board.tasks || []);
    if (event.type === "conversation.message" || event.type.startsWith("conversation.")) {
      state.conversation = await api.get(`/projects/${state.projectId}/conversation?limit=150`);
    }
    const editing = document.activeElement?.id === "message-input";
    if (!editing && !state.sending) renderPage();
    setConnection("online", "Runtime online");
  } catch (error) {
    setConnection("degraded", "Projection refresh failed");
  }
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-create-project]")) {
    showProjectDialog();
    return;
  }
  const routeLink = event.target.closest("a[data-route]");
  if (routeLink) {
    event.preventDefault();
    navigate(routeLink.dataset.route);
    return;
  }
  const projectButton = event.target.closest("[data-project-id]");
  if (projectButton) selectProject(projectButton.dataset.projectId);
});

document.getElementById("refresh")?.addEventListener("click", () => refreshBundle({ showLoading: true }));
document.getElementById("new-project")?.addEventListener("click", showProjectDialog);
document.getElementById("rail-new-project")?.addEventListener("click", showProjectDialog);
document.getElementById("project-rail-toggle")?.addEventListener("click", () => toggleProjectRail());
document.getElementById("project-search")?.addEventListener("input", (event) => renderProjectRail(state.projects, state.projectId, event.target.value));
document.getElementById("project-dialog-close")?.addEventListener("click", closeProjectDialog);
document.getElementById("project-dialog")?.addEventListener("click", (event) => {
  if (event.target.id === "project-dialog") closeProjectDialog();
});
document.getElementById("project-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    const payload = Object.fromEntries(new FormData(form));
    const project = await api.post("/projects", payload);
    await refreshProjects();
    closeProjectDialog();
    form.reset();
    state.route = "projects";
    history.pushState({}, "", updateProjectUrl("/dashboard", project.id));
    await selectProject(project.id);
    renderNavigation();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    submit.disabled = false;
  }
});
window.addEventListener("popstate", () => {
  state.route = routeFromPath(location.pathname);
  renderNavigation();
  renderPage();
});
window.addEventListener("beforeunload", () => projectStream?.close());
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeProjectDialog();
});

boot();
