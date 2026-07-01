const API = "/v1";
const STAGE_TITLES = {};
const state = {
  page: document.body.dataset.page || "overview",
  projectId: new URLSearchParams(location.search).get("project_id") || "default",
  projects: [],
  board: null,
  events: [],
  conversation: [],
  settings: {},
  workflow: {},
  memory: {},
  projectMd: "",
  usage: {},
  globalUsage: {},
  globalSettings: {},
  globalSkills: [],
  globalPlugins: [],
  pluginCommands: [],
  pluginAgents: [],
  pluginHooks: [],
  pluginMcpServers: [],
  pluginSettings: {},
  pluginMarketplace: null,
  pluginValidation: null,
  projectSkills: [],
  slashCommands: [],
  stream: null,
  streamProjectId: "",
  streamStatus: "disconnected",
  liveLogs: [],
  activeTask: null,
  projectTab: "configuration",
  conversationTab: "conversation",
  taskTab: "summary",
  busy: false
};
const $ = (id) => document.getElementById(id);
async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
async function refreshAll() {
  await loadProjects();
  await Promise.allSettled([loadBoard(), loadEvents(), loadConversation(), loadSettings(), loadGlobalSettings(), loadWorkflow(), loadMemory(), loadUsage(), loadProjectMd(), loadGlobalSkills(), loadGlobalPlugins(), loadPluginCommands(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings(), loadProjectSkills(), loadSlashCommands()]);
  renderShell();
  connectProjectStream();
}
async function loadProjects() {
  const data = await api(`${API}/kanban/projects`);
  state.projects = Array.isArray(data.projects) ? data.projects : [];
  if (!state.projects.some(p => p.id === state.projectId) && state.projects[0]) state.projectId = state.projects[0].id;
}
async function loadBoard() { state.board = await api(`${API}/kanban/board?project_id=${encodeURIComponent(state.projectId)}`); }
async function loadEvents() { const data = await api(`${API}/kanban/events?project_id=${encodeURIComponent(state.projectId)}&limit=80`); state.events = data.events || []; }
async function loadConversation() {
  const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`);
  state.conversation = normalizeMessages(data.messages || []);
  state.activeTask = data.active_task || state.activeTask;
}
async function loadSettings() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/settings`); state.settings = data.settings || data || {}; } catch (_) { state.settings = {}; } }
async function loadGlobalSettings() { try { const data = await api(`${API}/settings`); state.globalSettings = data.settings || data || {}; } catch (_) { state.globalSettings = {}; } }
async function loadGlobalSkills() { try { const data = await api(`${API}/skills`); const skills = data.skills || []; const detailed = await Promise.all(skills.map(async skill => (await api(`${API}/skills/${encodeURIComponent(skill.id)}`).catch(() => ({skill}))).skill || skill)); state.globalSkills = detailed; } catch (_) { state.globalSkills = []; } }
async function loadGlobalPlugins() { try { const data = await api(`${API}/plugins`); state.globalPlugins = data.plugins || []; } catch (_) { state.globalPlugins = []; } }
async function loadPluginCommands() { try { const data = await api(`${API}/plugins/commands`); state.pluginCommands = data.commands || []; } catch (_) { state.pluginCommands = []; } }
async function loadPluginAgents() { try { const data = await api(`${API}/plugins/agents`); state.pluginAgents = data.agents || []; } catch (_) { state.pluginAgents = []; } }
async function loadPluginHooks() { try { const data = await api(`${API}/plugins/hooks`); state.pluginHooks = data.hooks || []; } catch (_) { state.pluginHooks = []; } }
async function loadPluginMcpServers() { try { const data = await api(`${API}/plugins/mcp-servers`); state.pluginMcpServers = data.mcp_servers || []; } catch (_) { state.pluginMcpServers = []; } }
async function loadPluginSettings() {
  const out = {};
  await Promise.all((state.globalPlugins || []).map(async plugin => {
    try {
      const data = await api(`${API}/plugins/${encodeURIComponent(plugin.id)}/settings`);
      out[plugin.id] = data.settings || {};
    } catch (_) {
      out[plugin.id] = {};
    }
  }));
  state.pluginSettings = out;
}
async function loadProjectSkills() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/skills`); const skills = data.skills || []; const detailed = await Promise.all(skills.map(async skill => (await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/skills/${encodeURIComponent(skill.id)}`).catch(() => ({skill}))).skill || skill)); state.projectSkills = detailed; } catch (_) { state.projectSkills = []; } }
async function loadSlashCommands() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/slash-commands`); state.slashCommands = data.commands || []; } catch (_) { state.slashCommands = []; } }
async function loadWorkflow() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/workflow`); state.workflow = data.workflow || data || {}; } catch (_) { state.workflow = {}; } }
async function loadMemory() { try { state.memory = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/memory`); } catch (_) { state.memory = {}; } }
async function loadProjectMd() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/project-md`); state.projectMd = data.content || ""; } catch (_) { state.projectMd = ""; } }
async function loadUsage() {
  try {
    const [globalUsage, projectUsage] = await Promise.all([
      api(`${API}/usage/summary`),
      api(`${API}/usage/summary?project_id=${encodeURIComponent(state.projectId)}`)
    ]);
    state.globalUsage = globalUsage || {};
    state.usage = projectUsage || {};
  } catch (_) {
    state.globalUsage = {};
    state.usage = {};
  }
}
function renderShell() {
  state.section = activeSection();
  document.querySelectorAll(".nav-link").forEach(link => link.dataset.active = String(link.dataset.nav === activeNav()));
  $("ctxProject").textContent = currentProject().name || state.projectId;
  $("ctxStatus").textContent = projectStatus(currentProject()).label;
  $("ctxStatus").className = `badge ${projectStatus(currentProject()).badge}`;
  $("ctxModelRoute").innerHTML = modelRoutes().map(route => `<option>${esc(route)}</option>`).join("") || "<option>default</option>";
  renderProjectRail();
  if (state.section) renderSectionPage(state.section);
  else if (state.page === "projects") renderProjectsPage();
  else if (state.page === "kanban") renderKanbanPage();
  else if (state.page === "tasks") renderTaskPage();
  else renderOverviewPage();
}
function renderProjectRail() {
  const q = ($("projectSearch").value || "").toLowerCase();
  const list = state.projects.filter(p => `${p.name || ""} ${p.id}`.toLowerCase().includes(q));
  $("projectList").innerHTML = list.map(project => `<button class="project-row ${project.id === state.projectId ? "active" : ""}" data-project="${escAttr(project.id)}"><span class="project-row-title"><span>${esc(project.name || project.id)}</span><span><i class="dot ${projectStatus(project).dot}"></i>${projectStatus(project).label}</span></span><small>${esc(project.description || project.id)}</small></button>`).join("");
}
function renderOverviewPage() {
  const globalUsage = usageTotals(state.globalUsage);
  const projectUsage = usageTotals(state.usage);
  $("page").innerHTML = `
    <div class="section-stack">
      <section class="card hero">
        <div class="hero-title"><span class="folder-icon"></span><div><h1 class="h1">DevWerk Overview</h1><p class="muted">Global runtime view across projects, workflow events, memory and token usage.</p></div></div>
      </section>
      <div class="quick-grid">${kpi("Projects", state.projects.length)}${kpi("Tasks", compact(projectTotal("tasks")))}${kpi("Active Tasks", compact(projectTotal("active_tasks")))}${kpi("Failed Tasks", compact(projectTotal("failed_tasks")))}${kpi("Global Tokens", compact(globalUsage.total))}${kpi("LLM Calls", compact(globalUsage.calls))}</div>
      <div class="section-grid">
        <section class="card card-pad"><div class="page-head"><div><h2 class="h2">Project Health</h2><div class="muted">Status and usage are derived from backend project stats.</div></div></div><div class="project-cards compact">${state.projects.length ? state.projects.map(projectCard).join("") : `<div class="muted">No projects returned by backend.</div>`}</div></section>
        <aside class="side-stack">${usageCard(globalUsage, "Global Usage", state.globalUsage.by_project || state.globalUsage.projects || [])}${liveLogCard()}</aside>
      </div>
      <div class="section-grid"><section class="card card-pad"><div class="h3">Global Token Breakdown</div>${usageTable(state.globalUsage.by_project || [], ["project_id","calls","input_tokens","output_tokens","total_tokens","duration_ms"])}</section><aside class="side-stack">${recentEventsCard()}${memoryCard()}</aside></div>
    </div>`;
}
function renderProjectsPage() {
  $("page").innerHTML = `
    <div class="projects-page">
      <div class="page-head"><div class="title-block"><span class="folder-icon"></span><div><h1 class="h2">Projects</h1><div class="muted">Manage and configure your development assistant projects.</div></div></div><div class="toolbar"><label class="search-box" style="margin:0;width:230px"><span></span><input placeholder="Search projects..." /></label><select class="select-pill"><option>All Environments</option></select><select class="select-pill"><option>All Statuses</option></select><button id="newProjectMain" class="button primary">+ New Project</button></div></div>
      <div class="project-cards">${state.projects.length ? state.projects.map(projectCard).join("") : `<div class="card card-pad muted">No projects returned by backend.</div>`}</div>
      <div class="project-workbench-grid">
        ${conversationCard()}
        <aside class="side-stack">${liveLogCard()}${recentTasksCard()}${recentEventsCard()}</aside>
      </div>
      <section class="card config-panel">
        <div class="panel-head"><div class="title-block"><span class="folder-icon" style="width:28px;height:28px"></span><div><div class="muted">Project Configuration</div><h2 class="h2">${esc(currentProject().name || state.projectId)} <span class="badge green">Active</span></h2></div></div></div>
        <div class="tabs">${projectTabs().map(tab=>`<button class="tab tab-button ${state.projectTab===tab.key?"active":""}" data-project-tab="${tab.key}">${tab.label}</button>`).join("")}</div>
        ${projectTabContent()}
        <div class="card project-overview">${infoItem("Environment","default")}${infoItem("Model Route", modelRoutes()[0] || "default")}${infoItem("Created", dateShort(currentProject().created_at))}${infoItem("Last Updated", relative(currentProject().updated_at))}${infoItem("Project ID", state.projectId)}</div>
      </section>
    </div>`;
  $("newProjectMain").onclick = createProjectFromPrompt;
  wireConversation();
  document.querySelectorAll("[data-project-tab]").forEach(button => {
    button.onclick = () => {
      state.projectTab = button.dataset.projectTab || "configuration";
      renderProjectsPage();
    };
  });
}
function renderSectionPage(section) {
  const renderers = {
    events: renderEventsSection,
    memory: renderMemorySection,
    analytics: renderAnalyticsSection,
    settings: renderSettingsSection
  };
  (renderers[section] || renderProjectsPage)();
}
function renderEventsSection() {
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Events</h1><div class="muted">Project and task event stream. Use this to audit workflow movement and agent decisions.</div></div><button class="button" onclick="refreshAll()">Refresh</button></div><table class="data-table" style="margin-top:16px"><thead><tr><th>Time</th><th>Event</th><th>Task</th><th>Transition</th><th>Detail</th></tr></thead><tbody>${state.events.length ? state.events.map(eventRow).join("") : `<tr><td colspan="5" class="muted">No events recorded for this project.</td></tr>`}</tbody></table></section>
    <aside class="side-stack">${recentEventsCard()}${workflowHealthSmallCard()}${routingCard()}</aside>
  </div>`;
}
function renderMemorySection() {
  const mem = state.memory || {};
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Memory</h1><div class="muted">Project-level memory carried into future tasks. This is not a raw prompt log.</div></div><span class="badge green">Project Memory</span></div>
      <div class="dense-grid" style="margin-top:16px">${memoryBucket("Frameworks", mem.frameworks)}${memoryBucket("Codebase Paths", mem.paths)}${memoryBucket("Commands", mem.commands)}${memoryBucket("Rules", mem.rules)}${memorySummaries(mem)}<div class="card card-pad"><div class="h3">Raw Memory</div><pre class="json-panel">${esc(JSON.stringify(mem, null, 2))}</pre></div></div>
    </section>
    <aside class="side-stack">${memoryCard()}${recentTasksCard()}${routingCard()}</aside>
  </div>`;
}
function renderAnalyticsSection() {
  const projectUsage = usageTotals(state.usage);
  const globalUsage = usageTotals(state.globalUsage);
  $("page").innerHTML = `<div class="section-stack">
    <div class="page-head"><div><h1 class="h2">Analytics</h1><div class="muted">Token usage is split by global total, current project, and task. All numbers come from backend usage DB.</div></div><button class="button" onclick="refreshAll()">Refresh</button></div>
    <div class="quick-grid">${kpi("Global Tokens", compact(globalUsage.total))}${kpi("Project Tokens", compact(projectUsage.total))}${kpi("Project Requests", projectUsage.request_count || 0)}${kpi("Project LLM Calls", projectUsage.calls || 0)}${kpi("Input Tokens", compact(projectUsage.input))}${kpi("Output Tokens", compact(projectUsage.output))}</div>
    <div class="section-grid"><section class="card card-pad"><div class="h3">Project Token Breakdown</div>${usageTable(state.globalUsage.by_project || [], ["project_id","calls","input_tokens","output_tokens","total_tokens","duration_ms"])}</section><aside class="side-stack">${usageCard(projectUsage, "Current Project Usage", state.usage.by_task || state.usage.projects || [])}${usageCard(globalUsage, "Global Usage", state.globalUsage.by_project || state.globalUsage.projects || [])}</aside></div>
    <div class="section-grid"><section class="card card-pad"><div class="h3">Task Token Breakdown</div><div class="muted" style="font-size:12px;margin-top:4px">Filtered to current project: ${esc(state.projectId)}</div>${usageTable(state.usage.by_task || [], ["task_id","calls","input_tokens","output_tokens","total_tokens","duration_ms"])}</section><aside class="side-stack">${agentUsageCard()}${healthCard()}</aside></div>
    <div class="section-grid"><section class="card card-pad"><div class="h3">Workflow Distribution</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Stage</th><th>Tasks</th><th>Share</th></tr></thead><tbody>${columns().map(c=>`<tr><td>${esc(c.title || c.status_key)}</td><td>${(c.tasks || []).length}</td><td><div class="progress"><span style="width:${Math.min(100, Math.max(3, Math.round(((c.tasks||[]).length / Math.max(1, allTasks().length)) * 100)))}%"></span></div></td></tr>`).join("")}</tbody></table></section><aside class="side-stack">${taskUsageCard(activeBoardTask())}</aside></div>
  </div>`;
}
function renderSettingsSection() {
  const global = state.globalSettings || {};
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Global Settings</h1><div class="muted">System-wide LLM APIs and route keys. Project workflow and agent settings live under Projects.</div></div><span class="badge blue">Global</span></div>
      <div class="dense-grid" style="margin-top:16px">${settingsTile("Default Route", (global.routing || {}).default || "default", "Default model route used when a project or dynamically spawned node agent has no override.")}${settingsTile("Route Keys", Object.keys(global.routing || {}).length || 0, "Route keys are model aliases. Workflow nodes choose agents and may bind those agents to a route.")}${settingsTile("LLM Providers", Object.keys(global.llms || {}).length || 0, "Configured provider blocks from llm.json or global settings API.")}</div>
      <div style="margin-top:14px" class="config-grid">${editorCard("Global LLM Catalog","Provider credentials, base URLs, and model definitions.","JSON", JSON.stringify(global.llms || {}, null, 2))}${editorCard("Global Routing Map","Model route aliases mapped to provider/model configs. These are not workflow columns or agent names.","JSON", JSON.stringify(global.routing || {}, null, 2))}<div class="side-stack">${globalRoutingSummaryCard()}${skillSummaryCard()}${pluginSummaryCard()}${settingsTile("Dynamic Node Agents", "Workflow driven", "Project workflow nodes spawn temporary agents at runtime. Only project-agent and context-indexer are built in.")}</div></div>
      <div style="margin-top:14px" class="config-grid single-row">${globalPluginCards()}</div>
      <div style="margin-top:14px" class="config-grid single-row">${pluginAgentCards()}</div>
      <div style="margin-top:14px" class="config-grid single-row">${pluginRuntimeCatalogCards()}</div>
      <div style="margin-top:14px" class="config-grid single-row">${pluginSettingsEditors()}</div>
      <div style="margin-top:14px" class="config-grid single-row">${globalSkillEditors()}</div>
    </section>
    <aside class="side-stack">${usageCard(usageTotals(state.globalUsage), "Global Usage", state.globalUsage.by_project || state.globalUsage.projects || [])}${recentEventsCard()}</aside>
  </div>`;
}
function projectTabs() {
  return [
    {key:"configuration", label:"Configuration"},
    {key:"settings", label:"Settings"},
    {key:"workflow", label:"Workflow"},
    {key:"routing", label:"Routing"},
    {key:"integrations", label:"Integrations"},
    {key:"history", label:"History"},
    {key:"activity", label:"Activity"}
  ];
}
function projectTabContent() {
  const renderers = {
    configuration: projectConfigurationTab,
    settings: projectSettingsTab,
    workflow: projectWorkflowTab,
    routing: projectRoutingTab,
    integrations: projectIntegrationsTab,
    history: projectHistoryTab,
    activity: projectActivityTab
  };
  return (renderers[state.projectTab] || projectConfigurationTab)();
}
function projectConfigurationTab() {
  return `<div class="config-grid">${editorCard("Project.MD","Project operating manual injected into project work.","Markdown", state.projectMd || "")}${editorCard("Agents","Define the agents available in this project.","JSON", JSON.stringify(state.settings.agents || defaultAgents(), null, 2))}<div class="side-stack">${workflowPresetCard()}${routingSummaryCard()}${skillSummaryCard()}</div></div><div class="config-grid single-row">${editorCard("Parameters","Configure runtime parameters and defaults.","JSON", JSON.stringify(state.settings.parameters || defaultParameters(), null, 2))}</div><div class="config-grid single-row">${projectSkillEditors()}</div>`;
}
function projectSettingsTab() {
  return `<div class="config-grid">${editorCard("Project Settings","Identity, defaults, and runtime settings for this project.","JSON", JSON.stringify({project: currentProject(), settings: state.settings}, null, 2))}${editorCard("Task Policy","How DevWerk should manage task continuity, memory and approvals.","JSON", JSON.stringify(defaultTaskPolicy(), null, 2))}<div class="side-stack">${settingsTile("Task Continuity","Conversation-driven","Same topic continues active task; explicit new work starts a task.")}${settingsTile("Approval Mode","Automatic","Kanban state machine drives work without manual tab buttons.")}${settingsTile("Memory Scope","Project + Task","Project memory is injected into every task context.")}</div></div>`;
}
function projectWorkflowTab() {
  return `<div class="config-grid">${editorCard("Workflow Definition","Columns, semantic actions, and transition rules.","JSON", JSON.stringify(state.workflow || {}, null, 2))}<div class="card card-pad"><div class="h3">Columns</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Key</th><th>Title</th><th>Tasks</th></tr></thead><tbody>${columns().map(c=>`<tr><td>${esc(c.status_key)}</td><td>${esc(c.title || STAGE_TITLES[c.status_key] || c.status_key)}</td><td>${(c.tasks || []).length}</td></tr>`).join("")}</tbody></table></div><div class="side-stack">${workflowPresetCard()}${workflowHealthSmallCard()}</div></div>`;
}
function projectRoutingTab() {
  const agents = state.settings.agents || defaultAgents();
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Agent Routing</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Agent</th><th>Role</th><th>Model Route</th><th>Tools</th></tr></thead><tbody>${Object.entries(agents).map(([id,agent])=>`<tr><td>${esc(id)}</td><td>${esc(agent.role || agent.name || "-")}</td><td>${esc(agent.model_route || agent.model || "default")}</td><td>${esc((agent.tools || []).join(", ") || "-")}</td></tr>`).join("")}</tbody></table></section><aside class="side-stack">${routingSummaryCard()}${routingCard()}</aside></div>`;
}
function projectIntegrationsTab() {
  const integrations = state.settings.integrations || state.settings.capabilities || [];
  const rows = Array.isArray(integrations) ? integrations : Object.entries(integrations).map(([name, value]) => ({name, ...(typeof value === "object" ? value : {status: String(value)})}));
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Integrations</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Name</th><th>Status</th><th>Contract</th></tr></thead><tbody>${rows.length ? rows.map(i=>`<tr><td>${esc(i.name || i.id || "-")}</td><td>${esc(i.status || i.enabled || "-")}</td><td>${esc(i.detail || i.contract || i.description || "-")}</td></tr>`).join("") : `<tr><td colspan="3" class="muted">No integrations configured in backend project settings.</td></tr>`}</tbody></table></section><aside class="side-stack">${commandsCard()}${routingSummaryCard()}</aside></div>`;
}
function projectHistoryTab() {
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Project History</div><div class="timeline-list" style="margin-top:12px">${state.events.length ? state.events.map(eventTimeline).join("") : `<div class="muted">No project history yet.</div>`}</div></section><aside class="side-stack">${recentEventsCard()}${memoryCard()}</aside></div>`;
}
function projectActivityTab() {
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Activity</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Task</th><th>Status</th><th>Updated</th><th>Priority</th></tr></thead><tbody>${allTasks().map(t=>`<tr><td>${esc(t.title)}</td><td>${esc(STAGE_TITLES[t.status_key] || t.status_key || "-")}</td><td>${relative(t.updated_at)}</td><td>${priorityLabel(t.priority)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No tasks yet.</td></tr>`}</tbody></table></section><aside class="side-stack">${recentTasksCard()}${healthCard()}</aside></div>`;
}
function renderKanbanPage() {
  $("page").innerHTML = `<div class="kanban-page"><section class="kanban-main">
    <div class="card kanban-header"><div class="title-block"><span class="folder-icon" style="width:32px;height:32px"></span><div><h1 class="h2">Workflow Pipeline <span class="soft">...</span></h1><div class="muted">${allTasks().length} tasks across ${columns().length} stages</div></div></div><div class="filter-row"><span class="muted">Quick filters:</span><span class="chip active">All</span><span class="chip">My tasks</span><span class="chip">Blocked</span><span class="chip">High priority</span><span class="muted" style="margin-left:18px">Assignee:</span><select class="select-pill"><option>All assignees</option></select><button id="createTask" class="button primary">+ Create Task</button><button class="icon-button">...</button></div></div>
    <div class="board">${columns().map(columnHtml).join("")}</div>
  </section><aside class="card inspector">${inspectorHtml()}</aside></div>`;
  $("createTask").onclick = createTaskFromPrompt;
}
function renderTaskPage() {
  const task = activeBoardTask();
  if (!task) {
    $("page").innerHTML = `<div class="card card-pad"><h1 class="h2">No task selected</h1><p class="muted">The backend returned no tasks for project ${esc(state.projectId)}. Create or run a workflow task to populate this view.</p></div>`;
    return;
  }
  const events = state.events.filter(e => !task.id || e.task_id === task.id).slice(0, 8);
  const artifacts = task.artifacts || [];
  $("page").innerHTML = `<div class="task-browser">${taskListPanel(task)}<section class="task-main">
    <div class="card task-header"><div class="breadcrumb">Projects &gt; ${esc(state.projectId)} &gt; Tasks &gt; ${esc(task.id)}</div><div class="task-title-row"><div class="task-title-left"><span class="timeline-dot"></span><h1 class="h1">${esc(task.title || "Task detail")}</h1><span class="soft">...</span></div><div class="task-actions"><button class="button">Review</button><button class="button primary">Apply</button><button class="button">Re-run</button><button class="button">Open PR</button><button class="icon-button">...</button></div></div><div class="task-meta-row"><span class="badge ${task.status_key === "failed" ? "red" : "green"}">${esc(STAGE_TITLES[task.status_key] || task.status_key || "Active")}</span><span class="meta-chip">Stage: <b>${esc(STAGE_TITLES[task.status_key] || task.status_key || "-")}</b></span><span class="meta-chip">Priority: <b>${priorityLabel(task.priority)}</b></span><span class="meta-chip">Owner: <b>${esc(taskOwner(task))}</b></span></div><div class="task-meta-row"><span class="meta-chip">Created: <b>${dateShort(task.created_at)}</b></span><span class="meta-chip">Updated: <b>${relative(task.updated_at)}</b></span><span>Task ID: <b>${esc(task.id)}</b></span><span style="margin-left:auto"><button class="button">Open in editor</button></span></div></div>
    <section class="card"><div class="detail-tabs">${taskTabs().map(tab=>`<button class="tab tab-button ${state.taskTab===tab.key?"active":""}" data-task-tab="${tab.key}">${tab.label}</button>`).join("")}</div>${taskTabContent(task, events, artifacts)}</section></section><aside class="task-side">${taskUsageCard(task)}${timelineCard(events)}${linkedFilesCard(artifacts)}${commandsCard()}${memorySnippetsCard()}</aside></div>`;
  document.querySelectorAll("[data-task-tab]").forEach(button => {
    button.onclick = () => {
      state.taskTab = button.dataset.taskTab || "summary";
      renderTaskPage();
    };
  });
}
function pipelineHtml() {
  const stages = columns().map(column => column.status_key);
  if (!stages.length) return `<div class="card pipeline"><div class="pipeline-title">Workflow Pipeline</div><div class="muted">No workflow columns configured. Use the project conversation to design this project workflow.</div></div>`;
  const active = activeStage();
  let activeSeen = false;
  return `<div class="card pipeline"><div class="pipeline-title">Workflow Pipeline</div><div class="pipeline-row">${stages.map(stage => {
    const done = !activeSeen && stage !== active;
    if (stage === active) activeSeen = true;
    return `<div class="stage ${stage === active ? "active" : done ? "done" : ""}"><span class="stage-icon">${done ? "OK" : ""}</span><span class="stage-label">${stageTitle(stage)} <b>${statusCount(stage)}</b></span></div>`;
  }).join("")}</div></div>`;
}
function healthCard(){ const failed=(currentProject().stats || {}).failed_tasks || 0; const active=activeStage(); const stages=columns().map(c=>c.status_key); return `<div class="card card-pad"><div style="display:flex;gap:10px;align-items:center"><span class="ok">${failed ? "!" : "OK"}</span><div><div class="h3">${failed ? "Attention" : "No failed tasks"}</div><div class="muted" style="font-size:12px">Derived from backend Kanban state.</div></div></div><div style="margin-top:18px" class="muted">Active Stage</div><div style="margin-top:8px"><span class="badge blue">${esc(active ? stageTitle(active) : "-")}</span></div><div class="muted" style="font-size:12px;margin-top:6px">${active ? statusCount(active) : 0} tasks in active stage</div><div class="progress"><span style="width:${active && stages.length ? Math.max(3, Math.round(((stages.indexOf(active) + 1) / stages.length) * 100)) : 0}%"></span></div></div>`; }
function usageCard(u, title="Token Usage", rows=null){ return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;gap:10px"><div class="h3">${esc(title)}</div><span class="muted" style="font-size:12px">Backend usage DB</span></div><div style="font-size:20px;font-weight:850;margin-top:12px">${compact(u.total)} total tokens</div><div class="progress"><span style="width:${u.total ? 100 : 0}%"></span></div><div class="metric-lines"><div class="metric-line"><span>Input Tokens</span><b>${compact(u.input)}</b></div><div class="metric-line"><span>Output Tokens</span><b>${compact(u.output)}</b></div><div class="metric-line"><span>LLM Calls</span><b>${compact(u.calls)}</b></div><div class="metric-line"><span>Requests</span><b>${compact(u.request_count)}</b></div></div>${usageBars(rows || state.usage.by_task || state.usage.projects || [])}</div>`; }
function routingCard(){ return `<div class="card card-pad"><div class="h3">Agent Route</div><div class="muted" style="font-size:12px;margin-top:4px">Routes map agent responsibilities to model configs; they are not model names.</div><div class="metric-lines" style="margin-top:12px"><div><div class="muted">Project Default</div><b>${esc(modelRoutes()[0] || "default")}</b></div><div><div class="muted">Project Routes</div><b>${esc(modelRoutes().join(", ") || "default")}</b></div><div><div class="muted">Thinking Mode</div><b>${esc((state.settings.parameters || {}).thinking_mode || "Balanced")}</b></div></div></div>`; }
function conversationCard(){ return `<section class="card chat-card"><div class="tabs">${conversationTabs().map(tab=>`<button class="tab tab-button ${state.conversationTab===tab.key?"active":""}" data-chat-tab="${tab.key}">${tab.label}</button>`).join("")}</div><div id="chatBody" class="chat-body">${conversationTabContent()}</div><div class="composer"><div class="composer-box"><textarea id="prompt" class="composer-input" placeholder="Message DevWerk, or use slash commands..."></textarea>${slashHintHtml()}<div class="composer-actions"><div class="tool-row"><span class="tool">A</span><span class="tool">F</span><span class="tool">&lt;/&gt;</span><span class="tool">B</span></div><div style="display:flex;gap:10px"><select class="select-pill">${modelRoutes().map(m=>`<option>${esc(m)}</option>`).join("") || "<option>default</option>"}</select><button id="send" class="send-button">></button></div></div></div></div></section>`; }
function slashHintHtml(){
  const commands = state.slashCommands && state.slashCommands.length ? state.slashCommands : [
    {command:"/goal", argument_hint:"project objective", source:"builtin"},
    {command:"/learn", argument_hint:"reusable rule", source:"builtin"},
    {command:"/distill", argument_hint:"compact this project context", source:"builtin"}
  ];
  return `<div class="slash-hint"><b>Slash commands</b>${commands.slice(0,8).map(item => `<span title="${escAttr(item.summary || "")}">${esc(item.command)}${item.argument_hint ? " " + esc(item.argument_hint) : ""}${item.source === "plugin" ? " · plugin" : ""}</span>`).join("")}</div>`;
}
function conversationHtml() {
  const msgs = state.conversation.length ? state.conversation : [{role:"assistant", content:"I will help you break this down into actionable tasks and move them through the workflow. Tell me what you want DevWerk to build, review, research, or organize."}];
  return msgs.map(message => message.role === "user"
    ? `<div class="user-bubble"><div class="message-meta">You</div>${esc(displayMessageContent(message))}</div>`
    : `<div class="message"><span class="bot-badge">D</span><div class="message-content"><div class="message-meta">DevWerk Assistant</div>${esc(displayMessageContent(message))}</div></div>`
  ).join("");
}
function conversationTabs(){ return [{key:"conversation", label:"Conversation"}, {key:"workflow_log", label:"Workflow Log"}, {key:"artifacts", label:"Artifacts"}]; }
function conversationTabContent(){ if(state.conversationTab === "workflow_log") return workflowLogHtml(state.events); if(state.conversationTab === "artifacts") return artifactsOverviewHtml(allArtifacts()); return conversationHtml(); }
function workflowLogHtml(events){ return `<div class="summary-card wide exec-log" style="border:0;padding:0"><div class="log-head"><div class="h3">Workflow Log</div><span class="muted">${events.length} events</span></div><div class="log-lines" style="margin-top:12px">${events.length ? events.map(e=>`${dateTime(e.created_at)}  ${esc(eventTitle(e))} ${esc(e.task_id || "")} ${e.to_status ? "-> " + esc(e.to_status) : ""}`).join("<br/>") : "No workflow events returned by backend."}</div></div>`; }
function artifactsOverviewHtml(artifacts){ return `<div class="section-stack">${artifacts.length ? artifacts.map(a=>`<div class="summary-card"><div class="h3">${esc(a.artifact_type || a.type || a.name || "artifact")}</div><pre class="json-panel">${esc(JSON.stringify(a.payload || a, null, 2))}</pre></div>`).join("") : `<div class="muted">No artifacts returned by backend.</div>`}</div>`; }
function wireConversation() {
  const send = $("send"); const prompt = $("prompt");
  if (!send || !prompt) return;
  send.onclick = sendProjectMessage;
  prompt.addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendProjectMessage(); }});
  document.querySelectorAll("[data-chat-tab]").forEach(button => {
    button.onclick = () => {
      state.conversationTab = button.dataset.chatTab || "conversation";
      renderProjectsPage();
    };
  });
  scrollConversationToLatest();
}
function scrollConversationToLatest() {
  const body = $("chatBody");
  if (body) body.scrollTop = body.scrollHeight;
}
async function sendProjectMessage() {
  const prompt = $("prompt"); const content = (prompt.value || "").trim();
  if (!content || state.busy) return;
  const slash = parseSlashCommand(content);
  state.busy = true; prompt.disabled = true; $("send").disabled = true;
  state.conversation.push({role:"user", content});
  renderProjectsPage();
  try {
    const result = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`, {method:"POST", body: JSON.stringify({action:slash?.action || "message", message:slash?.argument || content, messages:state.conversation, metadata:{active_task_id: state.activeTask?.id || state.activeTask?.task_id || null, slash_command: slash?.command || null}})});
    if (result && result.task_id) state.activeTask = {id: result.task_id, status_key: result.status_key || "queued"};
    await Promise.allSettled([loadConversation(), loadBoard(), loadEvents()]);
    renderProjectsPage();
  } catch (error) {
    alert(error.message || String(error));
  } finally {
    state.busy = false;
    const currentPrompt = $("prompt");
    const currentSend = $("send");
    if (currentPrompt) currentPrompt.disabled = false;
    if (currentSend) currentSend.disabled = false;
  }
}
function parseSlashCommand(content){ const match = String(content || "").trim().match(/^\/(goal|learn|distill)(?:\s+([\s\S]*))?$/i); if(!match) return null; const command = match[1].toLowerCase(); return {command, action: command, argument: (match[2] || "").trim()}; }
function recentTasksCard(){ const tasks=allTasks().slice(0,5); return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Recent Tasks</div><a class="link" href="/tasks?project_id=${escAttr(state.projectId)}">View all</a></div><div class="list">${tasks.map(t=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow"><div class="list-row-title">${esc(t.title)}</div></div><span><i class="dot ${stageColor(t.status_key)}"></i>${esc(STAGE_TITLES[t.status_key] || t.status_key || "Draft")}</span></div>`).join("")}<div class="list-row"><span>+</span><a class="link">New Task</a></div></div></div>`; }
function memoryCard(){ const mem=state.memory || {}; return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Memory Status</div><a class="link">View</a></div><div class="metric-lines"><div class="metric-line"><b>Frameworks</b><b>${countMemory(mem,"framework")}</b></div><div class="metric-line"><b>Codebase Paths</b><b>${countMemory(mem,"path")}</b></div><div class="metric-line"><b>Commands</b><b>${countMemory(mem,"command")}</b></div><div class="metric-line"><b>Recent Summaries</b><b>${countMemory(mem,"summary")}</b></div></div><div class="muted" style="margin-top:20px;font-size:12px">Last updated: ${relative(mem.updated_at)} <i class="dot green" style="float:right"></i></div></div>`; }
function recentEventsCard(){ return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Recent Events</div><a class="link">View all</a></div><div class="list">${state.events.slice(0,5).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div><div class="list-row-title">${esc(dateTime(e.created_at))} ${esc(eventTitle(e))}</div><div class="list-row-sub">${esc(e.task_title || e.to_status || state.projectId)}</div></div></div>`).join("") || `<div class="muted">No events yet.</div>`}</div></div>`; }
function liveLogCard(){ const rows=(state.liveLogs.length ? state.liveLogs : state.events).slice(-80).reverse(); return `<div class="card card-pad live-log-card"><div class="side-title"><div><div class="h3">Live Workflow Log</div><div class="muted" style="font-size:12px">WebSocket ${esc(state.streamStatus)} for ${esc(state.projectId)}</div></div><span class="badge ${state.streamStatus === "connected" ? "green" : "orange"}">${esc(state.streamStatus)}</span></div><div class="live-log-lines">${rows.length ? rows.map(liveLogLine).join("") : `<div class="muted">No workflow events streamed yet.</div>`}</div></div>`; }
function liveLogLine(event){ const payload=event.payload || {}; return `<div class="live-log-line"><span>${esc(dateTime(event.created_at))}</span><b>${esc(eventTitle(event))}</b><small>${esc(event.task_title || event.task_id || payload.summary || payload.reason || "")}</small></div>`; }
function kpi(label,value){ return `<div class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div></div>`; }
function projectCard(project){ const stats=project.stats || {}; const health=projectHealth(stats); return `<button class="project-card ${project.id === state.projectId ? "selected" : ""}" data-project-card="${escAttr(project.id)}"><div class="card-top"><div class="project-title">${esc(project.name || project.id)}</div><span class="badge ${projectStatus(project).badge}">${projectStatus(project).label}</span></div><div class="muted" style="font-size:12px;line-height:1.45;margin-top:12px">${esc(project.description || project.id)}<br/>${esc(modelRoutes()[0] || "default")}</div><div class="card-meta"><div><div class="meta-label">Tasks</div><div class="meta-value">${stats.tasks || 0}</div></div><div><div class="meta-label">Active</div><div class="meta-value">${stats.active_tasks || 0}</div></div><div><div class="meta-label">Last activity</div><div class="meta-value">${relative(project.updated_at)}</div></div><div><div class="meta-label">Health</div><div class="meta-value">${health.label}</div></div></div>${projectUsageMini(stats)}</button>`; }
function editorCard(title, desc, mode, content){ const lines=String(content || "").split("\n"); return `<div class="editor-card card" data-editor-title="${escAttr(title)}" data-editor-mode="${escAttr(mode)}"><div class="editor-head"><div><div class="h3">${title}</div><div class="muted" style="font-size:12px;margin-top:4px">${desc}</div></div><button class="small-button" data-action="editor-format">${mode}</button></div><div class="editor"><div class="line-nos">${lines.map((_,i)=>i+1).join("<br/>")}</div><textarea class="code-editor" spellcheck="false">${esc(lines.join("\n"))}</textarea></div><div class="editor-foot"><span class="muted">Loaded from backend API</span><span><button class="small-button" data-action="editor-format">Format</button> <button class="small-button" data-action="editor-save">Save</button></span></div></div>`; }
function workflowPresetCard(){ const workflow=state.workflow || {}; const columnsCount=(workflow.columns || workflow.stages || columns() || []).length; return `<div class="card side-card"><div class="h3">Workflow Definition</div><div class="muted" style="font-size:12px;margin-top:4px">Loaded from backend project workflow.</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Name</b><span>${esc(workflow.name || "default")}</span></div><div class="metric-line"><b>Columns</b><span>${columnsCount}</span></div><div class="metric-line"><b>Source</b><span>${workflow.source ? esc(workflow.source) : "project settings"}</span></div></div></div>`; }
function skillSummaryCard(){ return `<div class="card side-card"><div class="h3">Skills</div><div class="muted" style="font-size:12px;margin-top:4px">Agents load SKILL.md entries from global and project scope.</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Global</b><span>${state.globalSkills.length}</span></div><div class="metric-line"><b>Project</b><span>${state.projectSkills.length}</span></div><div class="metric-line"><b>Entrypoint</b><span>SKILL.md</span></div></div></div>`; }
function pluginSummaryCard(){ const enabled=(state.globalPlugins || []).filter(plugin => plugin.enabled !== false).length; const skills=(state.globalPlugins || []).reduce((sum, plugin) => sum + Number(plugin.skills_count || 0), 0); return `<div class="card side-card"><div class="h3">Plugins</div><div class="muted" style="font-size:12px;margin-top:4px">Claude-style plugin packages can provide skills, commands, agent templates, hooks, and MCP server configs.</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Installed</b><span>${state.globalPlugins.length}</span></div><div class="metric-line"><b>Enabled</b><span>${enabled}</span></div><div class="metric-line"><b>Plugin Skills</b><span>${skills}</span></div><div class="metric-line"><b>Plugin Agents</b><span>${state.pluginAgents.length}</span></div><div class="metric-line"><b>Hooks</b><span>${state.pluginHooks.length}</span></div><div class="metric-line"><b>MCP Servers</b><span>${state.pluginMcpServers.length}</span></div></div></div>`; }
function globalPluginCards(){
  const plugins = state.globalPlugins || [];
  const importPanel = `<div class="card card-pad"><div class="h3">Import Plugin</div><div class="muted" style="font-size:12px;margin-top:4px">Import a local Claude-style plugin directory containing .claude-plugin/plugin.json.</div><div style="display:flex;gap:8px;margin-top:12px"><input id="pluginImportPath" class="input" placeholder="D:\\workspace\\codex\\devwerk\\3rd\\claude-code\\plugins\\frontend-design" /><button class="btn small" data-plugin-validate="true">Validate</button><button class="btn small" data-plugin-import="true">Import</button></div>${pluginValidationPreview()}</div>`;
  const marketplacePanel = `<div class="card card-pad"><div class="h3">Marketplace</div><div class="muted" style="font-size:12px;margin-top:4px">Load a Claude-style .claude-plugin/marketplace.json and import a listed plugin by name.</div><div style="display:grid;gap:8px;margin-top:12px"><input id="pluginMarketplacePath" class="input" placeholder="D:\\workspace\\codex\\devwerk\\3rd\\claude-code\\.claude-plugin\\marketplace.json" /><input id="pluginMarketplaceName" class="input" placeholder="frontend-design" /><div style="display:flex;gap:8px"><button class="btn small" data-plugin-marketplace-load="true">Load Marketplace</button><button class="btn small" data-plugin-marketplace-import="true">Import Listed Plugin</button></div></div>${marketplacePreview()}</div>`;
  if(!plugins.length) return `<section class="card card-pad"><div class="h3">Global Plugins</div><div class="muted">No global plugins returned by backend. Install Claude-style plugins under config/plugins or use the /v1/plugins API.</div><div class="plugin-grid" style="margin-top:14px">${importPanel}${marketplacePanel}</div></section>`;
  return `<section class="card card-pad"><div class="page-head"><div><div class="h3">Global Plugins</div><div class="muted">Global plugin packages expose capabilities to workflow-spawned agents. Skills are loaded through each plugin's skills/*/SKILL.md entries.</div></div><span class="badge blue">${plugins.length} installed</span></div><div class="plugin-grid" style="margin-top:14px">${importPanel}${marketplacePanel}${plugins.map(plugin => `<div class="card card-pad"><div class="page-head"><div><div class="h3">${esc(plugin.name || plugin.id)}</div><div class="muted">${esc(plugin.description || "No description")}</div></div><span class="badge ${plugin.enabled === false ? "" : "green"}">${plugin.enabled === false ? "Disabled" : "Enabled"}</span></div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Skills</b><span>${plugin.skills_count || 0}</span></div><div class="metric-line"><b>Commands</b><span>${plugin.commands_count || 0}</span></div><div class="metric-line"><b>Agents</b><span>${plugin.agents_count || 0}</span></div><div class="metric-line"><b>MCP Servers</b><span>${plugin.mcp_servers_count || 0}</span></div></div><div style="display:flex;gap:8px;margin-top:14px"><button class="btn small" data-plugin-toggle="${escAttr(plugin.id)}" data-enabled="${plugin.enabled === false ? "true" : "false"}">${plugin.enabled === false ? "Enable" : "Disable"}</button><button class="btn small" data-plugin-remove="${escAttr(plugin.id)}">Remove</button><span class="muted" style="align-self:center;font-size:12px">${esc(plugin.version || "")}</span></div></div>`).join("")}</div></section>`;
}
function pluginAgentCards(){
  const agents = state.pluginAgents || [];
  return `<section class="card card-pad"><div class="page-head"><div><div class="h3">Plugin Agents</div><div class="muted">Enabled plugin agent markdown is exposed as selectable runtime agent context. Use the stable agent_id in project workflow or agent settings.</div></div><span class="badge blue">${agents.length} available</span></div><div class="plugin-grid" style="margin-top:14px">${agents.length ? agents.map(agent => `<div class="card card-pad"><div class="h3">${esc(agent.summary || agent.id || agent.agent_id)}</div><div class="muted" style="font-size:12px">${esc(agent.agent_id || "")}</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Plugin</b><span>${esc(agent.plugin_id || "-")}</span></div><div class="metric-line"><b>MCP Servers</b><span>${(agent.mcp_servers || []).length}</span></div><div class="metric-line"><b>Chars</b><span>${agent.chars || 0}</span></div></div></div>`).join("") : `<div class="muted">No enabled plugin agents returned by backend.</div>`}</div></section>`;
}
function pluginRuntimeCatalogCards(){
  const hooks = state.pluginHooks || [];
  const servers = state.pluginMcpServers || [];
  return `<section class="card card-pad"><div class="page-head"><div><div class="h3">Plugin Runtime Catalog</div><div class="muted">Enabled plugin hooks and MCP server configs are loaded from backend APIs for future workflow/runtime integration.</div></div><span class="badge blue">${hooks.length + servers.length} entries</span></div><div class="config-grid" style="margin-top:14px"><div class="card card-pad"><div class="h3">Hooks</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Hook</th><th>Plugin</th><th>Source</th></tr></thead><tbody>${hooks.length ? hooks.map(hook => `<tr><td>${esc(hook.hook_id || hook.id)}</td><td>${esc(hook.plugin_id || "-")}</td><td>${esc(hook.path || "manifest")}</td></tr>`).join("") : `<tr><td colspan="3" class="muted">No enabled plugin hooks returned by backend.</td></tr>`}</tbody></table></div><div class="card card-pad"><div class="h3">MCP Servers</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Server</th><th>Plugin</th><th>Command</th></tr></thead><tbody>${servers.length ? servers.map(server => `<tr><td>${esc(server.server_ref || server.id)}</td><td>${esc(server.plugin_id || "-")}</td><td>${esc((server.config || {}).command || "-")}</td></tr>`).join("") : `<tr><td colspan="3" class="muted">No enabled plugin MCP servers returned by backend.</td></tr>`}</tbody></table></div></div></section>`;
}
function pluginSettingsEditors(){
  const plugins = state.globalPlugins || [];
  if(!plugins.length) return "";
  return `<section class="card card-pad"><div class="page-head"><div><div class="h3">Plugin Settings</div><div class="muted">Global per-plugin settings use frontmatter plus markdown, inspired by Claude Code .local.md configuration files.</div></div><span class="badge blue">Markdown</span></div><div class="config-grid single-row" style="margin-top:14px">${plugins.map(plugin => {
    const settings = state.pluginSettings[plugin.id] || {};
    const content = settings.content || `---\nenabled: ${plugin.enabled === false ? "false" : "true"}\n---\n\n# ${plugin.name || plugin.id} Settings\n`;
    return editorCard(`Plugin Settings: ${plugin.id}`, "Frontmatter is parsed by the backend and markdown can carry plugin-specific instructions.", "Markdown", content);
  }).join("")}</div></section>`;
}
function marketplacePreview(){
  const plugins = state.pluginMarketplace?.plugins || [];
  if(!plugins.length) return "";
  return `<div class="muted" style="font-size:12px;margin-top:10px">${esc(state.pluginMarketplace.marketplace?.name || "Marketplace")} loaded: ${plugins.slice(0,4).map(plugin => `${plugin.name}${plugin.installed ? " (installed)" : ""}`).join(", ")}${plugins.length > 4 ? "..." : ""}</div>`;
}
function pluginValidationPreview(){
  const validation = state.pluginValidation?.validation || state.pluginValidation;
  if(!validation) return "";
  const issues = validation.issues || [];
  const warnings = validation.warnings || [];
  const label = validation.ok ? "Valid plugin" : "Invalid plugin";
  const details = issues.length ? issues.join("; ") : (warnings.length ? warnings.join("; ") : `${validation.plugin_id || "plugin"} can be imported.`);
  return `<div class="muted" style="font-size:12px;margin-top:10px"><b>${esc(label)}</b>: ${esc(details)}</div>`;
}
function globalSkillEditors(){ const skills = state.globalSkills || []; if(!skills.length) return `<div class="card card-pad"><div class="h3">Global Skill Catalog</div><div class="muted">No global SKILL.md files returned by backend.</div></div>`; return skills.map(skill => editorCard(`Global Skill: ${skill.id}`, `Global SKILL.md (${skill.scope || "global"}).`, "Markdown", skill.content || skill.summary || "")).join(""); }
function projectSkillEditors(){ const skills = state.projectSkills || []; if(!skills.length) return `<div class="card card-pad"><div class="h3">Project Skills</div><div class="muted">No project-level SKILL.md entries configured yet. Use /learn for memory, or create project skills through the skills API.</div></div>`; return skills.map(skill => editorCard(`Project Skill: ${skill.id}`, `Project-scoped SKILL.md (${skill.enabled === false ? "disabled" : "enabled"}).`, "Markdown", skill.content || skill.summary || "")).join(""); }
function routingSummaryCard(){ const params=state.settings.parameters || {}; return `<div class="card side-card"><div class="h3">Project Route Summary</div><div class="muted" style="font-size:12px;margin-top:4px">Project agent routes. Route keys are responsibilities, not model names.</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Default Route</b><span>${esc(modelRoutes()[0] || "default")}</span></div><div class="metric-line"><b>Route Count</b><span>${modelRoutes().length}</span></div><div class="metric-line"><b>Strategy</b><span>${esc(params.routing_strategy || "project override")}</span></div></div></div>`; }
function globalRoutingSummaryCard(){ const routing=state.globalSettings.routing || {}; const rows=Object.entries(routing); return `<div class="card side-card"><div class="h3">Global Route Map</div><div class="muted" style="font-size:12px;margin-top:4px">Agent responsibility -> model route.</div><div class="metric-lines" style="margin-top:12px">${rows.length ? rows.slice(0,8).map(([key,value])=>`<div class="metric-line"><b>${esc(routeKeyLabel(key))}</b><span>${esc(value)}</span></div>`).join("") : `<div class="muted">No global routing returned by backend.</div>`}</div></div>`; }
function teamCard(){ const access=state.settings.access || state.settings.team || {}; const members=Array.isArray(access.members) ? access.members : []; return `<div class="card side-card"><div class="h3">Team & Access</div><div class="muted" style="font-size:12px;margin-top:4px">Access data from project settings.</div>${members.length ? `<div style="display:flex;gap:6px;margin:14px 0">${members.map(member=>`<span class="avatar">${esc(initials(member.name || member.id || member.email || "?"))}</span>`).join("")}</div>` : `<div class="muted" style="margin-top:14px">No team access configured.</div>`}<div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Members</b><span>${members.length}</span></div><div class="metric-line"><b>Role</b><span>${esc(access.role || "-")}</span></div></div></div>`; }
function infoItem(label,value){ return `<div class="info-item"><span class="info-icon">I</span><div><div class="muted">${label}</div><b>${esc(value || "-")}</b></div></div>`; }
function columnHtml(col){ return `<div class="column ${col.status_key === activeStage() ? "active" : ""}"><div class="col-head"><span>${esc(col.title || STAGE_TITLES[col.status_key] || col.status_key)} <span class="count">${(col.tasks || []).length}</span></span><span>+</span></div><div class="cards">${(col.tasks || []).slice(0,5).map(taskCardHtml).join("")}<div class="add-task">+ Add task</div></div></div>`; }
function taskCardHtml(t){ return `<button class="task-card" data-task="${escAttr(t.id)}"><div class="task-id">${esc(shortTaskId(t.id))}</div><div class="task-title">${esc(t.title || "Untitled task")}</div><div class="task-desc">${esc(t.description || "Workflow-managed task.")}</div><div class="tags"><span class="tag">${esc(t.status_key || "task")}</span></div><div class="task-foot"><span class="avatar ${avatarColor(t.priority)}" style="width:24px;height:24px;font-size:10px">EE</span><span class="priority"><i class="dot ${priorityColor(t.priority)}"></i>${priorityLabel(t.priority)}</span></div><div class="task-foot"><span>${relative(t.updated_at)}</span><span>${(t.metadata && t.metadata.files) || 0} files</span></div></button>`; }
function inspectorHtml(){ const tasks=allTasks(); const failed=tasks.filter(t=>t.status_key === "failed"); const atRisk=tasks.filter(t=>Number(t.priority || 0) >= 2 && t.status_key !== "done"); const completed=tasks.filter(t=>["done","verified"].includes(t.status_key)); const score=workflowScore(tasks); return `<div style="display:flex;justify-content:space-between"><div class="h3">Workflow Health</div><span>^</span></div><div class="ring"><div class="ring-inner"><div class="ring-number">${score}</div><div class="muted" style="font-size:11px">Derived Score</div></div></div><div class="metric-lines"><div class="metric-line"><span><i class="dot green"></i> On Track</span><b>${Math.max(0, tasks.length - failed.length - atRisk.length)}</b></div><div class="metric-line"><span><i class="dot orange"></i> At Risk</span><b>${atRisk.length}</b></div><div class="metric-line"><span><i class="dot red"></i> Failed</span><b>${failed.length}</b></div></div><hr style="border:none;border-top:1px solid var(--border);width:100%"/><div><b>Throughput</b><div class="kpi-value">${completed.length}</div><div class="muted">done or verified tasks from backend board</div></div><div><div style="display:flex;justify-content:space-between"><b>Failed Tasks</b><a class="link">View all</a></div>${failed.length ? failed.slice(0,2).map(t=>`<div class="blocked"><div class="task-id">${esc(shortTaskId(t.id))} <span class="priority"><i class="dot ${priorityColor(t.priority)}"></i>${priorityLabel(t.priority)}</span></div><b>${esc(t.title)}</b><div class="muted">${esc(t.description || "Waiting for workflow progress.")}<br/>Since ${relative(t.updated_at)}</div></div>`).join("") : `<div class="muted" style="margin-top:10px">No failed tasks returned by backend.</div>`}</div><div><div style="display:flex;justify-content:space-between"><b>Recent Activity</b><a class="link">View all</a></div><div class="list" style="margin-top:10px">${state.events.slice(0,4).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow">${esc(eventTitle(e))}</div><span class="muted">${relative(e.created_at)}</span></div>`).join("") || `<div class="muted">No recent events.</div>`}</div></div><button class="button">Export Board</button>`; }
function timelineCard(events){ return `<div class="card side-card"><div class="side-title"><div class="h3">Timeline</div><a class="link">View all</a></div><div class="list">${(events.length ? events : state.events.slice(0,5)).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow">${esc(eventTitle(e))}</div><span class="muted">${dateTime(e.created_at)}</span></div>`).join("") || "<div class='muted'>No timeline yet.</div>"}</div></div>`; }
function linkedFilesCard(artifacts){ return `<div class="card side-card"><div class="side-title"><div class="h3">Linked files</div><a class="link">View diff</a></div>${filesHtml(artifacts)}<a class="link">Show more files</a></div>`; }
function commandsCard(){ const commands=configuredCommands(); return `<div class="card side-card"><div class="h3" style="margin-bottom:10px">Related commands</div>${commands.length ? commands.map(c=>`<span class="command">${esc(c)}</span>`).join("") : `<div class="muted">No commands configured by backend project settings or global plugins.</div>`}</div>`; }
function memorySnippetsCard(){ return `<div class="card side-card"><div class="side-title"><div class="h3">Memory / Context</div><a class="link">View in Memory</a></div><div class="muted" style="font-size:12px">Relevant snippets</div><div class="snippet"><b>Project Memory</b><br/>${esc(JSON.stringify(state.memory || {}).slice(0,120) || "No memory recorded yet.")}</div><div class="snippet"><b>Workflow Context</b><br/>Kanban remains the state-machine driver.</div></div>`; }
function filesHtml(artifacts){ const paths=[]; (artifacts || []).forEach(a => { if (a.path) paths.push(a.path); const payload = a.payload || {}; (payload.changed_paths || payload.paths || []).forEach(x => paths.push(String(x))); }); const list=paths.slice(0,6); return `<div class="files" style="margin-top:10px">${list.length ? list.map(p=>`<div class="files-row"><span>${esc(p)}</span><span class="muted">artifact</span></div>`).join("") : `<div class="muted">No linked files returned by backend artifacts.</div>`}</div>`; }
function taskListPanel(active){ const tasks=allTasks(); return `<aside class="card task-list-panel"><div class="page-head" style="align-items:flex-start"><div><div class="h3">Task List</div><div class="muted" style="font-size:12px">${tasks.length} tasks in current project</div></div><button class="small-button" onclick="createTaskFromPrompt()">New</button></div>${tasks.length ? tasks.map(t=>`<button class="task-list-item ${active && active.id===t.id ? "active" : ""}" data-task="${escAttr(t.id)}"><div class="task-list-title">${esc(t.title || "Untitled task")}</div><div class="task-list-meta"><span>${esc(STAGE_TITLES[t.status_key] || t.status_key || "-")}</span><span>${compact(taskUsageTotals(t.id).total)} tokens</span></div></button>`).join("") : `<div class="muted">No tasks returned by backend.</div>`}</aside>`; }
function taskTabs(){ return [{key:"summary", label:"Summary"}, {key:"plan", label:"Plan"}, {key:"diff", label:"Diff / Artifacts"}, {key:"events", label:"Events"}, {key:"memory", label:"Memory Context"}]; }
function taskTabContent(task, events, artifacts){ const renderers={summary:()=>taskSummaryTab(task, events, artifacts), plan:()=>taskPlanTab(task, artifacts), diff:()=>taskDiffTab(artifacts), events:()=>taskEventsTab(events), memory:()=>taskMemoryTab(task)}; return (renderers[state.taskTab] || renderers.summary)(); }
function taskSummaryTab(task, events, artifacts){ return `<div class="summary-grid">
      <div class="summary-card"><div class="h3">What was done</div><p class="muted">${esc(task.description || "No task description returned by backend.")}</p><div class="h3">Acceptance criteria</div><ul class="clean"><li>Task has workflow status: ${esc(task.status_key || "-")}</li><li>${events.length} task events returned</li><li>${artifacts.length} artifacts returned</li></ul></div>
      <div class="summary-card"><div class="h3">Scope</div><ul class="clean"><li>Project: ${esc(task.project_id || state.projectId)}</li><li>Status: ${esc(task.status_key || "-")}</li><li>Priority: ${esc(String(task.priority || 0))}</li></ul></div>
      <div class="summary-card wide"><div class="h3">Checklist</div><div class="check-list">${["Plan recorded","Context gathered","Implementation tracked","Review evidence available","Verification tracked","Memory updated"].map(x=>`<div class="check-item"><span class="ok">${taskHasEvidence(task, x) ? "OK" : "-"}</span><span>${x}</span></div>`).join("")}</div></div>
      <div class="summary-card"><div class="h3">Touched paths</div>${filesHtml(artifacts)}<a class="link">Show more files</a></div>
      <div class="summary-card"><div class="h3">Assistant notes</div><p class="muted">${esc(latestArtifactSummary(task) || "No assistant notes recorded yet.")}</p><div class="soft" style="font-size:12px">Updated <span style="float:right">${relative(task.updated_at)}</span></div></div>
      <div class="summary-card wide exec-log"><div class="log-head"><div class="h3">Execution log (reasoning + actions)</div><button class="small-button">Copy log</button></div><div class="log-body"><div class="steps">${columns().slice(0,6).map(c=>`<div class="step ${c.status_key===task.status_key?"active":""}"><span class="timeline-dot" style="width:12px;height:12px"></span><b>${esc(c.title)}</b><span>${statusCount(c.status_key)}</span></div>`).join("")}</div><div class="log-lines">${events.length ? events.map(e=>`${dateTime(e.created_at)}  ${esc(e.event_type)} ${esc(e.from_status||"")} ${e.to_status ? "-> "+esc(e.to_status) : ""}`).join("<br/>") : "No task events recorded yet."}</div></div></div>
    </div>`; }
function taskPlanTab(task, artifacts){ const phases=artifacts.filter(a=>String(a.artifact_type || a.type || "").includes("phase") || String(a.artifact_type || a.type || "").includes("artifact") || String(a.artifact_type || a.type || "").includes("workflow")); return `<div class="summary-grid"><div class="summary-card wide"><div class="h3">Phase Artifacts</div>${phases.length ? phases.map(a=>`<pre class="json-panel">${esc(JSON.stringify(a.payload || a, null, 2))}</pre>`).join("") : `<div class="muted">No phase artifacts returned by backend for task ${esc(task.id)}.</div>`}</div></div>`; }
function taskDiffTab(artifacts){ return `<div class="summary-grid"><div class="summary-card wide"><div class="h3">Diff / Artifacts</div>${artifacts.length ? artifacts.map(a=>`<pre class="json-panel">${esc(JSON.stringify(a.payload || a, null, 2))}</pre>`).join("") : `<div class="muted">No artifacts returned by backend.</div>`}</div></div>`; }
function taskEventsTab(events){ return `<div class="summary-grid"><div class="summary-card wide"><div class="h3">Events</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Time</th><th>Event</th><th>Transition</th><th>Detail</th></tr></thead><tbody>${events.length ? events.map(e=>`<tr><td>${esc(dateTime(e.created_at))}</td><td>${esc(eventTitle(e))}</td><td>${esc(e.from_status || "")}${e.to_status ? " -> " + esc(e.to_status) : ""}</td><td>${esc(JSON.stringify(e.payload || {}).slice(0, 160))}</td></tr>`).join("") : `<tr><td colspan="4" class="muted">No task events returned by backend.</td></tr>`}</tbody></table></div></div>`; }
function taskMemoryTab(task){ return `<div class="summary-grid"><div class="summary-card wide"><div class="h3">Memory Context</div><pre class="json-panel">${esc(JSON.stringify({project_memory: state.memory || {}, task_metadata: task.metadata || {}}, null, 2))}</pre></div></div>`; }
function taskHasEvidence(task, label){ const text=JSON.stringify(task || {}).toLowerCase(); return text.includes(label.split(" ")[0].toLowerCase()); }
function allArtifacts(){ return allTasks().flatMap(task => task.artifacts || []); }
function usageTable(rows, keys){ return `<table class="data-table" style="margin-top:12px"><thead><tr>${keys.map(key=>`<th>${esc(usageColumnLabel(key))}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map(row=>`<tr>${keys.map(key=>`<td>${usageCell(row, key)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${keys.length}" class="muted">No usage rows returned by backend for this scope.</td></tr>`}</tbody></table>`; }
function usageCell(row, key){ const value=row[key]; if(key === "duration_ms") return esc(duration(value)); if(key.endsWith("tokens")) return esc(compact(value)); if(key === "task_id") return esc(value || "Unassigned task"); return esc(value ?? "-"); }
function usageColumnLabel(key){ return ({project_id:"Project", task_id:"Task", calls:"LLM Calls", input_tokens:"Input", output_tokens:"Output", total_tokens:"Total", duration_ms:"Duration", successful_calls:"Successful"})[key] || key; }
function taskUsageTotals(taskId){ const row=(state.usage.by_task || []).find(item => String(item.task_id || "") === String(taskId || "")); return usageTotals({totals: row || {}, request_count: row ? row.calls : 0}); }
function taskUsageCard(task){ const totals=taskUsageTotals(task && task.id); return `<div class="card side-card"><div class="h3">Task Token Usage</div><div class="muted" style="font-size:12px;margin-top:4px">${task ? esc(task.id) : "No task selected"}</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><span>Total</span><b>${compact(totals.total)}</b></div><div class="metric-line"><span>Input</span><b>${compact(totals.input)}</b></div><div class="metric-line"><span>Output</span><b>${compact(totals.output)}</b></div><div class="metric-line"><span>LLM Calls</span><b>${compact(totals.calls)}</b></div></div></div>`; }
function agentUsageCard(){ return `<div class="card side-card"><div class="h3">Agent Usage</div>${usageTable(state.usage.by_agent || [], ["agent_name","calls","input_tokens","output_tokens","total_tokens"])}</div>`; }
function eventRow(event){ const payload = event.payload || {}; return `<tr><td>${esc(dateTime(event.created_at))}</td><td>${esc(eventTitle(event))}</td><td>${esc(event.task_title || event.task_id || "-")}</td><td>${esc(event.from_status || "")}${event.to_status ? " -> " + esc(event.to_status) : ""}</td><td>${esc(payload.reason || payload.summary || payload.action || JSON.stringify(payload).slice(0, 120) || "-")}</td></tr>`; }
function eventTimeline(event){ const payload = event.payload || {}; return `<div class="timeline-item"><span class="timeline-dot"></span><div><b>${esc(eventTitle(event))}</b><div class="muted">${esc(payload.reason || payload.summary || payload.action || event.task_title || event.task_id || state.projectId)}</div></div><span class="muted">${relative(event.created_at)}</span></div>`; }
function memoryBucket(title, items){ const list = Array.isArray(items) ? items : []; return `<div class="card card-pad"><div class="h3">${title}</div><div class="pill-list">${list.length ? list.slice(-18).map(item=>`<span class="pill">${esc(typeof item === "string" ? item : JSON.stringify(item))}</span>`).join("") : `<span class="muted">No ${title.toLowerCase()} recorded yet.</span>`}</div></div>`; }
function memorySummaries(mem){ const summaries = Array.isArray(mem.phase_summaries) ? mem.phase_summaries : []; return `<div class="card card-pad"><div class="h3">Recent Summaries</div><div class="timeline-list" style="margin-top:12px">${summaries.length ? summaries.slice(-6).reverse().map(item=>`<div class="timeline-item"><span class="timeline-dot"></span><div><b>${esc(item.phase || item.task_id || "summary")}</b><div class="muted">${esc(item.summary || JSON.stringify(item).slice(0, 160))}</div></div><span class="muted">${relative(item.created_at || item.updated_at)}</span></div>`).join("") : `<div class="muted">No compact summaries yet.</div>`}</div></div>`; }
function settingsTile(title, value, detail){ return `<div class="card card-pad"><div class="h3">${title}</div><div class="kpi-value" style="font-size:18px">${esc(value)}</div><div class="muted" style="font-size:12px;line-height:1.55">${esc(detail)}</div></div>`; }
function workflowHealthSmallCard(){ return `<div class="card card-pad"><div class="h3">Workflow Health</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><span><i class="dot green"></i> On Track</span><b>${allTasks().filter(t=>t.status_key !== "failed").length}</b></div><div class="metric-line"><span><i class="dot red"></i> Failed</span><b>${allTasks().filter(t=>t.status_key === "failed").length}</b></div><div class="metric-line"><span><i class="dot blue"></i> Active Stage</span><b>${esc(STAGE_TITLES[activeStage()] || activeStage())}</b></div></div></div>`; }
function defaultTaskPolicy(){ return {task_identity:"conversation_groups_related_messages", new_task_trigger:"explicit_new_work_or_agent_decision", approval_mode:"auto", memory_policy:"task_and_project", workflow_driver:"kanban_state_machine", manual_actions:["retry","abandon"]}; }
function activeSection(){ const hash = location.hash.replace("#", "").trim().toLowerCase(); return ["events","memory","analytics","settings"].includes(hash) ? hash : ""; }
function activeNav(){ return state.section || state.page; }
function configuredCommands(){ const params=state.settings.parameters || {}; const requests=params.post_apply_tool_requests || params.tool_requests || params.commands || []; const projectCommands = Array.isArray(requests) ? requests.map(item => Array.isArray(item.command) ? item.command.join(" ") : item.command || item.name || item.tool || String(item)).filter(Boolean) : []; const pluginCommands = (state.pluginCommands || []).map(command => command.slash || `/${command.command_id}`).filter(Boolean); return [...projectCommands, ...pluginCommands]; }
function projectHealth(stats){ const failed=Number(stats.failed_tasks || 0); const active=Number(stats.active_tasks || 0); if(failed) return {label:"Attention", badge:"orange", dot:"orange"}; if(active) return {label:"Active", badge:"green", dot:"green"}; return {label:"Idle", badge:"blue", dot:"blue"}; }
function projectSpark(stats){ const values=[stats.request_count || 0, stats.llm_calls || 0, stats.input_tokens || 0, stats.output_tokens || 0].map(Number); const max=Math.max(...values, 1); return `<div class="mini-spark">${values.map(v=>`<i style="height:${Math.max(3, Math.round((v / max) * 24))}px"></i>`).join("")}</div>`; }
function projectUsageMini(stats){ const values=[stats.request_count || 0, stats.llm_calls || 0, stats.input_tokens || 0, stats.output_tokens || 0].map(Number); const total=values.reduce((a,b)=>a+b,0); if(!total) return `<div class="mini-usage"><div class="mini-usage-label">Usage mix</div><div class="mini-usage-legend">No usage</div></div>`; const max=Math.max(...values, 1); return `<div class="mini-usage"><div class="mini-usage-label">Usage mix</div><div class="mini-usage-bars">${values.map(v=>`<i style="height:${Math.max(3, Math.round((v / max) * 24))}px"></i>`).join("")}</div><div class="mini-usage-legend">Req / LLM / In / Out</div></div>`; }
function workflowScore(tasks){ if(!tasks.length) return 0; const failed=tasks.filter(t=>t.status_key==="failed").length; const atRisk=tasks.filter(t=>Number(t.priority || 0) >= 2 && t.status_key !== "done").length; return Math.max(0, Math.round(100 - (failed / tasks.length) * 70 - (atRisk / tasks.length) * 20)); }
function usageBars(rows){ rows = rows || []; if(!rows.length) return `<div class="muted" style="margin-top:12px">No usage rows returned by backend.</div>`; const values=rows.slice(0,24).map(row=>Number(row.total_tokens || 0)); const max=Math.max(...values, 1); return `<div class="muted" style="font-size:11px;margin-top:12px">Token bars by usage row</div><div class="sparkbars">${values.map(value=>`<i style="height:${Math.max(3, Math.round((value / max) * 52))}px"></i>`).join("")}</div>`; }
function initials(value){ const words=String(value || "?").trim().split(/\s+/).filter(Boolean); return (words.length >= 2 ? words[0][0] + words[1][0] : words[0]?.slice(0,2) || "?").toUpperCase(); }
function taskOwner(task){ const metadata=task.metadata || {}; return metadata.owner || metadata.assignee || task.owner || task.assignee || "Unassigned"; }
function pageUrlForCurrentView(){ const base = state.page === "projects" ? "/dashboard" : state.page === "kanban" ? "/kanban" : state.page === "tasks" ? "/tasks" : "/workbench"; return `${base}?project_id=${encodeURIComponent(state.projectId)}${state.section ? "#" + encodeURIComponent(state.section) : ""}`; }
function normalizeMessages(messages) {
  const seen = new Set();
  const out = [];
  for (const message of messages) {
    const content = displayMessageContent(message).trim();
    if (!content) continue;
    const key = [message.role || "assistant", message.kind || "message", message.task_id || "", content].join("\u0001");
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({...message, content});
  }
  return out;
}
function displayMessageContent(message) {
  const content = String(message && message.content != null ? message.content : "");
  const parsed = parseJsonDecision(content);
  if (!parsed) return content;
  if (parsed.reply && !looksLikeJson(parsed.reply)) return String(parsed.reply);
  if (parsed.action === "start_task") return "Starting the workflow task.";
  if (parsed.action === "continue_task") return "Continuing the active workflow task.";
  if (parsed.action === "save_design" || parsed.action === "design") return "Updating the project workflow design.";
  return "Project conversation updated.";
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
  let depth = 0, inString = false, escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === "\"") inString = false;
      continue;
    }
    if (ch === "\"") inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return i; }
  }
  return -1;
}
function looksLikeJson(value) {
  const text = String(value || "").trim();
  return text.startsWith("{") || text.startsWith("[") || (text.includes('"action"') && text.includes('"reply"'));
}
function defaultAgents(){ return {"dev-assistant":{name:"DevWerk Assistant",role:"primary",description:"General purpose development assistant",model_route:"default",tools:["code","search","file_editor","terminal"]}}; }
function defaultParameters(){ return {model:modelRoutes()[0] || "default",temperature:0.2,max_tokens:8192,top_p:1,stream:true,thinking_mode:"balanced",retry:{attempts:3,backoff_ms:500}}; }
async function createProjectFromPrompt(){ openTextDialog({title:"New Project", label:"Project name", defaultValue:"Untitled Project", submitText:"Create Project", onSubmit: async name => { const id=`project-${new Date().toISOString().replace(/[-:TZ.]/g,"").slice(0,17)}`; await api(`${API}/kanban/projects`,{method:"POST",body:JSON.stringify({project_id:id,name})}); state.projectId=id; await refreshAll(); location.href=`/workbench?project_id=${encodeURIComponent(id)}&new=1&project_name=${encodeURIComponent(name)}`; }}); }
async function createTaskFromPrompt(){ openTextDialog({title:"New Task", label:"Task title", defaultValue:"New workflow task", submitText:"Create Task", onSubmit: async title => { await api(`${API}/kanban/tasks`,{method:"POST",body:JSON.stringify({project_id:state.projectId,title,description:"Created from DevWerk Web UI."})}); await Promise.allSettled([loadBoard(),loadEvents()]); renderShell(); notify("Task created."); }}); }
function notify(message, type="info") {
  document.querySelectorAll(".toast").forEach(node => node.remove());
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "error" : ""}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3200);
}
function openTextDialog({title, label, defaultValue, submitText, onSubmit}) {
  document.querySelectorAll(".modal-backdrop").forEach(node => node.remove());
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = `<form class="modal"><h2>${esc(title)}</h2><label>${esc(label)}<input name="value" value="${escAttr(defaultValue || "")}" /></label><div class="modal-actions"><button type="button" class="button" data-dialog-close="true">Cancel</button><button type="submit" class="button primary">${esc(submitText || "Create")}</button></div></form>`;
  document.body.appendChild(wrap);
  const input = wrap.querySelector("input");
  input.focus();
  input.select();
  wrap.querySelector("[data-dialog-close]").onclick = () => wrap.remove();
  wrap.querySelector("form").onsubmit = async event => {
    event.preventDefault();
    const value = (input.value || "").trim();
    if (!value) return;
    try {
      await onSubmit(value);
      wrap.remove();
    } catch (error) {
      notify(error.message || String(error), "error");
    }
  };
}
function setPrompt(text) {
  const prompt = $("prompt");
  if (!prompt) {
    location.href = `/workbench?project_id=${encodeURIComponent(state.projectId)}`;
    return;
  }
  prompt.value = text;
  prompt.focus();
}
function goSection(section) {
  state.page = "projects";
  state.section = section;
  history.pushState(null, "", `/dashboard?project_id=${encodeURIComponent(state.projectId)}#${encodeURIComponent(section)}`);
  renderShell();
}
function navigatePrimary(section) {
  const paths = {overview:"/workbench", projects:"/dashboard", kanban:"/kanban", tasks:"/tasks"};
  if (["events","memory","analytics","settings"].includes(section)) {
    goSection(section);
    return;
  }
  const path = paths[section] || "/dashboard";
  location.href = `${path}?project_id=${encodeURIComponent(state.projectId)}`;
}
function connectProjectStream() {
  if (!("WebSocket" in window)) {
    state.streamStatus = "unavailable";
    return;
  }
  if (state.stream && state.streamProjectId === state.projectId) return;
  if (state.stream) state.stream.close();
  state.streamProjectId = state.projectId;
  state.streamStatus = "connecting";
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}${API}/kanban/projects/${encodeURIComponent(state.projectId)}/stream`);
  state.stream = socket;
  socket.onopen = () => { state.streamStatus = "connected"; renderLivePanels(); };
  socket.onclose = () => {
    if (state.stream === socket) {
      state.streamStatus = "disconnected";
      renderLivePanels();
      setTimeout(() => { if (state.stream === socket) connectProjectStream(); }, 2500);
    }
  };
  socket.onerror = () => { state.streamStatus = "error"; renderLivePanels(); };
  socket.onmessage = event => {
    try { applyStreamMessage(JSON.parse(event.data)); }
    catch (error) { console.warn("DevWerk stream message ignored", error); }
  };
}
function applyStreamMessage(message) {
  if (!message || message.project_id !== state.projectId) return;
  if (message.board) state.board = message.board;
  const incoming = Array.isArray(message.events) ? message.events : [];
  if (incoming.length) {
    const seen = new Set(state.events.map(event => event.id));
    const merged = [...incoming.filter(event => !seen.has(event.id)), ...state.events];
    state.events = merged.slice(0, 200);
    state.liveLogs = [...incoming, ...state.liveLogs].slice(0, 200);
  }
  renderLivePanels();
}
function renderLivePanels() {
  if (state.busy && document.activeElement && document.activeElement.id === "prompt") return;
  if (state.page === "kanban") renderKanbanPage();
  else if (state.page === "projects" && !state.section) renderProjectsPage();
  else if (state.section === "events") renderEventsSection();
  else {
    document.querySelectorAll(".live-log-card").forEach(node => { node.outerHTML = liveLogCard(); });
  }
}
async function cloneCurrentProject() {
  const source = currentProject();
  const id = `project-${new Date().toISOString().replace(/[-:TZ.]/g,"").slice(0,17)}`;
  await api(`${API}/kanban/projects`, {method:"POST", body:JSON.stringify({project_id:id, name:`${source.name || source.id} copy`, description:source.description || ""})});
  if (state.workflow && (state.workflow.columns || []).length) {
    await api(`${API}/kanban/projects/${encodeURIComponent(id)}/workflow`, {method:"PUT", body:JSON.stringify({workflow:state.workflow})}).catch(() => null);
  }
  state.projectId = id;
  await refreshAll();
  notify("Project cloned.");
}
function exportBoard() {
  const blob = new Blob([JSON.stringify({project_id: state.projectId, board: state.board, events: state.events}, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `devwerk-${state.projectId}-board.json`;
  document.body.appendChild(a);
  a.click();
  URL.revokeObjectURL(a.href);
  a.remove();
  notify("Board export downloaded.");
}
function editorPayload(card) {
  const text = card.querySelector(".code-editor")?.value || "";
  if ((card.dataset.editorMode || "").toUpperCase() === "MARKDOWN") return text;
  try { return JSON.parse(text); }
  catch (error) { throw new Error(`Invalid JSON in ${card.dataset.editorTitle}: ${error.message}`); }
}
function formatEditor(card) {
  const textarea = card.querySelector(".code-editor");
  if (!textarea) return;
  if ((card.dataset.editorMode || "").toUpperCase() === "JSON") textarea.value = JSON.stringify(JSON.parse(textarea.value || "{}"), null, 2);
  if ((card.dataset.editorMode || "").toUpperCase() === "MARKDOWN") textarea.value = textarea.value.replace(/\r\n/g, "\n").trim() + "\n";
  notify(`${card.dataset.editorTitle || "Editor"} formatted.`);
}
async function saveEditor(card) {
  const title = card.dataset.editorTitle || "";
  const payload = editorPayload(card);
  if (title === "Project.MD") await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/project-md`, {method:"PUT", body:JSON.stringify({content: payload})});
  else if (title === "Agents") await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/settings`, {method:"PUT", body:JSON.stringify({agents: payload})});
  else if (title === "Parameters") await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/settings`, {method:"PUT", body:JSON.stringify({parameters: payload})});
  else if (title === "Workflow Definition") await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/workflow`, {method:"PUT", body:JSON.stringify({workflow: payload})});
  else if (title === "Global LLM Catalog") await api(`${API}/settings`, {method:"PUT", body:JSON.stringify({llms: payload})});
  else if (title === "Global Routing Map") await api(`${API}/settings`, {method:"PUT", body:JSON.stringify({routing: payload})});
  else if (title.startsWith("Plugin Settings: ")) await api(`${API}/plugins/${encodeURIComponent(title.replace("Plugin Settings: ", ""))}/settings`, {method:"PUT", body:JSON.stringify({content: payload})});
  else if (title.startsWith("Global Skill: ")) await api(`${API}/skills/${encodeURIComponent(title.replace("Global Skill: ", ""))}`, {method:"PUT", body:JSON.stringify({skill_md: payload})});
  else if (title.startsWith("Project Skill: ")) await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/skills/${encodeURIComponent(title.replace("Project Skill: ", ""))}`, {method:"PUT", body:JSON.stringify({skill_md: payload, enabled:true})});
  else { notify(`${title} is read-only in this view.`); return; }
  await Promise.allSettled([loadSettings(), loadGlobalSettings(), loadGlobalSkills(), loadGlobalPlugins(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings(), loadProjectSkills(), loadWorkflow(), loadBoard(), loadProjectMd(), loadEvents()]);
  renderShell();
  notify(`${title} saved.`);
}
async function togglePluginEnabled(pluginId, enabled) {
  await api(`${API}/plugins/${encodeURIComponent(pluginId)}`, {method:"PATCH", body:JSON.stringify({enabled})});
  await Promise.allSettled([loadGlobalPlugins(), loadGlobalSkills(), loadPluginCommands(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings()]);
  renderShell();
  notify(`${pluginId} ${enabled ? "enabled" : "disabled"}.`);
}
async function importGlobalPlugin() {
  const sourcePath = ($("pluginImportPath")?.value || "").trim();
  if (!sourcePath) { notify("Plugin source path is required.", "error"); return; }
  await api(`${API}/plugins/import`, {method:"POST", body:JSON.stringify({source_path: sourcePath})});
  await Promise.allSettled([loadGlobalPlugins(), loadGlobalSkills(), loadPluginCommands(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings()]);
  renderShell();
  notify("Plugin imported.");
}
async function validateGlobalPlugin() {
  const sourcePath = ($("pluginImportPath")?.value || "").trim();
  if (!sourcePath) { notify("Plugin source path is required.", "error"); return; }
  state.pluginValidation = await api(`${API}/plugins/validate`, {method:"POST", body:JSON.stringify({source_path: sourcePath})});
  renderShell();
  notify("Plugin validation complete.");
}
async function removeGlobalPlugin(pluginId) {
  await api(`${API}/plugins/${encodeURIComponent(pluginId)}`, {method:"DELETE"});
  await Promise.allSettled([loadGlobalPlugins(), loadGlobalSkills(), loadPluginCommands(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings()]);
  renderShell();
  notify(`${pluginId} removed.`);
}
async function loadPluginMarketplace() {
  const marketplacePath = ($("pluginMarketplacePath")?.value || "").trim();
  if (!marketplacePath) { notify("Marketplace path is required.", "error"); return; }
  state.pluginMarketplace = await api(`${API}/plugins/marketplace?marketplace_path=${encodeURIComponent(marketplacePath)}`);
  renderShell();
  notify("Marketplace loaded.");
}
async function importMarketplacePlugin() {
  const marketplacePath = ($("pluginMarketplacePath")?.value || "").trim();
  const pluginName = ($("pluginMarketplaceName")?.value || "").trim();
  if (!marketplacePath || !pluginName) { notify("Marketplace path and plugin name are required.", "error"); return; }
  await api(`${API}/plugins/import-marketplace`, {method:"POST", body:JSON.stringify({marketplace_path: marketplacePath, plugin_name: pluginName})});
  await Promise.allSettled([loadGlobalPlugins(), loadGlobalSkills(), loadPluginCommands(), loadPluginAgents(), loadPluginHooks(), loadPluginMcpServers(), loadPluginSettings()]);
  renderShell();
  notify(`${pluginName} imported.`);
}
async function actOnCurrentTask(action) {
  const task = activeBoardTask();
  if (!task) { notify("No task selected.", "error"); return; }
  if (action === "review") { state.taskTab = "diff"; renderTaskPage(); notify("Review artifacts opened."); return; }
  if (action === "apply") { notify("Apply is performed by a connected capability provider; this dashboard records apply results."); return; }
  if (action === "open-pr") { notify("No pull request integration is configured for this project."); return; }
  if (action === "open-editor") { notify("No editor capability provider is connected to this web session."); return; }
  if (action === "rerun") {
    try {
      await api(`${API}/kanban/tasks/${encodeURIComponent(task.id)}/actions`, {method:"POST", body:JSON.stringify({action:"retry", payload:{source:"web_ui"}})});
      await Promise.allSettled([loadBoard(), loadEvents()]);
      renderTaskPage();
      notify("Retry requested.");
    } catch (error) {
      notify(error.message || String(error), "error");
    }
  }
}
function currentProject(){ return state.projects.find(p => p.id === state.projectId) || {id: state.projectId, name: state.projectId, description: ""}; }
function projectStatus(project){ const raw = String(project.status || project.state || "").toLowerCase(); if(raw === "draft") return {label:"Draft", badge:"orange", dot:"orange"}; if(raw === "planned") return {label:"Planned", badge:"blue", dot:"blue"}; if(raw === "idle") return {label:"Idle", badge:"blue", dot:"blue"}; return projectHealth(project.stats || {}); }
function columns(){ return (state.board && state.board.columns) || []; }
function allTasks(){ return columns().flatMap(column => column.tasks || []); }
function activeStage(){ if(state.activeTask && state.activeTask.status_key) return state.activeTask.status_key; const task = allTasks().find(t => t.status_key); return task ? task.status_key : (columns()[0]?.status_key || ""); }
function statusCount(stage){ return columns().find(column => column.status_key === stage)?.tasks?.length || 0; }
function activeBoardTask(){ const requested = new URLSearchParams(location.search).get("task_id"); const tasks = allTasks(); return tasks.find(t => t.id === requested) || tasks.find(t => t.status_key === activeStage()) || tasks[0]; }
function modelRoutes(){ const agents = state.settings.agents || {}; const routes = new Set(); Object.values(agents).forEach(agent => { if(agent && agent.model_route) routes.add(agent.model_route); if(agent && agent.model) routes.add(agent.model); }); const params = state.settings.parameters || {}; if(params.model) routes.add(params.model); if(params.model_route) routes.add(params.model_route); return Array.from(routes); }
function usageTotals(source=state.usage){ const totals=source.totals || {}; if(Object.keys(totals).length) return {request_count: source.request_count || totals.request_count || 0,calls: totals.calls || 0,input: totals.input_tokens || 0,output: totals.output_tokens || 0,total: totals.total_tokens || 0,duration: totals.duration_ms || 0}; const rows = source.projects || []; return rows.reduce((a,r)=>{ a.calls += r.calls || 0; a.input += r.input_tokens || 0; a.output += r.output_tokens || 0; a.total += r.total_tokens || 0; a.duration += r.duration_ms || 0; return a; }, {request_count: source.request_count || 0,calls:0,input:0,output:0,total:0,duration:0}); }
function projectTotal(key){ return state.projects.reduce((sum, project) => sum + Number((project.stats || {})[key] || 0), 0); }
function routeKeyLabel(key){ const labels={default:"Default route", compression:"Compression route"}; return labels[key] || key; }
function stageTitle(stage){ return columns().find(column => column.status_key === stage)?.title || STAGE_TITLES[stage] || stage || ""; }
function countMemory(mem, key){ const text = JSON.stringify(mem || {}).toLowerCase(); return (text.match(new RegExp(key, "g")) || []).length; }
function latestArtifactSummary(task){ const artifacts = task.artifacts || []; const item = [...artifacts].reverse().find(a => a.payload && typeof a.payload.summary === "string"); return item ? item.payload.summary : ""; }
function yamlish(obj, indent = 0){ if(obj == null) return ""; if(typeof obj !== "object") return String(obj); return Object.entries(obj).map(([k,v]) => `${" ".repeat(indent)}${k}: ${typeof v === "object" ? "\n" + yamlish(v, indent + 2) : String(v)}`).join("\n"); }
function compact(n){ n = Number(n || 0); if(n >= 1000000) return (n / 1000000).toFixed(1) + "M"; if(n >= 1000) return (n / 1000).toFixed(1) + "K"; return String(n); }
function duration(ms){ ms = Number(ms || 0); if(!ms) return "0m"; const h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000); return h ? `${h}h ${m}m` : `${m}m`; }
function dateShort(value){ if(!value) return "-"; const d = new Date(value); return isNaN(d) ? String(value).slice(0,16) : d.toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"}); }
function dateTime(value){ if(!value) return ""; const d = new Date(value); return isNaN(d) ? String(value).slice(0,16) : d.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"}); }
function relative(value){ if(!value) return "-"; const d = new Date(value); if(isNaN(d)) return String(value).slice(0,16); const diff = Math.max(0, Date.now() - d.getTime()); const m = Math.floor(diff / 60000); if(m < 1) return "now"; if(m < 60) return `${m}m ago`; const h = Math.floor(m / 60); if(h < 48) return `${h}h ago`; return `${Math.floor(h / 24)}d ago`; }
function eventTitle(e){ return String(e.event_type || "event").replace(/_/g, " "); }
function stageColor(s){ if(!s) return ""; return "blue"; }
function priorityColor(p){ return Number(p || 0) >= 3 ? "red" : Number(p || 0) === 2 ? "orange" : "blue"; }
function avatarColor(p){ return Number(p || 0) >= 3 ? "" : Number(p || 0) === 2 ? "yellow" : "green"; }
function priorityLabel(p){ return Number(p || 0) >= 3 ? "P1" : Number(p || 0) === 2 ? "P2" : "P3"; }
function shortTaskId(id){ const text = String(id || "task"); return text.startsWith("T-") ? text : `T-${text.slice(0,4)}`; }
function esc(value){ return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch])); }
function escAttr(value){ return esc(value).replace(/`/g, "&#96;"); }
$("projectList").addEventListener("click", async event => { const button = event.target.closest("[data-project]"); if(!button) return; state.projectId = button.dataset.project; history.replaceState(null, "", pageUrlForCurrentView()); await refreshAll(); });
$("projectSearch").addEventListener("input", renderShell);
$("newProjectRail").onclick = createProjectFromPrompt;
$("refresh").onclick = () => refreshAll().catch(err => alert(err.message || String(err)));
window.addEventListener("hashchange", () => { state.section = activeSection(); renderShell(); });
document.addEventListener("click", async event => {
  const collapse = event.target.closest(".nav-collapse");
  if (collapse) {
    document.querySelector(".app-shell")?.classList.toggle("nav-collapsed");
    return;
  }
  const sectionLink = event.target.closest("a[data-nav]");
  if (sectionLink) {
    event.preventDefault();
    navigatePrimary(sectionLink.dataset.nav || "projects");
    return;
  }
  const project = event.target.closest("[data-project-card]");
  if(project) { state.projectId = project.dataset.projectCard; history.replaceState(null, "", pageUrlForCurrentView()); await refreshAll(); }
  const task = event.target.closest("[data-task]");
  if(task) location.href = `/tasks?project_id=${encodeURIComponent(state.projectId)}&task_id=${encodeURIComponent(task.dataset.task)}`;
  const pluginToggle = event.target.closest("[data-plugin-toggle]");
  if (pluginToggle) {
    await togglePluginEnabled(pluginToggle.dataset.pluginToggle, pluginToggle.dataset.enabled === "true");
    return;
  }
  const pluginRemove = event.target.closest("[data-plugin-remove]");
  if (pluginRemove) {
    await removeGlobalPlugin(pluginRemove.dataset.pluginRemove);
    return;
  }
  const pluginValidate = event.target.closest("[data-plugin-validate]");
  if (pluginValidate) {
    await validateGlobalPlugin();
    return;
  }
  const pluginImport = event.target.closest("[data-plugin-import]");
  if (pluginImport) {
    await importGlobalPlugin();
    return;
  }
  const marketplaceLoad = event.target.closest("[data-plugin-marketplace-load]");
  if (marketplaceLoad) {
    await loadPluginMarketplace();
    return;
  }
  const marketplaceImport = event.target.closest("[data-plugin-marketplace-import]");
  if (marketplaceImport) {
    await importMarketplacePlugin();
    return;
  }
  const editorButton = event.target.closest("[data-action]");
  if (editorButton) {
    const card = editorButton.closest(".editor-card");
    try {
      if (editorButton.dataset.action === "editor-format" && card) formatEditor(card);
      if (editorButton.dataset.action === "editor-save" && card) await saveEditor(card);
    } catch (error) {
      notify(error.message || String(error), "error");
    }
    return;
  }
  const target = event.target.closest("button,a.link");
  if (!target || target.classList.contains("tab-button") || target.closest("[data-project-card]") || target.closest("[data-task]")) return;
  const label = (target.innerText || target.title || "").trim().replace(/\s+/g, " ");
  if (!label) return;
  if (label === "N" || target.title === "Notifications" || label === "View all" && target.closest(".card")?.textContent?.includes("Recent Events")) { goSection("events"); return; }
  if (label === "S" || target.title === "Settings") { goSection("settings"); return; }
  if (label === "Add Context") { setPrompt("Add project context: "); return; }
  if (label === "..." && target.closest(".config-panel")) { state.projectTab = "activity"; renderProjectsPage(); return; }
  if (label === "New Task" || label === "+ Add task") { setPrompt("Start a new workflow task: "); return; }
  if (label === "View" || label === "View in Memory") { goSection("memory"); return; }
  if (label === "Show more files" || label === "View diff") { state.taskTab = "diff"; if (state.page === "tasks") renderTaskPage(); else location.href = `/tasks?project_id=${encodeURIComponent(state.projectId)}`; return; }
  if (label === "Export Board") { exportBoard(); return; }
  if (label === "Review") { await actOnCurrentTask("review"); return; }
  if (label === "Apply") { await actOnCurrentTask("apply"); return; }
  if (label === "Re-run") { await actOnCurrentTask("rerun"); return; }
  if (label === "Open PR") { await actOnCurrentTask("open-pr"); return; }
  if (label === "Open in editor") { await actOnCurrentTask("open-editor"); return; }
});
refreshAll().catch(error => { $("page").innerHTML = `<div class="card card-pad"><h1 class="h2">DevWerk UI failed to load</h1><p class="muted">${esc(error.message || String(error))}</p></div>`; });
