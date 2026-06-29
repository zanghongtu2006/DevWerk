# DevWerk Service

DevWerk is the product core: a FastAPI service that owns projects, Kanban
workflow definitions, dynamic workflow-node agents, durable task state, project
memory, usage accounting, MCP, and the Web UI.

The service does not write source files directly. Capability providers such as
the IntelliJ plugin, a future VS Code client, CI, GitHub, or an MCP client supply
workspace evidence and execute granted capabilities. Source writes still go
through the provider's guarded apply and snapshot path.

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
http://localhost:8000/workbench
http://localhost:8000/dashboard
http://localhost:8000/kanban
http://localhost:8000/tasks
http://localhost:8000/docs
http://localhost:8000/mcp
```

## LLM Configuration

`.env` should stay small:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
LOG_LEVEL=debug
LOG_FILE_ENABLED=true
LOG_DIR=./data/logs
```

Structured model settings live in `config/llm.json`, which is ignored by git.
`routing.default` is required. Other route keys are optional aliases that
projects or dynamically spawned workflow-node agents may reference.

```json
{
  "routing": {
    "default": "minimax/m3",
    "project": "minimax/m3",
    "context-indexer": "minimax/m3"
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

DevWerk no longer creates default Kanban columns. A project starts as
`unconfigured`. The project conversation agent must define columns, semantic
actions, transition rules, node agents, and capability requirements before a
task can run.

Runtime flow:

```text
project conversation -> saved workflow definition -> task -> workflow engine
  -> column -> job_template -> scheduler -> temporary node agent
  -> phase artifact/events/revision -> semantic action -> next column
```

Only two built-in agents exist:

- `project-agent`: talks with the user to create and maintain projects,
  workflows, node agents, and task requests.
- `context-indexer`: a local no-LLM helper that turns client source maps and
  diagnostics into compact project context when a workflow column asks for it.

All other agents are spawned from workflow columns. If a column references an
unknown `job_template`, DevWerk derives a temporary `{column}-agent` from the
project agent defaults or a project override. The agent is disposed after the
column run; durable state is kept as artifacts, events, revisions, task memory,
and project memory.

## Web UI

`/workbench`, `/dashboard`, `/kanban`, and `/tasks` share the same backend Web
shell. HTML, CSS, and JavaScript are split under:

```text
app/web/templates/dashboard.html
app/web/static/dashboard.css
app/web/static/dashboard.js
```

All displayed projects, tasks, usage, events, memory, workflow columns, and
settings are loaded from backend APIs. The UI must not use demo metrics or
front-end mock data for operational views.

## Main APIs

- `POST /v1/workflows`
- `GET /v1/workflows/{task_id}`
- `GET /v1/workflows/{task_id}/events`
- `GET /v1/workflows/{task_id}/result`
- `POST /v1/workflows/{task_id}/messages`
- `GET /v1/usage/summary?project_id=&task_id=`
- `GET /v1/kanban/projects`
- `POST /v1/kanban/projects`
- `GET/PUT /v1/kanban/projects/{project_id}/workflow`
- `POST /v1/kanban/projects/{project_id}/workflow/design`
- `POST /v1/kanban/tasks/{task_id}/actions`
- `GET/PUT /v1/settings`

Legacy `/v1/plan` and `/v1/execute` APIs have been removed. Clients should use
`/v1/workflows` and workflow message/action APIs.

## Runtime Data

Default paths:

```text
data/devwerk.db
data/logs/devwerk.log
data/sessions/{projectId}/audit_events.jsonl
data/sessions/{projectId}/project_memory.json
data/sessions/{projectId}/project_memory.jsonl
```

SQLite is the source of truth for projects, settings, columns, tasks,
conversations, events, artifacts, column runs, and candidate revisions. Project
memory is a compact reusable summary; it is not a raw transcript.

## Checks

```powershell
cd DevWerk
$env:LOG_FILE_ENABLED='false'
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests -q

cd ..\idea-plugin
.\gradlew.bat compileKotlin
```

## Ground Rules

- The service is framework-neutral and must not hard-code Java, IntelliJ,
  Maven, Gradle, VS Code, CI, `src`, `test`, or business directory layouts.
- Tool requests use semantic capability names such as `project.compile`,
  `source.diagnostics`, `workspace.read`, and `process.run`.
- Capability providers map those names to their own SDK or runtime.
- Kanban is the task driver. Human-visible dashboard state is an audit view,
  not a manual drag-and-drop state machine.
