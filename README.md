# DevWerk

DevWerk is a pre-1.0 multi-agent workflow system. Each Project has one long-lived logical Conversation Agent that communicates with the user, shapes requirements, publishes the Project workflow, dispatches formal Tasks, supervises execution, and handles recovery. Tasks run through a Column-based Kanban state machine until they explicitly reach `done` or `failed`.

The current public release is `v0.0.5`. The repository carries one active architecture only; historical implementations and compatibility contracts are intentionally not retained in the working tree.

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
- [`DevWerk/docs/conversation-session-gateway-v1.md`](DevWerk/docs/conversation-session-gateway-v1.md)
- [`DevWerk/docs/memory-and-workcell-runtime-v0.1.0.md`](DevWerk/docs/memory-and-workcell-runtime-v0.1.0.md)
- [`DevWerk/docs/v1-test-contract.md`](DevWerk/docs/v1-test-contract.md)

## Version 1 Boundaries

- V1 pre-release uses full, unredacted local debug tracing for Agent, Provider, Capability, and Runtime inputs and outputs. Functional completion and diagnosis take priority; authentication, approval, privacy controls, redaction, and production log minimization are deferred until after V1.
- One logical Conversation Agent identity per Project.
- One durable Conversation Session per Project; each Turn is a short-lived background Run, and Turn failure does not terminate the Session.
- Every governance Run preloads the versioned `DevWerk/DEVWERK.md` platform policy.
- Conversation is the user-facing governance mutation surface.
- Kanban, Task, Run, Event, and Artifact views are read-only to the user.
- A Project isolates structured facts by `project_id` and files by canonical `base_dir`.
- One active Workflow per Project, with immutable revisions.
- A reusable Workflow Plan is required before Workflow publication; it contains method and Task Contract facts, never a concrete Task inventory.
- A user objective becomes an immutable Task Plan that selects a Workflow Revision and owns concrete Task inputs, dependencies, conflicts, readiness, and Agent policy.
- A Task is materialized only from `task_plan_id + proposed_task_ref` and remains pinned to that Task Plan's Workflow Revision.
- Column Definitions declare repeatable process stages, context boundaries, execution, contracts, outcomes, transitions, and retry limits.
- Conversation Agent, single-Agent Columns, and Workcell Agent participants share one general-purpose AgentCore and Capability Registry.
- Reusable business process knowledge lives in version-controlled `loops/<name>/loop.meta` and `loop.json` files; SQLite stores only applied Project instances and source provenance, while Python runtime code contains no domain-routing branch.
- A Project's initial Workflow can only be materialized by `loop.apply`; subsequent immutable revisions may be published through `workflow.publish`.
- Columns select a generic `agent`, `capability_sequence`, or directed `workcell` executor. Workcells support arbitrary named Agent or deterministic participants, stable participant Sessions, receiver-scoped handoffs, and inner rework without falsely failing the Task.
- Every Column visit creates a Column Run; retry creates an immutable Attempt under that Run.
- Runtime execution may be deterministic, use one logical Agent, or coordinate persistent Workcell participants.
- Only `done` and `failed` are Task terminal states; both are reserved Workflow sentinels, not executable Columns.
- V1RuntimePolicy centralizes scheduling, leases, recovery delay, context windows, page sizes, and SQLite limits. V1 does not impose model-iteration, tool-call, or wall-clock budgets on Agent execution.
- Recoverable provider infrastructure failures move the same Task to non-terminal `recovering`; Kanban reclaims the same Column after `next_retry_at` without rebuilding the Task.
- Tool calls rejected before producing a side effect remain visible evidence but do not prevent the Agent from choosing a valid alternative and completing the Column.
- Task dispatch rechecks declared dependencies and conflict domains atomically before execution.
- SQLite is the transactional Runtime source of truth and uses WAL plus short transactions. Human-readable semantic Memory is Project-local Markdown under `.devwerk/memory`; optional search indexes are rebuildable providers rather than the authority.
- Large deliverables remain files; SQLite stores Artifact metadata, hashes, sizes, and relationships.
- Conversation history remains durable execution evidence. File-first semantic Memory and a replaceable index boundary are included in the v0.1.0 design; vector indexing remains an optional provider. IDEA Plugin development, user approval boundaries, and multiple active Workflows remain outside the current implementation scope.

## Repository Layout

```text
DevWerk/
  app/
    main.py       FastAPI application assembly
    v1/           current domain, File Memory, Workcell runtime, Conversation Agent, API
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

## Download and Docker

Download the current standalone ZIP package from [GitHub Releases](https://github.com/zanghongtu2006/DevWerk/releases/latest), or download the `v0.0.5` asset directly:

```text
https://github.com/zanghongtu2006/DevWerk/releases/download/v0.0.5/devwerk-release.zip
```

Docker Hub is the recommended container source:

```bash
docker pull zanghongtu2006/devwerk:v0.0.5
```

The same image is also published to GitHub Container Registry:

```bash
docker pull ghcr.io/zanghongtu2006/devwerk:v0.0.5
```

Create persistent volumes and start DevWerk:

```bash
docker volume create devwerk-data
docker volume create devwerk-projects
docker run -d --name devwerk --restart unless-stopped -p 8000:8000 -v devwerk-data:/opt/devwerk/data -v devwerk-projects:/workspace zanghongtu2006/devwerk:v0.0.5
```

Open `http://127.0.0.1:8000/workbench`. Project base directories created inside the container should use `/workspace/...` so generated files remain in the `devwerk-projects` volume.

For LLM-backed Conversation and Column Agents, copy `DevWerk/config/llm.example.json` to a host `llm.json`, configure the provider credentials, and mount that individual file without hiding the image's remaining configuration:

```bash
docker run -d --name devwerk --restart unless-stopped -p 8000:8000 -v devwerk-data:/opt/devwerk/data -v devwerk-projects:/workspace --mount type=bind,source=/absolute/path/to/llm.json,target=/opt/devwerk/config/llm.json,readonly zanghongtu2006/devwerk:v0.0.5
```

Follow runtime logs with:

```bash
docker logs -f devwerk
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

The tests cover declarative outer and Workcell graph validation, filesystem Loop discovery, immutable planning and Task materialization, Capability Registry dispatch, Project isolation, File Memory provider substitution/versioning/search, persistent Conversation and Workcell participant Sessions, receiver-scoped feedback, provider recovery, deterministic participants, explicit terminal paths, SQLite indexes, Artifact boundaries, provider contracts, logging, APIs, and the read-only Web governance boundary.

Conversation messages are stored with stable message IDs and timestamps and are rendered as normal user/Agent turns. Runtime status and tool audit evidence remain outside the human conversation bubbles and update over the Project SSE stream.

Real-provider and three-project black-box acceptance evidence is kept outside the repository in `D:\workspace\codex-devwerk-project-files` so generated products do not pollute source control.

## Review Rule

A change is not reviewable when it reintroduces an API, module, test, configuration surface, or document that belongs to a superseded design. Git history is the archive; the active tree is the current product contract.
