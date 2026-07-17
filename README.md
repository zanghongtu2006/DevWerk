# DevWerk

DevWerk is a pre-release Version 1 multi-agent workflow system. Each Project has one long-lived logical Conversation Agent that communicates with the user, shapes requirements, publishes the Project workflow, dispatches formal Tasks, supervises execution, and handles recovery. Tasks run through a Column-based Kanban state machine until they explicitly reach `done` or `failed`.

Version 1 is not released yet. The repository therefore carries one architecture only; historical implementations and compatibility contracts are intentionally not retained in the working tree.

## Authoritative Design

The following documents define the product and runtime contract:

- [`DevWerk/docs/generic-conversation-agent-and-declarative-column-runtime.md`](DevWerk/docs/generic-conversation-agent-and-declarative-column-runtime.md) — normative implementation source of truth
- [`DevWerk/docs/conversation-agent-design-v1.md`](DevWerk/docs/conversation-agent-design-v1.md)
- [`DevWerk/docs/kanban-workflow-design-v1.md`](DevWerk/docs/kanban-workflow-design-v1.md)
- [`DevWerk/docs/v1-test-contract.md`](DevWerk/docs/v1-test-contract.md)

When code, tests, or secondary documentation conflict with these documents, treat that as a defect. Do not restore an older API or behavior through compatibility patches.

## Version 1 Boundaries

- One logical Conversation Agent identity per Project.
- Conversation is the user-facing governance mutation surface.
- Kanban, Task, Run, Event, and Artifact views are read-only to the user.
- A Project isolates structured facts by `project_id` and files by canonical `base_dir`.
- One active Workflow per Project, with immutable revisions.
- A Task is pinned to the Workflow revision active when it is created.
- Column Definitions declare execution, contracts, outcomes, transitions, retry limits, and terminal meaning.
- Conversation Agent and ephemeral Column Agents share one general-purpose AgentCore and Capability Registry.
- Conversation-created Workflow revisions are data; source code contains no business prompt or Workflow template.
- Columns select either a generic `agent` executor or a generic `capability_sequence` executor.
- Every Column entry creates an independent Column Run record.
- Runtime execution may be deterministic or use an ephemeral agent.
- Only `done` and `failed` are Task terminal states, and both are explicit Workflow terminals.
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
  config/         LLM routing configuration
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

The tests cover declarative graph validation, Capability Registry dispatch, Project isolation, persistent Conversation Agent identity, immutable Workflow revisions, shared AgentCore tool loops, deterministic and ephemeral-agent Columns, explicit terminal paths, retry evidence, lease recovery, SQLite indexes, Artifact boundaries, native provider tool-call normalization, API behavior, and the read-only Web governance boundary.

Real-provider and three-project black-box acceptance evidence is kept outside the repository in `D:\workspace\codex-devwerk-project-files` so generated products do not pollute source control.

## Review Rule

A change is not reviewable when it reintroduces an API, module, test, configuration surface, or document that belongs to a superseded design. Git history is the archive; the active tree is the current product contract.
