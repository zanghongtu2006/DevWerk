# DevWerk

DevWerk is a Kanban-centered AI workflow engine. The product core is the
`DevWerk/` FastAPI service: it owns projects, workflow definitions, task state,
dynamic column agents, memory, usage accounting, events, artifacts, MCP, and
the Web UI. Clients such as the IntelliJ plugin, future VS Code providers, CI,
GitHub, or MCP clients provide capabilities and workspace evidence.

The IntelliJ plugin remains the first capability provider and keeps the most
important code-safety rule: every source write through the plugin is protected
by before/after snapshots. The service itself is framework-neutral and must not
depend on IntelliJ, Java, Maven, Gradle, VS Code, or a fixed directory layout.

## Layout

```text
DevWerk/
  app/          FastAPI service, workflow engine, Kanban, memory, MCP, Web UI
  config/       LLM config templates and runtime workflow examples
  tests/        service, workflow, UI, and real-LLM smoke tests
  startup.bat   Windows service launcher

idea-plugin/
  IntelliJ-family capability provider
  source-map collection
  attachments
  IDE diagnostics/compile/process tools
  snapshot-protected apply

docs/
  architecture, workflow runtime, MCP, smoke tests, memory notes

scripts/
packaging/
  packaging helpers for service/plugin distributions
```

The former `backend/` directory has been renamed to `DevWerk/` so the service
can stand on its own as the product core.

## Current Architecture

```text
User / Web Workbench / IDE / MCP client
        |
        v
DevWerk service
  project conversation agent
  workflow designer and validator
  Kanban state machine
  workflow engine
  context compiler and memory writer
  usage/event/artifact persistence
        |
        v
Dynamic workflow column
  column definition -> job_template -> temporary column agent
  context pack -> LLM/tool loop -> phase output
  semantic action -> next column
        |
        v
Capability provider
  source map, file read/search, diagnostics, compile, process.run,
  guarded apply, workspace.write, browser/CDP/Playwright, MCP tools
```

DevWerk asks for semantic capabilities such as `workspace.read`,
`workspace.write`, `project.compile`, `source.diagnostics`, `process.run`, or
browser tools. A provider decides how to implement those names.

## Workflow Model

Projects do not receive hard-coded Kanban columns. A new project starts
unconfigured. The project conversation agent talks with the user, proposes and
saves:

- project operating guide (`Project.MD` style content)
- workflow columns
- semantic actions and transition targets
- node-agent settings and capability requirements
- task policy, retry policy, and failure handling

There are no fixed planner/coder/reviewer Python agents in the runtime. If a
project needs planning, coding, review, verification, writing, research, or
editing, those are workflow columns. When a task reaches an executable column,
DevWerk spawns a temporary column agent from that column definition and destroys
it after the phase output is recorded.

Completion is explicit:

- Success requires a configured action such as `workflow_done`, `complete`, or
  `completed`.
- Failure requires configured actions such as `fail` and `abandon`.
- Retry must target a non-terminal recovery column.
- A column with no outgoing transition is not treated as a hidden success
  terminal.

This is deliberate: Kanban is the task driver, and the state machine must make
task completion auditable instead of guessing.

## Web UI

Start the service, then open:

```text
http://localhost:8000/workbench
http://localhost:8000/dashboard
http://localhost:8000/docs
http://localhost:8000/mcp
```

`/workbench` is the product entry. It provides a project list and a large
project conversation surface. The conversation is used to create projects,
design workflows, revise agents, start tasks, continue active tasks, and
inspect runtime feedback.

`/dashboard`, `/kanban`, `/tasks`, `/events`, `/memory`, `/analytics`, and
`/settings` share the same backend Web shell. Operational data must come from
backend APIs, not frontend mock data.

Web assets are split normally:

```text
DevWerk/app/web/templates/dashboard.html
DevWerk/app/web/static/dashboard.css
DevWerk/app/web/static/dashboard.js
```

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

Legacy `/v1/plan` and `/v1/execute` APIs have been removed. IDE and Web clients
use `/v1/workflows`, workflow messages, and semantic actions.

## LLM Configuration

`.env` should stay small:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
LOG_LEVEL=debug
LOG_FILE_ENABLED=true
LOG_DIR=./data/logs
```

Structured model settings live in `DevWerk/config/llm.json`, which is ignored
by git. `routing.default` is required. Other route keys are aliases that
projects and dynamic workflow columns may reference.

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

The Anthropic-compatible client includes tolerant JSON extraction because some
providers return JSON inside text fields or with repeated/trailing text. The
workflow engine then normalizes valid embedded JSON before applying the state
machine protocol.

## Runtime Data

Default local paths:

```text
DevWerk/data/devwerk.db
DevWerk/data/logs/devwerk.log
DevWerk/data/sessions/{projectId}/audit_events.jsonl
DevWerk/data/sessions/{projectId}/project_memory.json
DevWerk/data/sessions/{projectId}/project_memory.jsonl
```

SQLite is the runtime source of truth for projects, settings, tasks,
conversations, events, artifacts, column runs, revisions, and usage records.
Filesystem JSONL is an audit mirror. Project memory is compact reusable
knowledge carried into tasks; raw prompts are not promoted directly.

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

Backend checks:

```powershell
cd DevWerk
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests -q
```

Real LLM scaffold smoke is opt-in because it spends provider quota:

```powershell
cd DevWerk
$env:DEVWERK_RUN_REAL_PROJECT_SCAFFOLD_SMOKE='1'
.\.venv\Scripts\python.exe -m pytest tests\test_real_project_scaffold_e2e.py -q -s
```

This live smoke starts DevWerk on a temporary port, uses the configured
`routing.default` LLM, creates a project, designs a workflow, starts a task,
uses backend-local file apply, and verifies that a mini-program points-mall
scaffold is written to disk.

Plugin checks:

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
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

## Design Principles

- Kanban first: AI work is workflow, not a hidden chat turn.
- Workflow is project-defined; columns and agents stay independent.
- Completion is explicit; DevWerk does not infer terminal success.
- The service asks for capabilities, not IDE-specific APIs.
- Source maps, diagnostics, project memory, and task memory reduce token cost
  and improve consistency.
- Code writes are guarded through snapshots or backend-local isolated apply.
- Events, artifacts, phase outputs, and logs must make AI task movement
  inspectable.

## License

GNU LGPL 2.1. See [LICENSE](LICENSE).
