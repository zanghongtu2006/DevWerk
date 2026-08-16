export class ApiError extends Error {
  constructor(message, status = 0, detail = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 20_000);
  try {
    const response = await fetch(`/v1${path}`, {
      method: options.method || "GET",
      headers: { Accept: "application/json", "Content-Type": "application/json", ...(options.headers || {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = payload?.detail || payload;
      throw new ApiError(typeof detail === "string" ? detail : `Request failed (HTTP ${response.status})`, response.status, detail);
    }
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new ApiError("Request timed out. Check the runtime and try again.", 0);
    if (error instanceof ApiError) throw error;
    throw new ApiError(`Unable to connect to DevWerk: ${error.message || error}`, 0);
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  get(path, options) { return request(path, options); },
  post(path, body, options = {}) { return request(path, { ...options, method: "POST", body }); },
};

export async function loadProjectBundle(projectId) {
  const [project, conversation, conversationStatus, snapshot, events] = await Promise.all([
    api.get(`/projects/${projectId}`),
    api.get(`/projects/${projectId}/conversation?limit=150`),
    api.get(`/projects/${projectId}/conversation-state`),
    api.get(`/projects/${projectId}/projection`),
    api.get(`/projects/${projectId}/events?limit=500`),
  ]);
  const board = {
    project,
    workflow: snapshot.projection.workflow,
    tasks: snapshot.projection.tasks,
    version: snapshot.version,
  };
  return { conversation, conversationStatus, board, events };
}

export async function loadTaskDetail(projectId, taskId) {
  const [task, events] = await Promise.all([
    api.get(`/projects/${projectId}/tasks/${taskId}`),
    api.get(`/projects/${projectId}/tasks/${taskId}/events?limit=250`),
  ]);
  return { ...task, events };
}

export function openProjectStream(projectId, after, onEvent, onError) {
  const stream = new EventSource(`/v1/projects/${projectId}/stream?after=${after || 0}`);
  stream.addEventListener("project", (message) => onEvent(JSON.parse(message.data)));
  stream.onerror = onError;
  return stream;
}
