# DevWerk V1 Document Map

## v0.1.0 normative extension

- [`memory-and-workcell-runtime-v0.1.0.md`](memory-and-workcell-runtime-v0.1.0.md): File-first pluggable semantic Memory, context manifests, persistent Participant Sessions, and generic declarative Workcell execution. It supersedes earlier statements that Memory is postponed or every Agent Column is necessarily a one-Attempt ephemeral instance.

## Architecture authority

These documents define the locked V1 product and runtime direction:

1. [`generic-conversation-agent-and-declarative-column-runtime.md`](generic-conversation-agent-and-declarative-column-runtime.md)
2. [`conversation-agent-design-v1.md`](conversation-agent-design-v1.md)
3. [`kanban-workflow-design-v1.md`](kanban-workflow-design-v1.md)
4. [`conversation-agent-orchestration-soul-p0-design.md`](conversation-agent-orchestration-soul-p0-design.md)

Code and secondary documentation must remain consistent with all four. For Loop, Workflow Plan, Workflow Revision, Task Plan, and Task ownership, the approved refinement below is authoritative.

## Implemented V1 extensions

- [`loop-runtime-v1.md`](loop-runtime-v1.md): filesystem Loop discovery, initial Workflow materialization, directed rework, logical Writer sessions, and the bundled novel/DevOps Loops.
- [`loop-task-plan-decoupling-v1.md`](loop-task-plan-decoupling-v1.md): separates reusable Workflow Plans from objective-specific Task Plans and defines Task materialization.
- [`kanban-recovering-runtime-v1.md`](kanban-recovering-runtime-v1.md): same-Task recovery from structured temporary provider failures.
- [`agent-tool-rejection-recovery-v1.md`](agent-tool-rejection-recovery-v1.md): distinction between rejection before effect and a failed execution effect.
- [`conversation-session-gateway-v1.md`](conversation-session-gateway-v1.md): persistent per-Project Conversation Sessions, Hermes-style background Turn execution, transcript continuity, and failure isolation.
- [`global-settings-v1.md`](global-settings-v1.md): validated YAML global settings and the default startup pause contract for unfinished Tasks.

## Verification

- [`v1-test-contract.md`](v1-test-contract.md): maps the current test suite to the V1 contracts.

Real-provider smoke-test products and audit evidence remain outside source control under `D:\workspace\codex-devwerk-project-files` and `D:\workspace\codex-notes`.
