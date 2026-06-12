# DevWerk

DevWerk is a kanban-centered AI engineering loop for IDE-driven code generation.

The core idea is simple: AI should not be a loose text box that writes into a
repository. Every coding action should become a visible engineering task, move
through a small workflow, produce artifacts, and return guarded changes to the
IDE plugin. The plugin remains responsible for snapshot-protected writes.

DevWerk is evolving from a basic CodeOps backend into a loop engineering and
harness system:

- The IDE plugin collects project context, source maps, attachments, and applies
  returned changes through its local snapshot safety layer.
- The backend owns kanban workflow, model routing, planning artifacts, coder
  harness rules, token accounting, and generated patch/file operations.
- Kanban is the operating surface. `/v1/chat` is no longer just a chat endpoint;
  it creates or advances a kanban task.

## Architecture

```text
IntelliJ Plugin
  - projectId from .devwerk/meta
  - source map and selected context
  - attachment upload
  - snapshot-protected apply
        |
        v
DevWerk Backend (FastAPI)
  - /v1/chat kanban workflow entry
  - coder harness from source_map
  - planning bundle artifacts
  - patch/file operation generation
  - apply-result and verification state
  - local SQLite usage accounting
        |
        v
LLM Catalog
  - provider/model refs
  - task routing
  - per-model parameters
  - cloud or local endpoints
```

## Kanban Workflow

The default workflow is intentionally short. Columns represent main task states;
details such as requirements, design, verification checks, and rework reasons are
stored as events or artifacts.

```text
Draft
Context Indexed
Planned
Coding
Ready To Apply
Applied
Verified
Done
Failed
```

State meaning:

- `Draft`: A user request exists. The task may have been created by `/v1/chat`.
- `Context Indexed`: The backend received source map, selected context, and
  attachment metadata. This phase should use as little LLM work as possible.
- `Planned`: The backend saved a planning bundle containing requirement
  breakdown, system design, implementation plan, and verification policy.
- `Coding`: The coder harness is generating guarded changes from the plan.
- `Ready To Apply`: Changes are ready for the plugin. The backend has not
  written to the repository.
- `Applied`: The plugin applied changes through its snapshot-protected write
  path and reported the result.
- `Verified`: Required verification checks passed.
- `Done`: The task is closed.
- `Failed`: A phase failed. Rework should move the task back to the appropriate
  earlier state rather than adding more columns.

## Planning Artifacts

`Planned` is a state, not a single string. DevWerk stores a planning bundle:

```json
{
  "requirement_breakdown": {
    "summary": "...",
    "goals": [],
    "non_goals": [],
    "acceptance_criteria": [],
    "constraints": []
  },
  "system_design": {
    "summary": "...",
    "components": [],
    "api_changes": [],
    "storage_changes": [],
    "risks": []
  },
  "implementation_plan": {
    "steps": [],
    "files_to_touch": [],
    "warnings": []
  },
  "verification_policy": {
    "required": ["compile", "smoke"],
    "optional": ["unit", "integration"],
    "results": {}
  }
}
```

For small tasks, sections may be short. For larger tasks, this artifact becomes
the place where requirement decomposition and system design are preserved without
turning the kanban board into a long list of micro-columns.

## Coder Harness

The backend builds a zero-token coder skill from the IDE-provided source map.
It detects common project shapes such as:

- DevWerk monorepo
- IntelliJ plugin
- FastAPI backend
- Spring Boot
- React or Vue
- generic Python/JVM projects

The harness tells the model which framework it is looking at, which paths are
representative, and what writing rules should be respected. This lets the backend
save tokens by scanning project structure locally before asking an LLM to reason.

## LLM Configuration

LLM configuration is stored outside `.env` because it is structured and should be
hand-editable.

```text
backend/config/llm.example.json   committed template
backend/config/llm.json           local ignored runtime config
```

`.env` points to the runtime config:

```env
DEVWERK_LLM_CONFIG_PATH=./config/llm.json
```

The config has two top-level sections:

```json
{
  "routing": {
    "default": "minimax/m3",
    "planner": "deepseek/deepseek-chat",
    "architecture": "minimax/m3",
    "coding": "minimax/m3",
    "compression": "ollama/deepseek-r1:32b"
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

Model refs use `provider/model`. This is inspired by OpenClaw-style model refs
and Hermes-style main/auxiliary slot routing, but DevWerk maps them to engineering
loop roles such as planning, architecture, coding, and compression.

## Main APIs

### `POST /v1/chat`

Kanban workflow entrypoint. If `task_id` is missing, the backend creates a task.
It then records context, plans, codes, and returns changes in `Ready To Apply`.

Important response fields:

```json
{
  "ok": true,
  "task_id": "...",
  "status_key": "ready_to_apply",
  "planning": {},
  "ops": [],
  "patch_ops": []
}
```

### `POST /v1/kanban/tasks/{task_id}/apply-result`

Called by the plugin after it applies returned changes. The plugin snapshot layer
is local and atomic; the backend only records the result.

```json
{
  "ok": true,
  "snapshot_id": "optional-audit-id",
  "changed_paths": ["src/..."],
  "verification": {
    "required": ["compile", "smoke"],
    "results": {
      "compile": "passed",
      "smoke": "passed"
    }
  }
}
```

### `GET/PUT /v1/settings`

Reads and writes `backend/config/llm.json`. The dashboard exposes this as two
JSON editors: `LLM Catalog` and `Routing`.

### `GET /dashboard`

Local web UI for:

- statistics
- projects
- kanban
- global model/routing settings
- project settings

## Repository Layout

```text
backend/
  app/
    core/        configuration and schema
    models/      IDE and planning response models
    routes/      IDE, kanban, settings, dashboard routes
    services/    LLM clients, kanban DB, usage DB, coder harness
  config/
    llm.example.json
  tests/
  startup.bat
  requirements.txt

idea-plugin/
  IntelliJ plugin frontend
  source map collection
  attachments
  snapshot-protected code apply
```

## Running Locally

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy config\llm.example.json config\llm.json
startup.bat
```

Open:

```text
http://localhost:8000/dashboard
http://localhost:8000/docs
```

Plugin build:

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

`gradlew build` may fail at `prepareSandbox` if IntelliJ is currently holding the
sandbox plugin jar open. In that case, Kotlin compilation and jar creation may
still be valid while sandbox copy is blocked by the IDE process.

## Tests

Backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests
```

## Design Principles

- Kanban first: AI work should be visible as workflow, not hidden as a chat turn.
- Backend plans and generates; frontend applies through snapshots.
- Columns stay short; details live in events, artifacts, and checklists.
- Source maps are zero-token structure that should reduce LLM context cost.
- Multiple models exist to optimize cost and stability by task type.
- Rework is a transition reason, not a board column.

## License

GNU LGPL 2.1. See [LICENSE](LICENSE).
