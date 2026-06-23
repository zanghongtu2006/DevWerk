# DevWerk Service

This directory is the DevWerk product core. It was formerly named `backend`,
but it now stands as the independent FastAPI service that owns Kanban workflow,
agents, project memory, model routing, MCP, Web UI, and durable task state.

The service does not write project source files directly. Capability providers
such as the IntelliJ plugin, a future VS Code client, CI, GitHub, or an MCP
client provide context and execute granted local tools. Source writes still go
through the provider's guarded apply path.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy config\llm.example.json config\llm.json
.\startup.bat
```

Default URLs:

```text
http://localhost:8000/dashboard
http://localhost:8000/workbench
http://localhost:8000/docs
http://localhost:8000/mcp
```

## Configuration

`.env` should stay small:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
LOG_LEVEL=debug
LOG_FILE_ENABLED=true
LOG_DIR=./data/logs
```

Structured model settings live in `config/llm.json`, which is ignored by git.
Use `config/llm.example.json` as the committed template.

```json
{
  "routing": {
    "default": "minimax/m3",
    "planner": "minimax/m3",
    "executor": "minimax/m3",
    "reviewer": "minimax/m3"
  },
  "llms": {
    "minimax": {
      "api": "anthropic",
      "base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "API_TOKEN",
      "trust_env_proxy": false,
      "models": {
        "m3": {
          "model": "M3",
          "temperature": 0.2,
          "max_tokens": 4096,
          "thinking_mode": "max"
        }
      }
    }
  }
}
```

## Workflow Model

The default managed workflow is:

```text
Draft -> Context Indexed -> Planned -> Coding -> Reviewed -> Ready To Apply
      -> Applied -> Verified -> Done
                         \-> Failed
```

Columns are states. Agents are derived:

```text
column -> job_template -> scheduler -> enabled agent -> model route -> capabilities
```

Default definitions:

```text
config/workflows/default.json
config/agents/default.json
```

Project overrides are stored in SQLite and can be edited through `/workbench`.

## Workbench

`/workbench` is the independent Web entry for project setup. It can:

- create a project
- load the active project workflow
- discuss a process in a chat-style panel
- generate workflow and agent override drafts through the planner LLM
- fall back to a valid local draft if no LLM is configured
- save columns, transitions, actions, and agent overrides

The designer API is:

```text
POST /v1/kanban/projects/{project_id}/workflow/design
```

## Runtime Data

Default paths:

```text
data/devwerk.db
data/logs/devwerk.log
data/sessions/{projectId}/audit_events.jsonl
data/sessions/{projectId}/project_memory.json
data/sessions/{projectId}/project_memory.jsonl
```

SQLite is the source of truth for tasks, conversations, events, artifacts,
column runs, and candidate revisions. Project memory is only a compact summary.

## Main APIs

- `POST /v1/workflows`
- `GET /v1/workflows/{task_id}`
- `GET /v1/workflows/{task_id}/events`
- `GET /v1/workflows/{task_id}/result`
- `POST /v1/workflows/{task_id}/messages`
- `GET /v1/kanban/projects`
- `POST /v1/kanban/projects`
- `GET/PUT /v1/kanban/projects/{project_id}/workflow`
- `POST /v1/kanban/projects/{project_id}/workflow/design`
- `POST /v1/kanban/tasks/{task_id}/actions`
- `GET/PUT /v1/settings`

## Checks

```powershell
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests
```

## Notes

- The service is framework-neutral and must not hard-code Java, IntelliJ,
  Maven, Gradle, VS Code, or CI logic.
- Tool requests use semantic capability names such as `project.compile`,
  `source.diagnostics`, `workspace.read`, and `process.run`.
- Capability providers map those names to their own SDK or runtime.
- The IntelliJ plugin owns snapshot-protected source writes.
