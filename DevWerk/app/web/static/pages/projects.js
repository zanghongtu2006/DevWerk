import { escapeHtml, formatDate } from "../core/format.js?v=20260804-debug1";
import { eventRow, noProjectState } from "../ui/components.js?v=20260804-debug1";

export function renderProjects(state) {
  if (!state.project) return noProjectState();
  const tasks = state.board?.tasks || [];
  const workflow = state.board?.workflow;
  const messages = visibleConversationMessages(state.conversation || []);
  return `<div class="project-workspace">
    <section class="conversation-card card">
      <header class="conversation-head"><div><span class="eyebrow">CONVERSATION AGENT</span><h1>${escapeHtml(state.project.name)}</h1><p>通用 Agent · 项目经理 · 敏捷教练 · 系统恢复入口</p></div><span class="agent-state"><i></i>${state.sending ? "Thinking" : "Supervising"}</span></header>
      ${conversationStatusStrip(state.conversationStatus, state.sending)}
      <div id="conversation-messages" class="conversation-messages">${state.conversationHasOlder ? '<button class="load-older-messages" type="button" data-load-older-messages>加载更早消息</button>' : ""}${messages.length ? messages.map(messageBubble).join("") : welcomeMessage(state.project)}${state.pendingMessage ? pendingUserBubble(state.pendingMessage) : ""}</div>
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
  const notification = message.meta?.kind === "notification";
  const label = user ? "You" : notification ? "Conversation Agent · 主动通知" : "Conversation Agent";
  return `<article class="chat-message ${user ? "user" : "assistant"}" data-message-id="${escapeHtml(message.id)}" title="Message ID: ${escapeHtml(message.id)}">${user ? "" : '<span class="agent-avatar">DW</span>'}<div><span class="message-role">${label} · ${formatDate(message.created_at)}</span><div class="message-content">${escapeHtml(message.content)}</div></div></article>`;
}

function welcomeMessage(project) {
  return `<article class="chat-message assistant"><span class="agent-avatar">DW</span><div><span class="message-role">Conversation Agent</span><div class="message-content">Project “${escapeHtml(project.name)}” 已就绪。告诉我目标、约束和验收标准，我会判断应直接处理还是建立正式 Task。</div></div></article>`;
}

function pendingUserBubble(message) {
  return `<article class="chat-message user pending"><div><span class="message-role">You · sending</span><div class="message-content">${escapeHtml(message)}</div></div></article>`;
}

function visibleConversationMessages(messages) {
  return messages.filter((message) => (
    ["user", "assistant"].includes(message.role)
    && Boolean(message.content)
  ));
}

function conversationStatusStrip(status, submitting) {
  const job = status?.job;
  if (!job && !submitting) return "";
  const effectiveStatus = job?.status || "queued";
  if (effectiveStatus === "succeeded") return "";
  const failed = effectiveStatus === "failed" || status?.agent_state === "attention";
  const queued = effectiveStatus === "queued";
  const label = failed ? "本轮未完成" : queued ? "等待处理" : "Conversation Agent 正在工作";
  const detail = failed
    ? "运行记录已保留，可在 Events 中查看原因。"
    : queued
      ? "消息已进入 Project 的处理队列。"
      : "正在理解需求、检查项目状态并安排工作。";
  return `<section class="conversation-status-strip ${failed ? "attention" : "active"}" aria-live="polite"><span class="status-pulse"></span><div><b>${label}</b><small>${detail}${job?.updated_at ? ` · ${formatDate(job.updated_at)}` : ""}</small></div>${failed ? '<button type="button" data-open-route="events">查看执行过程</button>' : ""}</section>`;
}
