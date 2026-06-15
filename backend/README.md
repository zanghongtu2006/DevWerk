# DevWerk Backend

FastAPI backend for DevWerk's kanban-centered engineering loop.

The backend does not write source files directly. It receives IDE context,
creates or advances kanban tasks, builds planning artifacts, generates guarded
file operations, and waits for the IntelliJ plugin to apply those changes through
its local snapshot protection.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy config\llm.example.json config\llm.json
```

Edit `config\llm.json` with local API keys and model routing.

Start:

```powershell
startup.bat
```

Default URLs:

```text
http://localhost:8000/dashboard
http://localhost:8000/docs
```

## Configuration

`.env` should stay small. Structured model settings live in JSON:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
```

`config/llm.json` has:

- `llms`: provider definitions, endpoints, keys, models, model parameters
- `routing`: task roles mapped to `provider/model` refs

Example:

```json
{
  "routing": {
    "planner": "deepseek/deepseek-chat",
    "architecture": "minimax/m3",
    "coding": "minimax/m3"
  },
  "llms": {
    "minimax": {
      "api": "anthropic",
      "base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "API_TOKEN",
      "models": {
        "m3": {
          "model": "M3",
          "temperature": 0.2,
          "max_tokens": 4096
        }
      }
    }
  }
}
```

`config/llm.json` is ignored by git. Update `config/llm.example.json` when the
schema changes.

## Workflow

Default kanban states:

```text
Draft -> Context Indexed -> Planned -> Coding -> Ready To Apply
      -> Applied -> Verified -> Done
```

`Failed` is available from every stage. Rework should move a task back to the
right previous state and record a reason in events/artifacts.

Every phase writes a `workflow_phase_output` artifact:

```json
{
  "session_id": "plan-...",
  "phase": "plan",
  "agent": "planner",
  "status_key": "planned",
  "summary": "...",
  "inputs": {},
  "outputs": {},
  "warnings": [],
  "next_action": "execute"
}
```

This is the compatibility contract for future multi-agent scheduling. Planner,
coder, and tester agents may use separate sessions, but the backend state machine
still owns column transitions. Empty file-level plans for coding requests are
treated as planning failures, not successful pure Q&A.

## Main Endpoints

### `POST /v1/chat`

Primary coding loop entrypoint.

Request shape follows `IdeChatRequest`:

```json
{
  "mode": "agent",
  "project_id": "...",
  "task_id": null,
  "messages": [
    {"role": "user", "content": "Implement ..."}
  ],
  "workspace": {
    "source_map": {}
  },
  "tool_results": []
}
```

Behavior:

1. Create a kanban task if `task_id` is missing.
2. Record request/context artifacts.
3. Move to `Context Indexed`.
4. Generate a planning bundle.
5. Move to `Planned`.
6. Generate code changes.
7. Move to `Ready To Apply`.
8. Return `phase_output`, `next_action`, `ops` and/or `patch_ops`, plus any
   client-side post-apply `tool_requests` to the plugin.

## Tool Requests

`tool_requests` is an extensible backend-to-client action protocol.

- Backend research tools: `list_dir`, `read_file`, `search`
- Client-side post-apply tools: currently `run_command`; future IntelliJ SDK
  actions can use the same response field

Backend research tools are resolved inside `/v1/execute` before file operations
are returned. Client-side tools may be returned with `ops` or `patch_ops`; the
IDE plugin applies the generated changes first, runs the tool, then reports the
result through `apply_result.verification`.

Example client-side tool request:

```json
{
  "id": "compile",
  "tool": "run_command",
  "args": {
    "command": ["./mvnw", "test"],
    "timeout_seconds": 120
  }
}
```

The current plugin implementation refuses shell wrappers and only allows
project-local Gradle/Maven executables such as `gradlew`, `mvnw`, `gradle`, and
`mvn`.

### `POST /v1/kanban/tasks/{task_id}/actions`

Single workflow action endpoint. Clients report semantic actions; the backend
state machine decides the next kanban state.

```json
{
  "action": "apply_result",
  "payload": {
    "ok": true,
    "snapshot_id": "optional",
    "changed_paths": ["src/..."],
    "verification": {
      "required": ["compile", "smoke"],
      "results": {
        "compile": "passed",
        "smoke": "passed"
      }
    }
  }
}
```

`apply_result` is terminal in the current single-agent flow. Successful apply
without a verification policy moves through `Applied` and `Verified` to `Done`.
If verification requirements are present, they must all pass; otherwise the task
moves to `Failed`.

Other client-visible actions include `retry` and `abandon`. No client should call
direct column-move endpoints.

### `GET /v1/kanban/events`

Lists project/task event logs for tracing rapid AI workflow movement.

```text
GET /v1/kanban/events?project_id=...&task_id=...&limit=200
```

The event stream includes column transitions, workflow actions, planner/executor
round input-output summaries, tool request results, artifacts, apply results,
and verification outcomes. Dashboard Events is a UI over this endpoint.

### `GET/PUT /v1/settings`

Reads/writes `config/llm.json`. Dashboard Settings presents two editors:

- LLM Catalog
- Routing

### Kanban APIs

- `GET /v1/kanban/projects`
- `POST /v1/kanban/projects`
- `GET /v1/kanban/board?project_id=...`
- `GET /v1/kanban/events?project_id=...&task_id=...`
- `POST /v1/kanban/tasks`
- `GET /v1/kanban/tasks/{task_id}/workflow`
- `POST /v1/kanban/tasks/{task_id}/actions`
- `POST /v1/kanban/tasks/{task_id}/events`
- `POST /v1/kanban/tasks/{task_id}/artifacts`

## Local Checks

```powershell
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests
```

Smoke-test a running backend:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/settings
Invoke-RestMethod http://127.0.0.1:8000/v1/kanban/projects
```

## Notes

- The backend stores SQLite data under `backend/data/` by default.
- Attachments are stored locally.
- The IDE plugin owns snapshot-protected source writes.
- The backend records `snapshot_id` only as an audit value when the plugin sends
  it back in apply result.
