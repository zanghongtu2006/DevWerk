import { escapeHtml, relativeTime, shortId } from "../core/format.js";
import { badge, emptyState, noProjectState } from "../ui/components.js";

export function renderKanban(state) {
  if (!state.project) return noProjectState();
  const workflow = state.board?.workflow;
  const columns = workflow?.definition?.columns || [];
  const tasks = state.board?.tasks || [];
  if (!columns.length) {
    return `<div class="page-stack"><header class="page-heading"><div><span class="eyebrow">KANBAN</span><h1>Workflow board</h1><p>The board is a read-only runtime projection.</p></div><span class="read-only-label">Read-only</span></header><section class="card">${emptyState("No Workflow yet", "Describe the delivery goal to the Conversation Agent; it will design and publish the Workflow.", '<button class="button primary" data-open-route="projects">Open project conversation</button>')}</section></div>`;
  }
  return `<div class="kanban-page"><header class="page-heading"><div><span class="eyebrow">KANBAN · REVISION ${workflow.revision}</span><h1>${escapeHtml(workflow.definition.name)}</h1><p>${escapeHtml(workflow.definition.description || "Declarative workflow driven by uniform Columns.")}</p></div><div class="heading-actions"><span class="read-only-label">Read-only board</span><span class="task-total">${tasks.length} tasks</span></div></header><div class="kanban-scroll"><div class="kanban-board" style="--columns:${columns.length}">${columns.map((column) => columnView(column, tasks)).join("")}</div></div></div>`;
}

function columnView(column, tasks) {
  const items = tasks.filter((task) => task.current_column === column.key || (column.terminal && task.status === column.terminal));
  const kind = column.terminal ? `terminal: ${column.terminal}` : column.executor?.kind || "unknown";
  const capabilities = column.executor?.kind === "agent"
    ? (column.executor.capabilities || [])
    : (column.executor?.steps || []).map((step) => step.capability);
  const contract = {
    capabilities,
    input_contract: column.input_contract || {},
    output_contract: column.output_contract || {},
    transitions: column.transitions || [],
    retry: column.retry || {},
    wait_policy: column.wait_policy || {},
  };
  return `<section class="kanban-column ${column.terminal ? `terminal ${column.terminal}` : ""}"><header><span class="column-dot"></span><div><h2>${escapeHtml(column.name)}</h2><small>${escapeHtml(column.key)}</small></div><b>${items.length}</b></header><div class="kanban-cards">${items.length ? items.map(taskCard).join("") : '<div class="column-empty">No Task in this Column</div>'}</div><details class="column-contract"><summary>${escapeHtml(kind)} · runtime contract</summary><pre>${escapeHtml(JSON.stringify(contract, null, 2))}</pre></details></section>`;
}

function taskCard(task) {
  return `<article class="kanban-task"><div class="task-card-head">${badge(task.status)}<time>${relativeTime(task.updated_at)}</time></div><h3>${escapeHtml(task.title)}</h3><p>${escapeHtml(task.brief || "No task brief")}</p><div class="task-card-foot"><code>${escapeHtml(shortId(task.id))}</code><button class="text-button" data-open-route="tasks" data-task-id="${escapeHtml(task.id)}">Details</button></div></article>`;
}
