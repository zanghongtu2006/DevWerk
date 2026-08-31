import { api, loadProjectBundle, loadTaskDetail, openProjectStream } from "./core/api.js?v=20260804-debug1";
import { routeFromPath, state, updateProjectUrl } from "./core/state.js?v=20260804-debug1";
import { configureStatusCatalog } from "./core/format.js?v=20260804-debug1";
import { pageSkeleton } from "./ui/components.js?v=20260804-debug1";
import {
  closeProjectDialog,
  renderProjectRail,
  setBusy,
  setConnection,
  setProjectContext,
  showProjectDialog,
  showToast,
  toggleProjectRail,
} from "./ui/shell.js?v=20260804-debug1";
import { renderOverview } from "./pages/overview.js?v=20260804-debug1";
import { renderProjects } from "./pages/projects.js?v=20260804-debug1";
import { renderKanban } from "./pages/kanban.js?v=20260825-task-order1";
import { renderTasks } from "./pages/tasks.js?v=20260804-debug1";
import { renderEvents } from "./pages/events.js?v=20260804-debug1";
import { renderSettings } from "./pages/settings.js?v=20260826-settings1";

const page = document.getElementById("page");
let projectStream = null;
let activeBundleRequest = 0;
let projectEventFlushTimer = null;
let pendingProjectEvents = [];
let loadingOlderConversation = false;

const renderers = {
  overview: renderOverview,
  projects: renderProjects,
  kanban: renderKanban,
  tasks: renderTasks,
  events: renderEvents,
  settings: renderSettings,
};

function navigate(route) {
  const paths = { overview: "/workbench", projects: "/dashboard", kanban: "/kanban", tasks: "/tasks", events: "/events", settings: "/settings" };
  history.pushState({}, "", updateProjectUrl(paths[route] || "/workbench", state.projectId));
  state.route = route;
  state.taskDetail = null;
  state.agentDetail = null;
  renderNavigation();
  renderPage();
  if (route === "projects" && state.projectId) refreshConversationView();
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
    conversationStatus: [state.conversationStatus?.agent_state, state.conversationStatus?.job?.id, state.conversationStatus?.job?.updated_at],
    tasks: (state.board?.tasks || []).map((item) => [item.id, item.status, item.current_column, item.updated_at]),
    workflow: state.board?.workflow?.id,
    events: state.events.map((item) => item.id),
  });
}

function mergeConversationMessages(current, incoming) {
  const messages = new Map(current.map((item) => [Number(item.id), item]));
  for (const item of incoming) messages.set(Number(item.id), item);
  return [...messages.values()].sort((left, right) => Number(left.id) - Number(right.id));
}

function lastConversationMessageId() {
  return state.conversation.reduce((value, item) => Math.max(value, Number(item.id) || 0), 0);
}

async function refreshConversationMessages() {
  const afterId = lastConversationMessageId();
  const incoming = await api.get(`/projects/${state.projectId}/conversation?limit=150&after_id=${afterId}`);
  state.conversation = mergeConversationMessages(state.conversation, incoming);
  if (state.pendingMessage && incoming.some((item) => item.role === "user" && item.content === state.pendingMessage)) {
    state.pendingMessage = "";
  }
}

async function refreshConversationStatus() {
  state.conversationStatus = await api.get(`/projects/${state.projectId}/conversation-state`);
}

function currentPageView() {
  if (state.route !== "kanban") return null;
  const scroll = document.querySelector(".kanban-scroll");
  return scroll ? { left: scroll.scrollLeft, top: scroll.scrollTop } : null;
}

