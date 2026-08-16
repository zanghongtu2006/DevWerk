import { escapeHtml, relativeTime, shortId } from "../core/format.js?v=20260804-debug1";
import { badge, emptyState, noProjectState } from "../ui/components.js?v=20260804-debug1";

export function renderKanban(state) {
  if (!state.project) return noProjectState();
  const workflow = state.board?.workflow;
  const columns = workflow?.definition?.columns || [];
  const tasks = state.board?.tasks || [];
  if (!columns.length) {
    return `<div class="page-stack"><header class="page-heading"><div><span class="eyebrow">KANBAN</span><h1>Workflow board</h1><p>The board is a read-only runtime projection.</p></div><span class="read-only-label">Read-only</span></header><section class="card">${emptyState("No Workflow yet", "Describe the delivery goal to the Conversation Agent; it will design and publish the Workflow.", '<button class="button primary" data-open-route="projects">Open project conversation</button>')}</section></div>`;
  }
  const nodes = [...columns, { key: "done", name: "Done", terminal: "done" }, { key: "failed", name: "Failed", terminal: "failed" }];
  return `<div class="kanban-page"><header class="page-heading"><div><span class="eyebrow">KANBAN · REVISION ${workflow.revision}</span><h1>${escapeHtml(workflow.definition.name)}</h1><p>${escapeHtml(workflow.definition.description || "Declarative workflow driven by uniform Columns.")}</p></div><div class="heading-actions"><span class="read-only-label">Read-only board</span><span class="task-total">${tasks.length} tasks</span></div></header><div class="kanban-scroll"><div class="kanban-board" style="--columns:${nodes.length}">${nodes.map((column) => columnView(column, tasks)).join("")}</div></div></div>`;
}

function columnView(column, tasks) {
  const items = tasks.filter((task) => column.terminal ? task.status === column.terminal : task.current_column === column.key);
  const contractView = column.terminal ? "" : columnDefinitionView(column);
  return `<section class="kanban-column ${column.terminal ? `terminal ${column.terminal}` : ""}"><header><span class="column-dot"></span><div><h2>${escapeHtml(column.name)}</h2><small>${escapeHtml(column.key)}</small></div><b>${items.length}</b></header><div class="kanban-cards">${items.length ? items.map(taskCard).join("") : '<div class="column-empty">No Task in this Column</div>'}</div>${contractView}</section>`;
}

function columnDefinitionView(column) {
  const context = column.context || {};
  const inputRoots = Object.keys(column.input_contract?.properties || {});
  const upstream = context.upstream_outputs || [];
  const artifacts = context.artifact_globs || [];
  const taskInput = context.include_task
    ? "随 Task 上下文提供（含 task.input）"
    : "不提供";
  const execution = executorView(column);
  const transitions = (column.transitions || []).length
    ? column.transitions.map((item) => `<li><code>${escapeHtml(item.outcome)}</code><span>→</span><b>${escapeHtml(item.target)}</b></li>`).join("")
    : "<li>未声明</li>";

  return `<div class="column-definition">
    <section><h3>Column 目的</h3><p>${escapeHtml(column.instruction || "未声明")}</p></section>
    <section><h3>输入与上游</h3><dl>
      <div><dt>Task 输入</dt><dd>${escapeHtml(taskInput)}</dd></div>
      <div><dt>输入约束</dt><dd>${escapeHtml(inputRoots.length ? inputRoots.join("、") : "未声明")}</dd></div>
      <div><dt>上游结果</dt><dd>${escapeHtml(upstream.length ? upstream.join("、") : "未声明")}</dd></div>
      <div><dt>项目上下文</dt><dd>${context.include_project ? "提供" : "不提供"}</dd></div>
      <div><dt>项目文件</dt><dd>${escapeHtml(artifacts.length ? artifacts.join("、") : "未声明")}</dd></div>
    </dl></section>
    ${execution}
    <section><h3>结果流转</h3><ul class="column-transitions">${transitions}</ul></section>
    <details class="column-contract"><summary>查看原始 Runtime JSON</summary><pre>${escapeHtml(JSON.stringify(column, null, 2))}</pre></details>
  </div>`;
}

function executorView(column) {
  const executor = column.executor || {};
  if (executor.kind === "agent") {
    const capabilities = executor.capabilities || [];
    return `<section><h3>执行方式</h3><dl>
      <div><dt>派生临时 Agent</dt><dd>是</dd></div>
      <div><dt>Agent 要做什么</dt><dd>${escapeHtml(column.instruction || "未声明")}</dd></div>
      <div><dt>可用能力</dt><dd>${escapeHtml(capabilities.length ? capabilities.join("、") : "未声明")}</dd></div>
      <div><dt>执行限制</dt><dd>无平台预设轮次、工具调用或时长上限</dd></div>
    </dl></section>`;
  }

  if (executor.kind === "capability_sequence") {
    const steps = executor.steps || [];
    const stepItems = steps.length
      ? steps.map((step, index) => `<li><b>${index + 1}. ${escapeHtml(step.capability)}</b><span>${escapeHtml(stepArgumentSummary(step.arguments || {}))}</span></li>`).join("")
      : "<li>未声明执行步骤</li>";
    return `<section><h3>执行方式</h3><dl><div><dt>派生临时 Agent</dt><dd>否（确定性步骤）</dd></div></dl><ol class="column-steps">${stepItems}</ol></section>`;
  }

  return `<section><h3>执行方式</h3><p>未声明 Executor</p></section>`;
}

function stepArgumentSummary(argumentsValue) {
  if (Array.isArray(argumentsValue.argv)) return `命令：${compactText(argumentsValue.argv.join(" "))}`;
  if (argumentsValue.path) return `文件：${argumentsValue.path}`;
  const keys = Object.keys(argumentsValue);
  return keys.length ? `参数：${keys.join("、")}` : "无参数";
}

function compactText(value) {
  return value.length > 180 ? `${value.slice(0, 180)}…（完整内容见 Runtime JSON）` : value;
}

function taskCard(task) {
  return `<article class="kanban-task"><div class="task-card-head">${badge(task.status)}<time>${relativeTime(task.updated_at)}</time></div><h3>${escapeHtml(task.title)}</h3><p>${escapeHtml(task.brief || "No task brief")}</p><div class="task-card-foot"><code>${escapeHtml(shortId(task.id))}</code><button class="text-button" data-open-route="tasks" data-task-id="${escapeHtml(task.id)}">Details</button></div></article>`;
}
