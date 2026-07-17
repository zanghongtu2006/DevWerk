import { escapeHtml, shortId } from "../core/format.js";
import { icon } from "./components.js";

export function renderProjectRail(projects, selectedId, query = "") {
  const list = document.getElementById("project-list");
  if (!list) return;
  const needle = query.trim().toLowerCase();
  const visible = projects.filter((project) => !needle || `${project.name} ${project.description} ${project.base_dir}`.toLowerCase().includes(needle));
  list.innerHTML = visible.length
    ? visible.map((project) => `<button class="project-row ${project.id === selectedId ? "active" : ""}" data-project-id="${escapeHtml(project.id)}"><span class="project-avatar">${escapeHtml(project.name.slice(0, 2).toUpperCase())}</span><span><b>${escapeHtml(project.name)}</b><small>${escapeHtml(project.description || project.base_dir)}</small></span><span class="project-chevron">${icon("arrow")}</span></button>`).join("")
    : `<div class="rail-empty">${projects.length ? "没有匹配的 Project" : "尚未创建 Project"}</div>`;
}
export function setProjectContext(project, tasks = []) {
  const name = document.getElementById("context-project");
  const id = document.getElementById("context-id");
  const status = document.getElementById("context-status");
  if (!project) {
    name.textContent = "No project selected";
    id.textContent = "Create a Project to begin";
    status.className = "context-status neutral";
    status.innerHTML = "<i></i>Idle";
    return;
  }
  name.textContent = project.name;
  id.textContent = `${shortId(project.id)} · ${project.base_dir}`;
  const running = tasks.some((task) => ["running", "recovering"].includes(task.status));
  status.className = `context-status ${running ? "active" : "success"}`;
  status.innerHTML = `<i></i>${running ? "Active delivery" : "Project ready"}`;
}

export function setConnection(tone, label) {
  const element = document.getElementById("runtime-status");
  element.className = `runtime-status ${tone}`;
  element.innerHTML = `<i></i>${escapeHtml(label)}`;
}

export function setBusy(active, label = "正在加载…") {
  document.documentElement.classList.toggle("is-busy", active);
  const bar = document.getElementById("loading-bar");
  const text = document.getElementById("loading-label");
  bar.setAttribute("aria-hidden", active ? "false" : "true");
  text.textContent = active ? label : "";
}

export function showProjectDialog() {
  const dialog = document.getElementById("project-dialog");
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => dialog.querySelector("input")?.focus());
}

export function closeProjectDialog() {
  const dialog = document.getElementById("project-dialog");
  if (dialog?.open) dialog.close();
}

export function showToast(message, tone = "neutral") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast ${tone} visible`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

export function toggleProjectRail(force) {
  const shell = document.querySelector(".app-shell");
  shell.classList.toggle("rail-open", force === undefined ? !shell.classList.contains("rail-open") : force);
}
