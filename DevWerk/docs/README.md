# DevWerk V1 Document Map

## P0 runtime correction

- [`DEVWERK_Scope_Extension_Implementation_2026-09-10.md`](DEVWERK_Scope_Extension_Implementation_2026-09-10.md): implemented same-project scope revision, sole Conversation Agent, persistent Worker continuation, verified reply reports, and read-only source database replay; hard character-limit repair is deferred.
- [`DEVWERK_Novel_Combined_Remediation_Plan_2026-09-10.md`](DEVWERK_Novel_Combined_Remediation_Plan_2026-09-10.md): scope-extension A–E implementation and deferred hard character-limit F, with separate verification boundaries.
- [`bugs/2026-09-10-conversation-scope-extension-review-and-plan.md`](bugs/2026-09-10-conversation-scope-extension-review-and-plan.md): real-project continuation failure, single Conversation Agent identity, scope revision and fact-based reporting repair proposal; awaiting review.
- [`DEVWERK_P0_Runtime_Implementation_2026-09-10.md`](DEVWERK_P0_Runtime_Implementation_2026-09-10.md): R1–R9 implementation, fresh regression evidence, persistent Main/Worker lifecycle, migration and remaining verification boundaries.
- [`DEVWERK_P0_Runtime_Bug_Register_2026-09-09.md`](DEVWERK_P0_Runtime_Bug_Register_2026-09-09.md): original fault evidence and per-item remediation status.
- [`DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md`](DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md): reviewed Hermes architecture plan and release acceptance checklist.
- [`mailbox-lifecycle-p0-design.md`](mailbox-lifecycle-p0-design.md): durable Mailbox message/delivery lifecycle, single automatic delivery, explicit redelivery, and the Scheduler/Conversation boundary.
- [`agent-core-completion-evolution-v0.1.0.md`](agent-core-completion-evolution-v0.1.0.md): approved staged separation of Agent protocol, completion admission, execution evidence, and Workflow transition responsibilities.
- [`conversation-agent-generic-tool-loop-v0.1.0.md`](conversation-agent-generic-tool-loop-v0.1.0.md): removes business-language parsing and forced capability sequences from the Conversation kernel while retaining durable receipts and generic no-progress detection.

## v0.1.0 normative extension

- [`single-agent-column-runtime-v0.1.0.md`](single-agent-column-runtime-v0.1.0.md): File-first Memory and the single-Agent Column invariant; collaboration and rework live in the observable Workflow graph.

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
