import { escapeHtml, formatDate, relativeTime, shortId, statusLabel, statusTone, truncate } from "../core/format.js";

export function icon(name) {
  const paths = {
    overview: '<path d="M4 13h6V4H4v9Zm0 7h6v-4H4v4Zm10 0h6v-9h-6v9Zm0-16v4h6V4h-6Z"/>',
    projects: '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2H19a2 2 0 0 1 2 2v9.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-11Z"/>',
    kanban: '<path d="M4 5h4v14H4V5Zm6 0h4v9h-4V5Zm6 0h4v11h-4V5Z"/>',
    tasks: '<path d="m5 7 2 2 4-4M13 7h6M5 13l2 2 4-4M13 13h6M5 19l2 2 4-4M13 19h6"/>',
    events: '<path d="M12 8v5l3 2M4.9 4.9a10 10 0 1 1-1.8 11.4M3 4v5h5"/>',
    arrow: '<path d="m9 18 6-6-6-6"/>',
  };
  return `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.overview}</svg>`;
}
export function badge(status) {
  return `<span class="status-badge ${statusTone(status)}"><i></i>${escapeHtml(statusLabel(status))}</span>`;
}

export function emptyState(title, detail, action = "") {
  return `<div class="empty-state"><span class="empty-orbit"><i></i></span><h2>${escapeHtml(title)}</h2><p>${escapeHtml(detail)}</p>${action}</div>`;
}

export function pageSkeleton(route, error = "") {
  if (route === "error") return emptyState("无法加载 DevWerk", error || "请确认 startup.bat 已启动服务。", '<button class="button primary" onclick="location.reload()">重新连接</button>');
  if (route === "task-detail") return `<div class="detail-skeleton">${skeletonLines(8)}</div>`;
  const cards = route === "kanban" ? 4 : 3;
  return `<div class="page-loading" aria-live="polite"><div class="loading-heading"><span class="skeleton block wide"></span><span class="skeleton block medium"></span></div><div class="loading-grid">${Array.from({ length: cards }, () => `<div class="skeleton-card">${skeletonLines(5)}</div>`).join("")}</div><span class="sr-only">正在加载</span></div>`;
}

function skeletonLines(count) {
  return Array.from({ length: count }, (_, index) => `<span class="skeleton line ${index % 3 === 2 ? "short" : ""}"></span>`).join("");
}

export function workflowPipeline(workflow, tasks = []) {
  const columns = workflow?.definition?.columns || [];
  if (!columns.length) return '<div class="pipeline-empty">Conversation Agent 尚未发布正式 Workflow。</div>';
  return `<div class="pipeline-scroll"><div class="pipeline" style="--columns:${columns.length}">${columns.map((column, index) => {
    const count = tasks.filter((task) => task.current_column === column.key || (column.terminal && task.status === column.terminal)).length;
    const mark = column.terminal === "done" ? "✓" : column.terminal === "failed" ? "!" : index + 1;
    return `<div class="pipeline-stage ${column.terminal ? `terminal ${column.terminal}` : ""}"><span class="stage-index">${mark}</span><span><b>${escapeHtml(column.name)}</b><small>${escapeHtml(column.key)} · ${count} task${count === 1 ? "" : "s"}</small></span>${index < columns.length - 1 ? '<i class="stage-link"></i>' : ""}</div>`;
  }).join("")}</div></div>`;
}

export function taskMiniCard(task, options = {}) {
  return `<button class="task-mini ${options.active ? "active" : ""}" data-task-id="${escapeHtml(task.id)}"><span class="task-mini-top">${badge(task.status)}<small>${relativeTime(task.updated_at)}</small></span><b>${escapeHtml(task.title)}</b><span class="task-mini-meta"><code>${escapeHtml(shortId(task.id))}</code><span>${escapeHtml(task.current_column)}</span></span></button>`;
}

export function eventRow(event) {
  return `<article class="event-row"><span class="event-mark ${event.type?.includes("failed") ? "danger" : event.type?.includes("done") ? "success" : ""}"></span><div><div class="event-title"><b>${escapeHtml(event.type)}</b><time>${formatDate(event.created_at)}</time></div><p>${escapeHtml(eventSummary(event))}</p><code>${escapeHtml(shortId(event.task_id || event.run_id || event.project_id))}</code></div></article>`;
}

function eventSummary(event) {
  const data = event.data || {};
  return truncate(data.title || data.error || data.summary || data.column || data.path || "状态已由 Runtime 持久化", 150);
}

export function metricCard(label, value, detail, tone = "blue") {
  return `<article class="metric-card"><span class="metric-icon ${tone}"></span><div><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong><p>${escapeHtml(detail)}</p></div></article>`;
}

export function noProjectState() {
  return emptyState("还没有 Project", "Project 是会话、Workflow、Task 与文件产物的隔离边界。", '<button class="button primary" data-create-project>创建第一个 Project</button>');
}
