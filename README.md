# DevWerk

DevWerk is a pre-release Version 1 multi-agent workflow system. Each Project has one long-lived logical Conversation Agent that communicates with the user, shapes requirements, publishes the Project workflow, dispatches formal Tasks, supervises execution, and handles recovery. Tasks run through a Column-based Kanban state machine until they explicitly reach `done` or `failed`.

Version 1 is not released yet. The repository therefore carries one architecture only; historical implementations and compatibility contracts are intentionally not retained in the working tree.

## Authoritative Design

The following documents define the product and runtime contract:

- [`DevWerk/docs/generic-conversation-agent-and-declarative-column-runtime.md`](DevWerk/docs/generic-conversation-agent-and-declarative-column-runtime.md)
- [`DevWerk/docs/conversation-agent-design-v1.md`](DevWerk/docs/conversation-agent-design-v1.md)
- [`DevWerk/docs/kanban-workflow-design-v1.md`](DevWerk/docs/kanban-workflow-design-v1.md)
- [`DevWerk/docs/conversation-agent-orchestration-soul-p0-design.md`](DevWerk/docs/conversation-agent-orchestration-soul-p0-design.md)

These four locked documents remain equal architecture facts for the general Agent and Kanban Runtime. The approved planning-ownership refinement in `loop-task-plan-decoupling-v1.md` is authoritative for Loop, Workflow Plan, Workflow Revision, Task Plan, and Task boundaries. When code, tests, or secondary documentation conflict with the applicable authority, treat that as a defect. Do not restore an older API or behavior through compatibility patches.

Implemented V1 runtime extensions are specified by:

- [`DevWerk/docs/loop-runtime-v1.md`](DevWerk/docs/loop-runtime-v1.md)
- [`DevWerk/docs/loop-task-plan-decoupling-v1.md`](DevWerk/docs/loop-task-plan-decoupling-v1.md)
- [`DevWerk/docs/kanban-recovering-runtime-v1.md`](DevWerk/docs/kanban-recovering-runtime-v1.md)
- [`DevWerk/docs/agent-tool-rejection-recovery-v1.md`](DevWerk/docs/agent-tool-rejection-recovery-v1.md)
- [`DevWerk/docs/v1-test-contract.md`](DevWerk/docs/v1-test-contract.md)

## Version 1 Boundaries

- V1 pre-release uses full, unredacted local debug tracing for Agent, Provider, Capability, and Runtime inputs and outputs. Functional completion and diagnosis take priority; authentication, approval, privacy controls, redaction, and production log minimization are deferred until after V1.
- One logical Conversation Agent identity per Project.
- Every governance Run preloads the versioned `DevWerk/DEVWERK.md` platform policy.
- Conversation is the user-facing governance mutation surface.
- Kanban, Task, Run, Event, and Artifact views are read-only to the user.
- A Project isolates structured facts by `project_id` and files by canonical `base_dir`.
- One active Workflow per Project, with immutable revisions.
- A reusable Workflow Plan is required before Workflow publication; it contains method and Task Contract facts, never a concrete Task inventory.
- A user objective becomes an immutable Task Plan that selects a Workflow Revision and owns concrete Task inputs, dependencies, conflicts, readiness, and Agent policy.
- A Task is materialized only from `task_plan_id + proposed_task_ref` and remains pinned to that Task Plan's Workflow Revision.
- Column Definitions declare repeatable process stages, context boundaries, execution, contracts, outcomes, transitions, and retry limits.
- Conversation Agent and ephemeral Column Agents share one general-purpose AgentCore and Capability Registry.
- Reusable business process knowledge lives in version-controlled `loops/<name>/loop.meta` and `loop.json` files; SQLite stores only applied Project instances and source provenance, while Python runtime code contains no domain-routing branch.
- A Project's initial Workflow can only be materialized by `loop.apply`; subsequent immutable revisions may be published through `workflow.publish`.
- Columns select either a generic `agent` executor or a generic `capability_sequence` executor.
- Every Column visit creates a Column Run; retry creates an immutable Attempt under that Run.
- Runtime execution may be deterministic or use an ephemeral agent.
- Only `done` and `failed` are Task terminal states; both are reserved Workflow sentinels, not executable Columns.
- V1RuntimePolicy centralizes scheduling, leases, recovery delay, context windows, page sizes, and SQLite limits. V1 does not impose model-iteration, tool-call, or wall-clock budgets on Agent execution.
- Recoverable provider infrastructure failures move the same Task to non-terminal `recovering`; Kanban reclaims the same Column after `next_retry_at` without rebuilding the Task.
- Tool calls rejected before producing a side effect remain visible evidence but do not prevent the Agent from choosing a valid alternative and completing the Column.
- Task dispatch rechecks declared dependencies and conflict domains atomically before execution.
- SQLite is the structured source of truth and uses WAL plus short transactions.
- Large deliverables remain files; SQLite stores Artifact metadata, hashes, sizes, and relationships.
- IDEA Plugin development, memory redesign, user approval boundaries, and multiple active Workflows are outside the current implementation scope.

## Repository Layout

```text
DevWerk/
  app/
    main.py       FastAPI application assembly
    v1/           current domain, store, runtime, Conversation Agent, API
    services/     LLM provider adapters, error classification, usage accounting
    core/         service configuration and logging
    web/          modular native-ES-module Web workbench
  docs/           authoritative V1 design and test contract
  tests/          V1-only automated contract tests
  config/         LLM routing
  loops/          discoverable Loop cards and declarative Workflow bundles
  scripts/        current V1 operational helpers
  startup.bat     project-venv-only service launcher

idea-plugin/      suspended; not part of the standalone V1 release gate
```

## Start

The service must use the existing project virtual environment:

```powershell
cd D:\workspace\DevWerk\DevWerk
.\startup.bat
```

Open:

- `http://127.0.0.1:8000/workbench`
- `http://127.0.0.1:8000/dashboard`
- `http://127.0.0.1:8000/kanban`
- `http://127.0.0.1:8000/tasks`
- `http://127.0.0.1:8000/events`
- `http://127.0.0.1:8000/docs`

## Test

The entire test directory is the Version 1 release contract. There is no secondary historical suite.

```powershell
cd D:\workspace\DevWerk\DevWerk
.\venv\Scripts\python.exe -m pytest tests -q
.\venv\Scripts\python.exe -m compileall app tests
```

The tests cover declarative graph validation, filesystem Loop discovery/application without implicit Task creation, separate immutable Workflow Plan and Task Plan persistence, Task materialization from plan references, initial-Workflow admission, Capability Registry dispatch, Project isolation, persistent Conversation Agent identity, immutable Workflow revisions, shared AgentCore tool loops, deterministic and ephemeral-agent Columns, persistent Writer sessions, explicit terminal paths, Kanban recovery, rejected-before-effect tool handling, SQLite indexes, Artifact boundaries, provider error classification and tool-call normalization, full debug logging, API behavior, and the read-only Web governance boundary.

Conversation messages are stored with stable message IDs and timestamps and are rendered as normal user/Agent turns. Runtime status and tool audit evidence remain outside the human conversation bubbles and update over the Project SSE stream.

Real-provider and three-project black-box acceptance evidence is kept outside the repository in `D:\workspace\codex-devwerk-project-files` so generated products do not pollute source control.

## Review Rule

A change is not reviewable when it reintroduces an API, module, test, configuration surface, or document that belongs to a superseded design. Git history is the archive; the active tree is the current product contract.
