# DevWerk V1 Document Map

## Architecture authority

These documents define the locked V1 product and runtime direction:

1. [`generic-conversation-agent-and-declarative-column-runtime.md`](generic-conversation-agent-and-declarative-column-runtime.md)
2. [`conversation-agent-design-v1.md`](conversation-agent-design-v1.md)
3. [`kanban-workflow-design-v1.md`](kanban-workflow-design-v1.md)
4. [`conversation-agent-orchestration-soul-p0-design.md`](conversation-agent-orchestration-soul-p0-design.md)

Code and secondary documentation must remain consistent with all four.

## Implemented V1 extensions

- [`workflow-template-runtime-v1.md`](workflow-template-runtime-v1.md): metadata-driven template selection, immutable materialization, directed rework, logical Writer sessions, and the bundled novel/DevOps templates.
- [`kanban-recovering-runtime-v1.md`](kanban-recovering-runtime-v1.md): same-Task recovery from structured temporary provider failures.
- [`agent-tool-rejection-recovery-v1.md`](agent-tool-rejection-recovery-v1.md): distinction between rejection before effect and a failed execution effect.

## Verification

- [`v1-test-contract.md`](v1-test-contract.md): maps the current test suite to the V1 contracts.

Real-provider smoke-test products and audit evidence remain outside source control under `D:\workspace\codex-devwerk-project-files` and `D:\workspace\codex-notes`.
