# DevWerk Version 1 Test Contract

> Archived v0.1.0 design record (2026-10-16). Status and authority claims below describe its historical version only.

**Status**: active pre-release review gate<br>
**Derived from**: [`generic-conversation-agent-and-declarative-column-runtime.md`](generic-conversation-agent-and-declarative-column-runtime.md) and [`conversation-session-gateway-v1.md`](conversation-session-gateway-v1.md), with product intent retained in the Conversation Agent and Kanban design records

## Purpose

The complete `tests` directory protects the current Version 1 architecture. It is intentionally not a compatibility suite. A test must map to a current design invariant, a mounted API, an active provider boundary, or the Web governance contract.

## Test Layers

The [test-case map](../../../../tests/README.md) groups all 25 modules and 538 collected cases by behavior. The principal boundaries are:

| Scenario | Protected contract |
| --- | --- |
| Conversation turns, scope and supervision | Durable Project Conversation Session, tool and intent receipts, scope changes, visible failures, and supervision |
| Workflow definition and execution | Immutable revisions and plans, directed transitions, Column completion, explicit rework, recovery, and Task admission |
| Worker lifecycle, context and memory | Persistent Worker identity, assignment ownership, fenced recovery, context projection, checkpoints, and scoped memory |
| Tools, provider and Web API | Capability Registry, MCP bridge, file and command boundaries, provider normalization, and mounted UI/API |
| Settings, persistence, release and evaluation | Global settings and logs, Store invariants, repository assets, and isolated evaluation controls |

## Required Gate

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

The command must complete with zero failures. `skip`, `xfail`, and tests importing removed modules are not accepted as a clean result.

The Runtime P0 design and incident replay are recorded in [`DEVWERK_P0_Runtime_Fix_2026-09-08.md`](../../2026-09-08/DEVWERK_P0_Runtime_Fix_2026-09-08.md). Agent/command/recovery limits are Runtime liveness controls; workflow revisions do not carry a second independent execution-budget protocol.

## External Acceptance

Real LLM and generated-project acceptance is intentionally outside the deterministic unit/integration gate because it spends quota and depends on external runtimes. Its evidence must be stored under `D:\workspace\codex-devwerk-project-files`, while the deterministic contracts for provider parsing, file boundaries, Runtime state, and API behavior remain in this repository.

Every Workflow test and audit must also satisfy the [Column Agent lifecycle and interaction gate](../../2026-09-16/column-agent-lifecycle-interaction-test-gate-2026-09-16.md). In a complex-task test, verify actual Agent/Session continuity, feedback delivery across Columns, targeted rework, repeated verification, and successful implementation build/test evidence before reporting success. A missing or broken interaction/rework/implementation gate is P0; a test that did not exercise the required cycles is incomplete, not passing.

## Change Rule

09-21 contract revision: [repair/evidence/token design](../../2026-09-21/DEVWERK_Repair_Evidence_Token_Fix_2026-09-21.md) defines Mailbox as passive notifications. The Conversation and Runtime scenario modules assert notification handling and task-control boundaries. `test_p0_assignment_regressions.py` covers explicit Worker input, logical identity, and archived context; `test_repair_notification_contract.py` covers cross-Column repair cycles; `test_task_entry_admission.py` covers plan compilation and entry receipts; `test_completion_protocol.py` covers completion evidence and the selected Workflow edge.

Before changing a contract:

1. update the authoritative design document;
2. update this mapping;
3. update implementation and tests together;
4. run the entire test directory;
5. do not add a compatibility branch for an unshipped historical design.
