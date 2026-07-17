import { escapeHtml, formatDate } from "../core/format.js";
import { eventRow, noProjectState } from "../ui/components.js";

export function renderProjects(state) {
  if (!state.project) return noProjectState();
  const tasks = state.board?.tasks || [];
  const workflow = state.board?.workflow;
  const messages = state.conversation || [];
  return `<div class="project-workspace">
    <section class="conversation-card card">
      <header class="conversation-head"><div><span class="eyebrow">CONVERSATION AGENT</span><h1>${escapeHtml(state.project.name)}</h1><p>通用 Agent · 项目经理 · 敏捷教练 · 系统恢复入口</p></div><span class="agent-state"><i></i>${state.sending ? "Thinking" : "Supervising"}</span></header>
      <div id="conversation-messages" class="conversation-messages">${messages.length ? messages.map(messageBubble).join("") : welcomeMessage(state.project)}${state.pendingMessage ? pendingBubble(state.pendingMessage) : ""}</div>
      <form id="conversation-form" class="conversation-composer"><label for="message-input" class="sr-only">向 Conversation Agent 发送消息</label><textarea id="message-input" rows="3" ${state.sending ? "disabled" : ""} placeholder="描述目标、约束、优先级，或需要诊断与恢复的问题…">${escapeHtml(state.draftMessage || "")}</textarea><div class="composer-footer"><span>Workflow 与 Column 由 Conversation Agent 在对话中生成并监督</span><button class="button primary send-button" type="submit" ${state.sending ? "disabled" : ""}>${state.sending ? '<span class="button-spinner"></span>处理中' : "发送"}</button></div></form>
    </section>
    <aside class="project-side">
      <section class="card info-card"><span class="eyebrow">PROJECT BOUNDARY</span><h2>Context</h2><dl><div><dt>Project ID</dt><dd><code>${escapeHtml(state.project.id)}</code></dd></div><div><dt>Base directory</dt><dd title="${escapeHtml(state.project.base_dir)}">${escapeHtml(state.project.base_dir)}</dd></div><div><dt>Created</dt><dd>${formatDate(state.project.created_at)}</dd></div></dl></section>
      <section class="card info-card"><div class="section-head"><div><span class="eyebrow">WORKFLOW</span><h2>${escapeHtml(workflow?.definition?.name || "Not published")}</h2></div><span class="read-only-label">Read-only</span></div><p>${escapeHtml(workflow?.definition?.description || "在对话中说明正式交付目标，Conversation Agent 会发布声明式 Workflow。")}</p><div class="workflow-summary"><span><b>${workflow?.definition?.columns?.length || 0}</b> columns</span><span><b>${tasks.length}</b> tasks</span><span><b>${tasks.filter((task) => task.status === "done").length}</b> done</span></div></section>
      <section class="card info-card live-card"><span class="eyebrow">LIVE ACTIVITY</span><h2>Runtime events</h2><div class="event-list compact">${state.events.length ? state.events.slice(-4).reverse().map(eventRow).join("") : '<p class="muted-copy">暂无 Runtime 事件。</p>'}</div></section>
    </aside>
  </div>`;
}
function messageBubble(message) {
  const user = message.role === "user";
  return `<article class="chat-message ${user ? "user" : "assistant"}">${user ? "" : '<span class="agent-avatar">DW</span>'}<div><span class="message-role">${user ? "You" : "Conversation Agent"} · ${formatDate(message.created_at)}</span><div class="message-content">${escapeHtml(message.content)}</div></div></article>`;
}

function welcomeMessage(project) {
  return `<article class="chat-message assistant"><span class="agent-avatar">DW</span><div><span class="message-role">Conversation Agent</span><div class="message-content">Project “${escapeHtml(project.name)}” 已就绪。告诉我目标、约束和验收标准，我会判断应直接处理还是建立正式 Task。</div></div></article>`;
}

function pendingBubble(message) {
  return `<article class="chat-message user pending"><div><span class="message-role">You · sending</span><div class="message-content">${escapeHtml(message)}</div></div></article><article class="chat-message assistant thinking"><span class="agent-avatar">DW</span><div><span class="message-role">Conversation Agent</span><div class="message-content"><span class="typing"><i></i><i></i><i></i></span>正在理解需求、检查项目状态并安排工作…</div></div></article>`;
}
