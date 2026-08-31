import { escapeHtml, formatDate, shortId } from "../core/format.js?v=20260804-debug1";
import { badge, emptyState, eventRow, noProjectState, taskMiniCard } from "../ui/components.js?v=20260804-debug1";

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
  const attempts = detail?.attempts || [];
  const artifacts = detail?.artifacts || [];
  const agents = detail?.agent_runs || [];
  const workcells = detail?.workcells || [];
  const events = detail?.events || [];
  const failure = task.status === "failed" ? taskFailureSummary(task, runs, attempts) : "";
  const audit = agentDetail ? `<section class="task-events"><div class="section-head"><div><span class="eyebrow">SELECTED AGENT RUN</span><h2>${escapeHtml(shortId(agentDetail.id))}</h2></div>${badge(agentDetail.status)}</div><details open><summary>Capabilities and context</summary><pre>${escapeHtml(JSON.stringify({ capabilities: agentDetail.capabilities, context: agentDetail.context }, null, 2))}</pre></details><details><summary>Messages (${agentDetail.messages?.length || 0})</summary><pre>${escapeHtml(JSON.stringify(agentDetail.messages || [], null, 2))}</pre></details><details><summary>Tool invocations (${agentDetail.tool_invocations?.length || 0})</summary><pre>${escapeHtml(JSON.stringify(agentDetail.tool_invocations || [], null, 2))}</pre></details></section>` : "";
  const workcellEvidence = workcells.length ? `<section class="task-events"><div class="section-head"><div><span class="eyebrow">WORKCELLS</span><h2>Participant collaboration</h2></div><b>${workcells.length}</b></div><div class="workcell-list">${workcells.map(workcellCard).join("")}</div></section>` : "";
  const evidence = detail ? `<div class="task-evidence-grid"><section><div class="section-head"><div><span class="eyebrow">COLUMN RUNS</span><h2>Execution history</h2></div><b>${runs.length}</b></div><div class="run-list">${runs.length ? runs.map(runRow).join("") : '<p class="muted-copy">No Column Run yet.</p>'}</div><details><summary>Attempts (${attempts.length})</summary><div class="run-list">${attempts.map(attemptRow).join("")}</div></details></section><section><div class="section-head"><div><span class="eyebrow">ARTIFACTS</span><h2>Delivered files</h2></div><b>${artifacts.length}</b></div><div class="artifact-list">${artifacts.length ? artifacts.map(artifactRow).join("") : '<p class="muted-copy">No Artifact registered for this Task.</p>'}</div></section></div>${workcellEvidence}<section class="task-events"><div class="section-head"><div><span class="eyebrow">AGENT RUNS</span><h2>Tool-loop audit</h2></div><b>${agents.length}</b></div><div class="run-list">${agents.length ? agents.map(agentRow).join("") : '<p class="muted-copy">No Agent participant was started.</p>'}</div></section>${audit}<section class="task-events"><div class="section-head"><div><span class="eyebrow">EVENTS</span><h2>Task timeline</h2></div><b>${events.length}</b></div><div class="event-list">${events.slice().reverse().map(eventRow).join("")}</div></section>` : `<div class="detail-load-prompt"><p>Load Runs, Attempts, Agent Runs, Artifacts, and Events to inspect complete execution evidence.</p><button class="button primary" data-task-id="${escapeHtml(task.id)}">Load execution detail</button></div>`;
  return `<header class="task-detail-head"><div><span class="eyebrow">${escapeHtml(shortId(task.id))}</span><h1>${escapeHtml(task.title)}</h1><p>${escapeHtml(task.brief || "No task brief")}</p></div>${badge(task.status)}</header><div class="task-facts"><div><small>Current column</small><b>${escapeHtml(task.current_column)}</b></div><div><small>Created</small><b>${formatDate(task.created_at)}</b></div><div><small>Updated</small><b>${formatDate(task.updated_at)}</b></div><div><small>Attempts</small><b>${attempts.length || task.attempt || 0}</b></div></div>${failure}${evidence}`;
}

