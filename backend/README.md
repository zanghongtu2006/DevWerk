# DevWerk Backend

FastAPI backend for DevWerk's kanban-centered engineering loop.

The backend does not write source files directly. It receives capability-client context,
creates or advances kanban tasks, builds planning artifacts, generates guarded
file operations, and waits for a client to apply those changes through its local
snapshot protection. The current capability client is the IntelliJ plugin; the
protocol is intentionally not tied to Java or a particular IDE.

Workflow columns reference job templates, never concrete agents. The scheduler
loads `config/agents/default.json`, selects an enabled agent by role and required
capabilities, and records the derived agent, model route, skills, and grants in
the column run. Workflow topology remains separately configurable in
`config/workflows/default.json` and through project workflow overrides.

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
      "trust_env_proxy": false,
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

LLM HTTP clients ignore `HTTP_PROXY`/`HTTPS_PROXY` by default. Set
`trust_env_proxy: true` on a provider only when that provider must be reached
through the host proxy.

### Backend Logs

Backend logs are written to both stdout and a UTF-8 rotating file. The default
active file is `backend/data/logs/devwerk.log`; at local midnight it is rotated
with a date suffix such as `devwerk.log.2026-06-20`.

```env
LOG_FILE_ENABLED=true
LOG_DIR=./data/logs
LOG_FILE_NAME=devwerk.log
LOG_RETENTION_DAYS=30
```

Rotation uses Python's standard `logging.handlers.TimedRotatingFileHandler`.
The log directory is local runtime data and is ignored by git.

## Workflow

Default kanban states:

```text
Draft -> Context Indexed -> Planned -> Coding -> Reviewed -> Ready To Apply
      -> Applied -> Verified -> Done
```

`Failed` is available from every stage. Reviewer rework is expressed as semantic
actions such as `request_recoding` or `request_replan`; the workflow state
machine chooses the target column and records a reason in events/artifacts.
An interactive stop emits `workflow_run_paused` with `terminal=false`, a
`waiting_for` value, and its reason. It is resumable through the same task id;
only terminal runs emit a completed `workflow_finished` boundary.

Normal agent exploration is intentionally not constrained by a small retry
budget. Project parameters expose high safety ceilings which can be edited in
the dashboard project settings:

```json
{
  "workflow_max_total_runs": 512,
  "workflow_max_rework_runs": 128,
  "planner_max_rounds": 128,
  "agent_tool_max_rounds": 128
}
```

These limits protect against a broken state-machine loop; they are not expected
completion targets. Provider transport retries remain separately bounded.

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
  "decision": "approve",
  "next_action": "execute"
}
```

Planner, coder, and reviewer use independent agent routes and durable column
runs. The backend state machine alone owns transitions. A plan file is a change
candidate unless `required=true`; reviewer does not reject a valid revision just
because an optional candidate path stayed unchanged.

## Conversation And Memory Storage

Conversation and workflow state are durable. They are not held only in Python
process memory.

Default paths:

```text
backend/data/devwerk.db
backend/data/sessions/{projectId}/audit_events.jsonl
backend/data/sessions/{projectId}/project_memory.json
backend/data/sessions/{projectId}/project_memory.jsonl
```

Override the file root:

```env
DEVWERK_SESSION_DIR=./data/sessions
```

SQLite tables:

- `kb_conversations`, `kb_messages`: multi-turn transcript, rolling compression
  summary, pause reason, and active column.
- `kb_column_runs`: independent planner/coder/reviewer invocation checkpoints.
- `kb_revisions`: generated candidate revisions and parent relationships.
- `kb_events`, `kb_artifacts`: workflow audit and phase contracts.

Project memory is updated from every phase output. It keeps compact reusable
  facts: phase summaries, touched paths, framework signals, run commands,
  extracted rules, and tasks seen.
Project memory intentionally does not store raw prompt transcripts.

## Main Endpoints

### `POST /v1/workflows`

Primary coding loop entrypoint. The request returns immediately with a task id;
the backend then drives the kanban workflow in the background.

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
2. Return `poll_url`, `result_url`, and `events_url`.
3. Record request/context artifacts in the background.
4. Move through `Context Indexed`, `Planned`, `Coding`, and `Reviewed` using
   durable per-column agent runs.
5. Reviewer approval moves the task to `Ready To Apply`; reviewer rework moves
   back to `Coding` or `Planned`.
6. Store a `workflow_result` artifact with `phase_output`, `next_action`,
   `ops` and/or `patch_ops`, plus any client-side post-apply `tool_requests`.

Clients poll:

```text
GET /v1/workflows/{task_id}
GET /v1/workflows/{task_id}/events
GET /v1/workflows/{task_id}/result
```

Interactive clients set `interaction_mode: "confirm_plan"`. The planner then
returns a nonterminal result with `waiting_for: "plan_confirmation"`. Continue
the same task with:

```text
POST /v1/workflows/{task_id}/messages
```

Actions are `confirm_plan`, `revise_plan`, `message`, `tool_result`, and
`cancel`. Resume responses include result cursors so SSE/poll clients cannot
mistake an earlier waiting result for the new run.

IDE and API clients should treat `GET /v1/workflows/{task_id}/events` as the
primary progress channel. It streams `workflow_state`, `kanban_event`,
`workflow_column_started`, `workflow_column_completed`,
`workflow_transition_decided`, `agent_context_built`, `agent_output_recorded`,
`heartbeat`, `workflow_result`, and `workflow_error` events as
`text/event-stream`. `GET /v1/workflows/{task_id}` is the fallback state endpoint
for clients that lost the stream.

`/v1/chat` has been removed. IDE and API clients should use workflows instead
of long blocking chat requests.

## Tool Requests

`tool_requests` is an extensible backend-to-client action protocol.

- Local research capabilities: `workspace.list`, `workspace.read`, `workspace.search`
- Remote capabilities: `project.compile`, `source.diagnostics`, `process.run`

Backend research tools are resolved inside an agent run. Client-side evidence
tools pause the current workflow column and resume the same task through a
`tool_result` message. Client-side post-apply tools may also be returned with
`ops` or `patch_ops`; the IDE plugin applies the generated changes first, runs
the tool, then reports the result through `apply_result.verification`.

Example client-side tool request:

```json
{
  "id": "project-compile",
  "tool": "project.compile",
  "args": {
    "timeout_seconds": 300,
    "max_errors": 200
  }
}
```

The IntelliJ provider implements `project.compile` through `CompilerManager`.
Another provider can implement the same capability differently, or advertise
an implementation mapping such as
`{"capability":"project.compile","implementation":"pipeline.compile"}`.
`source.diagnostics` is a fast parser/static check and must not be treated as
compilation. `process.run` remains available for project-specific verification
selected from workspace evidence.

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

Internal agents use the same action boundary with `approve`,
`request_recoding`, `request_replan`, and `fail`.

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

The event stream includes column transitions, workflow actions, agent
context/output records, planner/executor round input-output summaries, reviewer
decisions, tool request results, artifacts, apply results, and verification
outcomes. Dashboard Events is a UI over this endpoint.

### `GET /v1/kanban/projects/{project_id}/memory`

Reads the durable project memory summary.

```json
{
  "ok": true,
  "project_id": "...",
  "memory": {
    "tasks_seen": [],
    "frameworks": [],
    "paths": [],
    "commands": [],
    "rules": [],
    "phase_summaries": []
  }
}
```

Dashboard Memory is a UI over this endpoint.

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
