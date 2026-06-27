# DevWerk

DevWerk is a Kanban-centered AI engineering loop. It is moving from an IDE
plugin plus backend into a product-shaped workflow engine: the DevWerk service
owns durable tasks, workflow state, agents, model routing, memory, events, and
tool contracts; clients provide capabilities such as source-map collection,
file apply, diagnostics, compile, command execution, or MCP tools.

The IntelliJ plugin remains the first capability provider and keeps the most
important safety rule: every source write is protected by a before/after
snapshot. The service does not directly mutate the user's repository.

## Layout

```text
DevWerk/
  app/          FastAPI service, workflow engine, agents, Kanban, MCP
  config/       default agents, workflow, LLM config templates
  tests/        service and workflow tests
  startup.bat   Windows service launcher

idea-plugin/
  IntelliJ capability provider
  source-map collection
  attachments
  IDE diagnostics/compile tools
  snapshot-protected apply

docs/
  smoke tests, MCP notes, workflow runtime notes
```

The former `backend/` directory has been renamed to `DevWerk/` so the service
can stand on its own as the product core.

## Architecture

```text
Capability Provider
  IntelliJ plugin, future VS Code provider, CI, GitHub, MCP client
  - projectId
  - source map and selected context
  - attachments
  - client tool execution
  - guarded file apply
        |
        v
DevWerk Service
  - /v1/workflows
  - /v1/kanban/*
  - /dashboard
  - /workbench
  - /mcp
  - durable conversations and compression
  - Kanban workflow state machine
  - per-column agent runs
  - workflow designer
  - project memory and audit events
  - token and request accounting
        |
        v
LLM Catalog
  provider/model refs, routing, parameters, cost-aware role mapping
```

Service code must not depend on IntelliJ, VS Code, Java, Maven, or a fixed
directory layout. It should request named capabilities such as
`project.compile`, `source.diagnostics`, `workspace.read`, or `process.run`.
Each client maps those names to its own implementation.

## Workflow

The default workflow is:

```text
Draft -> Context Indexed -> Planned -> Coding -> Reviewed -> Ready To Apply
      -> Applied -> Verified -> Done
                         \-> Failed
```

Columns are states. Agents are derived at runtime:

```text
column -> job_template -> scheduler -> enabled agent -> model route -> capabilities
```

The default agent catalog lives in `DevWerk/config/agents/default.json`.
The default workflow lives in `DevWerk/config/workflows/default.json`.
Project-specific workflow overrides are stored in the Kanban DB and can be
created from the Web Workbench.

Important runtime rules:

- Kanban is the source of task truth.
- Clients do not move columns directly; they report semantic actions.
- The state machine drives tasks to `done` or `failed`.
- Waiting for a client tool is explicit and bounded by supervisor timeouts.
- Retry is idempotent and resumes from the persisted workflow request.
- Every column run records events, artifacts, and phase outputs.

## Web UIs

Start the service, then open:

```text
http://localhost:8000/dashboard
http://localhost:8000/workbench
http://localhost:8000/docs
```

`/dashboard` shows statistics, projects, Kanban, task details, events, project
memory, and global model routing.

`/workbench` is the product setup entry. It can:

- create a project
- show projects in a left rail and a large project chat on the right
- discuss project workflow, agent behavior, state-machine changes, and tasks
- let the project conversation agent decide whether to reply, save workflow
  design, start a new task, or continue the active task
- keep workflow JSON, agent overrides, memory, and events in dashboard views
  instead of the main conversation surface

The workbench is not limited to coding. It is the standalone product entry for
LLM-driven Kanban projects, including writing, research, review, revision, and
coding workflows.

## Main APIs

### `POST /v1/workflows`

Starts or resumes a coding workflow. The service creates or reuses a Kanban task,
returns `task_id`, `poll_url`, `events_url`, and `result_url`, then runs the
workflow in the background.

Clients should consume:

```text
GET /v1/workflows/{task_id}/events
GET /v1/workflows/{task_id}
GET /v1/workflows/{task_id}/result
POST /v1/workflows/{task_id}/messages
```

The event stream is the primary progress channel. Polling is only a fallback.

### `POST /v1/kanban/tasks/{task_id}/actions`

Reports semantic actions such as:

- `apply_result`
- `retry`
- `abandon`

Internal agents use the same state-machine boundary with actions such as
`approve`, `request_recoding`, `request_replan`, and `fail`.

### `POST /v1/kanban/projects/{project_id}/workflow/design`

Generates or revises a project workflow draft from conversation messages:

```json
{
  "messages": [{"role": "user", "content": "Add design, coding, review and verify gates"}],
  "current_workflow": {},
  "current_agents": {},
  "save": false
}
```

Response includes `workflow`, `agents`, `summary`, `reply`, and validation
warnings. With `save: true`, the service stores the workflow and project agent
overrides.

### `GET/PUT /v1/settings`

Reads and writes the LLM catalog in `DevWerk/config/llm.json`.

## Configuration

`.env` should stay small:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
LOG_LEVEL=debug
LOG_FILE_ENABLED=true
```

Structured model config is kept in JSON:

```text
DevWerk/config/llm.example.json   committed template
DevWerk/config/llm.json           local ignored runtime config
```

Example shape:

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

## Runtime Data

Default local data paths:

```text
DevWerk/data/devwerk.db
DevWerk/data/logs/devwerk.log
DevWerk/data/sessions/{projectId}/audit_events.jsonl
DevWerk/data/sessions/{projectId}/project_memory.json
DevWerk/data/sessions/{projectId}/project_memory.jsonl
```

SQLite is the source of truth for tasks, events, artifacts, conversations,
column runs, and candidate revisions. Task memory is built from the active
task's conversation, events, artifacts, and phase outputs. Project memory is a
compact reusable summary carried into every task, not a raw prompt store.

## Running Locally

```powershell
cd DevWerk
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy config\llm.example.json config\llm.json
.\startup.bat
```

Plugin compile:

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

## Tests

Service checks:

```powershell
cd DevWerk
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests
```

Plugin checks:

```powershell
cd idea-plugin
.\gradlew.bat test verifyPlugin --no-daemon
```

## Packaging

Windows:

```powershell
.\scripts\package-all.ps1
```

or:

```bat
scripts\package-all.bat
```

macOS/Linux:

```sh
sh scripts/package-all.sh
```

Outputs:

```text
dist/devwerk-release.zip              DevWerk service package
dist/idea-plugin/DevWerk-*.zip        IntelliJ Platform plugin install package
```

The service package includes `install` and `start` scripts for Windows and
Unix-like systems. Local secrets and runtime data are excluded, including
`DevWerk/config/llm.json`, `.env*`, `data/`, tests, and bytecode caches.

## Design Principles

- Kanban first: AI work is workflow, not a hidden chat turn.
- Workflow is configurable; columns and agents stay independent.
- The service asks for capabilities, not IDE-specific APIs.
- Source maps and project memory reduce token cost and improve consistency.
- Frontend applies through snapshots; service produces guarded operations.
- Events and artifacts must make fast AI task movement auditable.

## License

GNU LGPL 2.1. See [LICENSE](LICENSE).