function taskFailureSummary(task, runs, attempts) {
  const failedRun = [...runs].reverse().find((run) => run.status === "failed");
  const failedAttempt = [...attempts].reverse().find((attempt) => attempt.status === "failed");
  const phase = failedRun?.column_key || failedAttempt?.column_key || task.current_column || "unknown";
  const raw = String(task.error || failedRun?.error || failedAttempt?.error || "Runtime 未记录具体错误").trim();
  const summary = humanizeFailure(raw, phase);
  return `<section class="task-failure-summary" role="alert"><div class="task-failure-heading"><div><span class="eyebrow">FAILURE REASON</span><h2>任务未完成</h2></div><span class="task-failure-phase">失败阶段：${escapeHtml(phase)}</span></div><p>${escapeHtml(summary)}</p><details><summary>查看原始 Runtime 错误</summary><code>${escapeHtml(raw)}</code></details></section>`;
}

function humanizeFailure(raw, phase) {
  const text = raw.replace(/^RuntimeExecutionError:\s*/i, "").trim();
  const capability = text.match(/^([a-z][\w.-]+):\s*/i)?.[1];
  const unexpected = text.match(/Additional properties are not allowed \('([^']+)' was unexpected\)/i)?.[1];
  if (capability && unexpected) return `${phase} 调用了 ${capability}，但传入了不支持的参数“${unexpected}”，因此该阶段无法执行。`;
  const required = text.match(/'([^']+)' is a required property/i)?.[1];
  if (capability && required) return `${phase} 调用 ${capability} 时缺少必填参数“${required}”，因此该阶段无法执行。`;
  if (/ended without calling column\.complete/i.test(text)) return `${phase} 的 Agent 已结束，但没有提交 Column 完成结果（column.complete），Runtime 因此将该阶段判定为失败。`;
  if (/capability sequence completed with outcome mismatch/i.test(text)) return `${phase} 的确定性校验已经执行，但实际结果不符合该阶段的成功条件。请展开原始错误并查看下方 Column Run 输出确认未通过的检查项。`;
  if (/input rejected value/i.test(text)) return `${phase} 的输入不符合运行时契约，因此该阶段在执行前被拒绝。`;
  return `${phase} 执行失败：${text || "Runtime 未记录具体错误"}`;
}

function attemptRow(attempt) {
  return `<article class="run-row"><span class="run-sequence">${attempt.attempt_no}</span><div><b>${escapeHtml(shortId(attempt.id))}</b><small>${formatDate(attempt.started_at)}</small></div>${badge(attempt.status)}</article>`;
}

function runRow(run) {
  return `<article class="run-row"><span class="run-sequence">${run.sequence}</span><div><b>${escapeHtml(run.column_key)}</b><small>attempt ${run.attempt} · ${formatDate(run.started_at)}</small></div>${badge(run.status)}</article>`;
}

function agentRow(run) {
  return `<button class="run-row" data-agent-run-id="${escapeHtml(run.id)}"><span class="run-sequence">AI</span><div><b>${escapeHtml(shortId(run.id))}</b><small>${run.tool_calls || 0} tools · ${run.iterations || 0} iterations</small></div>${badge(run.status)}</button>`;
}

function workcellCard(workcell) {
  const participants = workcell.participants || [];
  const handoffs = workcell.handoffs || [];
  return `<article class="workcell-card"><header><div><b>${escapeHtml(shortId(workcell.id))}</b><small>Current state: ${escapeHtml(workcell.current_state)}</small></div>${badge(workcell.status)}</header><div class="workcell-participants">${participants.map((item) => `<span><b>${escapeHtml(item.participant_key)}</b><small>${escapeHtml(item.kind)} · ${escapeHtml(item.status)}</small></span>`).join("")}</div><ol class="workcell-handoffs">${handoffs.map((item) => `<li><b>${item.sequence}. ${escapeHtml(item.sender_key)}</b> emitted <code>${escapeHtml(item.signal)}</code>${item.receivers?.length ? ` → ${item.receivers.map(escapeHtml).join(", ")}` : ""}</li>`).join("") || "<li>No handoff yet.</li>"}</ol></article>`;
}

function artifactRow(artifact) {
  return `<article class="artifact-row"><span class="file-mark"></span><div><b title="${escapeHtml(artifact.path)}">${escapeHtml(artifact.path)}</b><small>${escapeHtml(artifact.kind)} · ${artifact.size || 0} bytes</small></div></article>`;
}