function renderPage() {
  const view = currentPageView();
  const renderer = renderers[state.route] || renderOverview;
  page.innerHTML = renderer(state);
  bindPageActions();
  if (view && state.route === "kanban") {
    const scroll = document.querySelector(".kanban-scroll");
    if (scroll) {
      scroll.scrollLeft = view.left;
      scroll.scrollTop = view.top;
    }
  }
  if (state.route === "projects") {
    const messages = document.getElementById("conversation-messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }
}

async function refreshConversationView() {
  try {
    await Promise.all([refreshConversationMessages(), refreshConversationStatus()]);
    if (state.route === "projects") renderPage();
  } catch (error) {
    setConnection("degraded", "Conversation refresh failed");
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
  document.querySelector("[data-load-older-messages]")?.addEventListener("click", loadOlderConversation);
  const conversationMessages = document.getElementById("conversation-messages");
  conversationMessages?.addEventListener("scroll", () => {
    if (conversationMessages.scrollTop <= 24 && state.conversationHasOlder) loadOlderConversation();
  }, { passive: true });
  document.getElementById("global-settings-form")?.addEventListener("submit", saveGlobalSettings);
}

async function boot() {
  state.route = routeFromPath(location.pathname);
  renderNavigation();
  setBusy(true, "正在连接 DevWerk…");
  renderLoading();
  try {
    const [health, projects, statusCatalog, globalSettings] = await Promise.all([
      api.get("/health"),
      api.get("/projects"),
      api.get("/runtime-statuses"),
      api.get("/settings"),
    ]);
    state.health = health;
    state.projects = projects;
    state.statusCatalog = statusCatalog;
    state.settings = globalSettings;
    configureStatusCatalog(statusCatalog);
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
  state.conversationStatus = null;
  state.conversationHasOlder = false;
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
    state.conversationStatus = bundle.conversationStatus;
    state.conversationHasOlder = bundle.conversation.length >= 150;
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
    await Promise.all([refreshConversationMessages(), refreshConversationStatus()]);
    state.pendingMessage = "";
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

async function loadOlderConversation() {
  if (loadingOlderConversation || !state.projectId || !state.conversation.length || !state.conversationHasOlder) return;
  loadingOlderConversation = true;
  const firstId = Math.min(...state.conversation.map((item) => Number(item.id)));
  const container = document.getElementById("conversation-messages");
  const oldHeight = container?.scrollHeight || 0;
  const oldTop = container?.scrollTop || 0;
  try {
    const older = await api.get(`/projects/${state.projectId}/conversation?limit=150&before_id=${firstId}`);
    state.conversation = mergeConversationMessages(older, state.conversation);
    state.conversationHasOlder = older.length >= 150;
    renderPage();
    const next = document.getElementById("conversation-messages");
    if (next) next.scrollTop = next.scrollHeight - oldHeight + oldTop;
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    loadingOlderConversation = false;
  }
}

function startProjectStream() {
  if (projectStream) projectStream.close();
  if (projectEventFlushTimer) clearTimeout(projectEventFlushTimer);
  projectEventFlushTimer = null;
  pendingProjectEvents = [];
  if (!state.projectId) return;
  const cursor = state.events.reduce((value, item) => Math.max(value, Number(item.id) || 0), 0);
  projectStream = openProjectStream(state.projectId, cursor, queueProjectEvent, () => setConnection("degraded", "Event stream reconnecting"));
}

async function saveGlobalSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector("button[type='submit']");
  const values = JSON.parse(JSON.stringify(state.settings.values));
  form.querySelectorAll("[data-setting-key]").forEach((input) => {
    const path = input.dataset.settingKey.split(".");
    const name = path.pop();
    const owner = path.reduce((value, part) => value[part], values);
    owner[name] = input.type === "checkbox" ? input.checked : input.value;
  });
  submit.disabled = true;
  try {
    const result = await api.post("/settings", {
      ...values,
      schema_version: state.settings.schema_version,
    });
    state.settings = result;
    if (result.restart_scheduled) {
      projectStream?.close();
      setConnection("connecting", "Runtime restarting");
      setBusy(true, "设置已保存，DevWerk 正在自动重启…");
      await waitForRuntimeRestart();
      location.reload();
      return;
    }
    showToast(result.restart_required ? "设置已保存，但当前启动方式不支持自动重启。" : "设置已保存。", result.restart_required ? "error" : "success");
    renderPage();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    submit.disabled = false;
  }
}

async function waitForRuntimeRestart() {
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const health = await api.get("/health", { timeout: 1500 });
      if (health.status === "ok") return;
    } catch (_error) {
      // The expected gap while startup.bat replaces Uvicorn.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("设置已保存，但 DevWerk 未能在 30 秒内重新启动。");
}

function queueProjectEvent(event) {
  if (!state.projectId || state.events.some((item) => item.id === event.id) || pendingProjectEvents.some((item) => item.id === event.id)) return;
  pendingProjectEvents.push(event);
  if (!projectEventFlushTimer) projectEventFlushTimer = setTimeout(flushProjectEvents, 250);
}

function changesBoard(event) {
  return event.type === "workflow.published"
    || event.type.startsWith("task.")
    || event.type.startsWith("column.")
    || event.type.startsWith("retry.");
}

function changesConversation(event) {
  return event.type.startsWith("conversation.") && event.type !== "conversation.progress";
}

async function flushProjectEvents() {
  projectEventFlushTimer = null;
  const events = pendingProjectEvents;
  pendingProjectEvents = [];
  if (!state.projectId || !events.length) return;
  const projectId = state.projectId;
  for (const event of events) {
    state.events.push(event);
    if (state.events.length > 500) state.events.shift();
  }
  try {
    const boardChanged = !state.board || events.some(changesBoard);
    if (boardChanged) {
      const snapshot = await api.get(`/projects/${projectId}/projection`);
      if (projectId !== state.projectId) return;
      state.board = { ...state.board, workflow: snapshot.projection.workflow, tasks: snapshot.projection.tasks, version: snapshot.version };
    }
    setProjectContext(state.project, state.board.tasks || []);
    const conversationChanged = state.route === "projects" && events.some(changesConversation);
    if (conversationChanged) {
      if (events.some((event) => event.type === "conversation.message")) await refreshConversationMessages();
      await refreshConversationStatus();
    }
    const editing = document.activeElement?.id === "message-input";
    const pageChanged = boardChanged || conversationChanged || state.route === "events";
    if (pageChanged && !editing && !state.sending) renderPage();
    setConnection("online", "Runtime online");
  } catch (error) {
    setConnection("degraded", "Projection refresh failed");
  } finally {
    if (pendingProjectEvents.length && !projectEventFlushTimer) projectEventFlushTimer = setTimeout(flushProjectEvents, 250);
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

document.getElementById("refresh")?.addEventListener("click", async () => {
  if (state.route === "settings") {
    state.settings = await api.get("/settings");
    renderPage();
    return;
  }
  await refreshBundle({ showLoading: true });
});
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
