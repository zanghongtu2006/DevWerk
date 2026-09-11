# DevWerk Single-Agent Column Runtime — v0.1.0

## Decision

A Workflow Column is the smallest observable execution and context boundary. It may execute:

- one logical Agent; or
- one deterministic capability sequence without an Agent.

A Column cannot contain multiple Agents, an inner state machine, hidden participant routing, or a nested collaboration graph. Collaboration is represented only by the outer Workflow graph.

## Why

Putting several Agents inside one Column duplicates the Workflow scheduler and hides meaningful work from Kanban. It also lets context, retries, evidence, and accepted states accumulate inside a second graph that users cannot supervise. DevWerk therefore keeps one state machine: Task movement between Workflow Columns.

## Generic model

DevWerk core does not define business roles such as Writer, Reviewer, frontend, backend, or QA. A Loop gives each Column its domain-specific instruction and capabilities. Runtime knows only:

- Project, Workflow, Column, Task, Run, Attempt, Agent Run, Artifact, Memory, Session, Transition, and terminal state;
- `agent` and `capability_sequence` executors;
- declared outcomes and explicit directed transitions.

For example, a Loop may express `produce -> inspect -> produce` or `implement_a -> implement_b -> verify -> implement_a`. These are ordinary Columns, not special role types.

## Session continuity

An Agent Column is ephemeral by default. A Loop may set `metadata.agent_session_key` when revisiting that Column must resume the same logical Agent session. Session history is checkpointed and bounded; the Workflow transition carries the observable result and Artifact evidence. A review Column remains an independent session unless its Loop explicitly declares otherwise.

## Evidence and observability

Each Column visit creates a Column Run and, for an Agent executor, an Agent Run. The Agent completes through `column.complete`; Runtime validates the declared outcome, capability evidence, output contract, and transition before advancing the Task. Kanban therefore shows every production, review, verification, and rework step directly.

## Invariants

1. Every non-terminal Column has exactly one executor.
2. An Agent executor represents at most one logical Agent.
3. Multi-Agent work is split into multiple Columns.
4. Rework uses explicit Workflow edges.
5. Runtime contains no domain-role enum or domain-specific routing branch.
6. New SQLite schemas, APIs, and UI projections contain no nested Workcell model.
7. Legacy `workcell` executor payloads are rejected during Workflow validation.

## Migration

- Novel production uses `foundation -> recap -> authoring -> review -> deliver`; review may return to authoring, recap, or foundation.
- GitLab delivery uses `design -> development -> verification -> review -> gitlab -> accept`; verification, review, CI, and acceptance may return to development.
- DDD software delivery uses separate backend-development, frontend-development, integration-review, system-test, delivery, and acceptance Columns.

The removed Workcell tables and endpoints were implementation details of an unreleased design and are not retained as an alternate execution path.
