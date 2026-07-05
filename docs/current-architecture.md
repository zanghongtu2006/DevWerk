# Current Architecture

This document records the current DevWerk architecture after the dynamic
workflow refactor.

## Product Boundary

DevWerk is the backend-owned workflow product. It owns:

- projects and project settings
- project conversation
- workflow definitions
- Kanban state machine
- dynamic workflow-column agents
- task state, artifacts, events, revisions, and usage records
- project memory and task memory
- MCP and Web UI entry points

Clients provide capabilities. The first client is the IntelliJ-family plugin,
but DevWerk must not depend on IntelliJ APIs, Java project layouts, Maven,
Gradle, VS Code, CI, or any fixed source directory structure.

## Runtime Flow

```text
project conversation
  -> workflow design and validation
  -> saved project workflow
  -> task request
  -> workflow engine
  -> current column
  -> temporary column agent
  -> phase output / artifact / optional code revision
  -> semantic action
  -> state-machine transition
  -> done or failed
```

The project conversation is the user-facing entry for both project design and
task dispatch. A conversation turn may:

- reply only
- save or revise workflow design
- start a new task
- continue the active task
- ask for more information

## Workflow Definitions

A project workflow defines columns and semantic actions. Columns are not global
defaults. Different projects can define completely different workflows.

Executable columns may define:

- `status_key`
- `title`
- `position`
- `transition_to`
- `job_template`
- `input_artifacts`
- `output_artifact`
- `success_action`
- `failure_actions`
- `context_policy`

Actions map semantic names to status targets:

```json
{
  "code_ready": {"to": "ready_to_apply"},
  "apply_succeeded": {"to": "verifying"},
  "workflow_done": {"to": "done"},
  "fail": {"to": "failed"},
  "abandon": {"to": "failed"},
  "retry": {"to": "analyzing"}
}
```

The engine validates that success and failure actions are explicit. A column
with no outgoing transition is not an implicit success terminal.

## Agent Lifecycle

Only these built-ins are stable concepts:

- `project-agent`: user conversation, project/workflow design, task dispatch
- `context-indexer`: local context helper for source maps and diagnostics

All other agents are runtime products of workflow columns:

1. The engine reaches an executable column.
2. The `JobScheduler` resolves the column `job_template`.
3. If no explicit template exists, a generic dynamic template is derived.
4. A temporary `{column}-agent` is spawned from project/default settings.
5. The context compiler builds the smallest useful context pack.
6. The LLM/tool loop runs.
7. The engine records phase output, artifacts, events, revisions, and memory
   writeback.
8. The state machine applies the semantic action.
9. The temporary agent is gone.

Durable knowledge lives in artifacts, events, task memory, project memory, and
usage records, not in a long-lived hidden agent object.

## Context Pack

Column agents receive context assembled by the backend:

- original user request
- task metadata
- current workflow summary
- required prior artifacts
- recent task events
- task conversation summary
- task memory
- project memory
- source-map/code-context summary when provided
- diagnostics and tool results when provided
- available client/backend capabilities

Agents do not read the whole memory store directly. They can submit structured
writeback, and the backend decides what becomes task memory or project memory.

## Code Results And Apply

The workflow engine recognizes concrete file changes from:

- top-level `ops`
- `outputs.ops`
- `patch_ops`
- file bundles with `files` entries containing `path` and `content`, including
  `code_patch.files`, `staged_patch.files`, `source_bundle.files`, and other
  shape-compatible containers

IDE mode waits for the client to apply changes through snapshot protection and
report `apply_result`.

Backend-local mode can apply changes into an explicitly supplied isolated
`project_root` and records `backend_local_apply_result`. Coding workflows accept
both apply-result types when evaluating completion guards.

## Completion Semantics

DevWerk requires explicit completion:

- success: `workflow_done`, `complete`, or `completed`
- failure: `fail` and `abandon`
- retry: a non-terminal recovery target

Coding workflows must also satisfy the done guard:

- a successful plugin `apply_result`, or
- a successful `backend_local_apply_result`, and
- either verification policy success, explicit verification skip by policy, or
  completion from a post-apply verification/review column.

If those conditions are not satisfied, `workflow_done` is ignored and an audit
event explains why.

## Provider Output Normalization

Provider behavior is not assumed to be perfect. DevWerk currently normalizes:

- embedded JSON in `raw_text`, `reply`, `summary`, or `content`
- first valid JSON object in repeated/trailing text
- common semantic aliases such as `done` or `success`
- target-column style outputs into configured workflow actions
- MiniMax/Anthropic-compatible response quirks

Normalization may map output into the active workflow protocol. It must not
invent hidden columns or hidden terminal states.

## Persistence

SQLite is the source of truth. Important tables use the `kb_` family for Kanban
and workflow records.

Runtime data includes:

- projects and project settings
- workflow definitions
- columns
- tasks
- task conversations and messages
- events
- artifacts
- revisions
- column runs
- usage records
- memory items

Filesystem logs and JSONL files are audit mirrors.

## Observability

Every meaningful step should be visible through events, artifacts, logs, or
memory:

- project conversation message
- workflow design saved or failed
- workflow worker started/stopped
- column started/completed
- agent context built
- phase output recorded
- semantic transition decided
- code result created
- apply result recorded
- verification/failure bundle recorded

Debug logs should include enough request, action, phase, and normalized-output
detail to diagnose state-machine failures without guessing.

## Test Guardrails

Core tests must protect:

- no default workflow columns for new projects
- explicit terminal actions only
- dynamic column-agent execution
- project conversation start/continue task behavior
- provider JSON repair/normalization
- code file-bundle conversion into file ops
- plugin and backend-local apply paths
- post-apply verification completion
- usage breakdown by project/task/agent
- Web UI views loading from backend APIs
- opt-in real LLM scaffold smoke

See `docs/smoke-tests.md` for commands.
