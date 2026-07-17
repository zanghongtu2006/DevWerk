import { escapeHtml, formatDate, shortId } from "../core/format.js";
import { badge, emptyState, eventRow, noProjectState, taskMiniCard } from "../ui/components.js";

export function renderTasks(state) {
  if (!state.project) return noProjectState();
  const tasks = state.board?.tasks || [];
  if (!tasks.length) return `<div class="page-stack"><header class="page-heading"><div><span class="eyebrow">TASKS</span><h1>Formal task evidence</h1><p>Tasks are created by the Conversation Agent and run on the declarative Workflow.</p></div><span class="read-only-label">Read-only</span></header><section class="card">${emptyState("No formal Tasks", "Use the project conversation to describe work that benefits from tracked multi-step delivery.")}</section></div>`;
  const selected = state.taskDetail || tasks.find((task) => task.id === state.selectedTaskId) || tasks[0];
  return `<div class="tasks-page"><aside class="card task-list"><header><span class="eyebrow">FORMAL TASKS</span><h1>Task browser</h1><p>${tasks.length} tasks in this Project</p></header><div class="task-list-scroll">${tasks.map((task) => taskMiniCard(task, { active: task.id === selected.id })).join("")}</div></aside><section id="task-detail" class="card task-detail">${taskDetail(selected, state.taskDetail, state.agentDetail)}</section></div>`;
}

function taskDetail(task, full, agentDetail) {
  const detail = full?.id === task.id ? full : null;
  const runs = detail?.runs || [];
  const artifacts = detail?.artifacts || [];
  const agents = detail?.agent_runs || [];
  const events = detail?.events || [];
  const audit = agentDetail ? `<section class="task-events"><div class="section-head"><div><span class="eyebrow">SELECTED AGENT RUN</span><h2>${escapeHtml(shortId(agentDetail.id))}</h2></div>${badge(agentDetail.status)}</div><details open><summary>Capabilities and context</summary><pre>${escapeHtml(JSON.stringify({ capabilities: agentDetail.capabilities, context: agentDetail.context }, null, 2))}</pre></details><details><summary>Messages (${agentDetail.messages?.length || 0})</summary><pre>${escapeHtml(JSON.stringify(agentDetail.messages || [], null, 2))}</pre></details><details><summary>Tool invocations (${agentDetail.tool_invocations?.length || 0})</summary><pre>${escapeHtml(JSON.stringify(agentDetail.tool_invocations || [], null, 2))}</pre></details></section>` : "";
  const evidence = detail ? `<div class="task-evidence-grid"><section><div class="section-head"><div><span class="eyebrow">COLUMN RUNS</span><h2>Execution history</h2></div><b>${runs.length}</b></div><div class="run-list">${runs.length ? runs.map(runRow).join("") : '<p class="muted-copy">No Column Run yet.</p>'}</div></section><section><div class="section-head"><div><span class="eyebrow">ARTIFACTS</span><h2>Delivered files</h2></div><b>${artifacts.length}</b></div><div class="artifact-list">${artifacts.length ? artifacts.map(artifactRow).join("") : '<p class="muted-copy">No Artifact registered for this Task.</p>'}</div></section></div><section class="task-events"><div class="section-head"><div><span class="eyebrow">AGENT RUNS</span><h2>Tool-loop audit</h2></div><b>${agents.length}</b></div><div class="run-list">${agents.length ? agents.map(agentRow).join("") : '<p class="muted-copy">No ephemeral Agent was started.</p>'}</div></section>${audit}<section class="task-events"><div class="section-head"><div><span class="eyebrow">EVENTS</span><h2>Task timeline</h2></div><b>${events.length}</b></div><div class="event-list">${events.slice().reverse().map(eventRow).join("")}</div></section>` : `<div class="detail-load-prompt"><p>Load Runs, Agent Runs, Artifacts, and Events to inspect complete execution evidence.</p><button class="button primary" data-task-id="${escapeHtml(task.id)}">Load execution detail</button></div>`;
  return `<header class="task-detail-head"><div><span class="eyebrow">${escapeHtml(shortId(task.id))}</span><h1>${escapeHtml(task.title)}</h1><p>${escapeHtml(task.brief || "No task brief")}</p></div>${badge(task.status)}</header><div class="task-facts"><div><small>Current column</small><b>${escapeHtml(task.current_column)}</b></div><div><small>Created</small><b>${formatDate(task.created_at)}</b></div><div><small>Updated</small><b>${formatDate(task.updated_at)}</b></div><div><small>Attempts</small><b>${task.attempt || 0}</b></div></div>${evidence}`;
}

function runRow(run) {
  return `<article class="run-row"><span class="run-sequence">${run.sequence}</span><div><b>${escapeHtml(run.column_key)}</b><small>attempt ${run.attempt} · ${formatDate(run.started_at)}</small></div>${badge(run.status)}</article>`;
}

function agentRow(run) {
  return `<button class="run-row" data-agent-run-id="${escapeHtml(run.id)}"><span class="run-sequence">AI</span><div><b>${escapeHtml(shortId(run.id))}</b><small>${run.tool_calls || 0} tools · ${run.iterations || 0} iterations</small></div>${badge(run.status)}</button>`;
}

function artifactRow(artifact) {
  return `<article class="artifact-row"><span class="file-mark"></span><div><b title="${escapeHtml(artifact.path)}">${escapeHtml(artifact.path)}</b><small>${escapeHtml(artifact.kind)} · ${artifact.size || 0} bytes</small></div></article>`;
}
