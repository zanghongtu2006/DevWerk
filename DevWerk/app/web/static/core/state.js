export const state = {
  route: "overview",
  health: null,
  projects: [],
  projectId: null,
  project: null,
  conversation: [],
  board: null,
  events: [],
  selectedTaskId: null,
  taskDetail: null,
  agentDetail: null,
  sending: false,
  pendingMessage: "",
  conversationStatus: null,
  conversationHasOlder: false,
  draftMessage: "",
  error: null,
};

export function routeFromPath(pathname) {
  if (pathname === "/dashboard") return "projects";
  if (pathname === "/kanban") return "kanban";
  if (pathname === "/tasks") return "tasks";
  if (pathname === "/events") return "events";
  return "overview";
}

export function updateProjectUrl(pathname, projectId, replace = false) {
  const url = new URL(pathname, location.origin);
  if (projectId) url.searchParams.set("project_id", projectId);
  const value = `${url.pathname}${url.search}`;
  if (replace) history.replaceState({}, "", value);
  return value;
}
