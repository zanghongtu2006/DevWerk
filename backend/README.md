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
8. Return `ops` and/or `patch_ops` to the plugin.

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

When required verification checks all pass, the backend moves the task through
`Verified` to `Done`.

Other client-visible actions include `retry` and `abandon`. No client should call
direct column-move endpoints.

### `GET/PUT /v1/settings`

Reads/writes `config/llm.json`. Dashboard Settings presents two editors:

- LLM Catalog
- Routing

### Kanban APIs

- `GET /v1/kanban/projects`
- `POST /v1/kanban/projects`
- `GET /v1/kanban/board?project_id=...`
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
