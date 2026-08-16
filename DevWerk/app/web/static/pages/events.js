import { eventRow, noProjectState } from "../ui/components.js?v=20260804-debug1";

export function renderEvents(state) {
  if (!state.project) return noProjectState();
  return `<div class="events-page"><header class="page-heading"><div><span class="eyebrow">EVENT STREAM</span><h1>Project timeline</h1><p>Conversation、Workflow、Task、Column Run、Agent Run 与 Artifact 的持久事实。</p></div><span class="event-count">${state.events.length} events</span></header><section class="card events-card"><div class="event-list">${state.events.length ? state.events.slice().reverse().map(eventRow).join("") : '<div class="empty-state"><h2>暂无事件</h2><p>项目状态发生变化后，事件会显示在这里。</p></div>'}</div></section></div>`;
}
