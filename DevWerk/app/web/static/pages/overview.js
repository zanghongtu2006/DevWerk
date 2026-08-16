import { escapeHtml, relativeTime } from "../core/format.js?v=20260804-debug1";
import { emptyState, eventRow, metricCard, noProjectState, taskMiniCard, workflowPipeline } from "../ui/components.js?v=20260804-debug1";

export function renderOverview(state) {
  if (!state.project) return noProjectState();
  const tasks = state.board?.tasks || [];
  const running = tasks.filter((task) => ["running", "waiting"].includes(task.status)).length;
  const done = tasks.filter((task) => task.status === "done").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const recent = tasks.slice(0, 4);
  return `<div class="page-stack overview-page">
    <section class="project-hero card"><div class="hero-copy"><span class="eyebrow">PROJECT OVERVIEW</span><h1>${escapeHtml(state.project.name)}</h1><p>${escapeHtml(state.project.description || "Conversation Agent 正在维护此 Project 的目标、Workflow 与交付事实。")}</p><div class="hero-meta"><code>${escapeHtml(state.project.base_dir)}</code><span>更新于 ${relativeTime(state.project.updated_at)}</span></div></div><div class="hero-actions"><button class="button primary" data-open-route="projects">打开项目对话</button><button class="button" data-open-route="kanban">查看 Kanban</button></div></section>
    <section class="metric-grid">${metricCard("Formal Tasks", String(tasks.length), "当前 Workflow 的全部任务", "blue")}${metricCard("In progress", String(running), "running / waiting", "violet")}${metricCard("Completed", String(done), "明确进入 done 终态", "green")}${metricCard("Failed", String(failed), failed ? "需要 Conversation Agent 介入" : "当前没有失败任务", failed ? "red" : "slate")}</section>
    <section class="card pipeline-card"><div class="section-head"><div><span class="eyebrow">WORKFLOW</span><h2>Delivery pipeline</h2></div><span class="read-only-label">Read-only</span></div>${workflowPipeline(state.board?.workflow, tasks)}</section>
    <div class="overview-columns"><section class="card panel-card"><div class="section-head"><div><span class="eyebrow">TASKS</span><h2>Recent delivery</h2></div><button class="text-button" data-open-route="tasks">全部任务</button></div><div class="mini-list">${recent.length ? recent.map((task) => taskMiniCard(task)).join("") : emptyState("暂无任务", "在 Project 对话中描述正式工作，Conversation Agent 会决定是否创建 Task。")}</div></section><section class="card panel-card"><div class="section-head"><div><span class="eyebrow">EVENT STREAM</span><h2>Recent activity</h2></div><button class="text-button" data-open-route="events">完整事件</button></div><div class="event-list compact">${state.events.length ? state.events.slice(-6).reverse().map(eventRow).join("") : emptyState("暂无事件", "Conversation、Task 与 Runtime 状态变化会出现在这里。")}</div></section></div>
  </div>`;
}
