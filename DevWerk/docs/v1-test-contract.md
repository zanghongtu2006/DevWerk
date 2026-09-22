# DevWerk Version 1 Test Contract

**Status**: active pre-release review gate<br>
**Derived from**: [`generic-conversation-agent-and-declarative-column-runtime.md`](generic-conversation-agent-and-declarative-column-runtime.md) and [`conversation-session-gateway-v1.md`](conversation-session-gateway-v1.md), with product intent retained in the Conversation Agent and Kanban design records

## Purpose

The complete `tests` directory protects the current Version 1 architecture. It is intentionally not a compatibility suite. A test must map to a current design invariant, a mounted API, an active provider boundary, or the Web governance contract.

## Test Layers

| Test module | Protected contract |
| --- | --- |
| `test_domain_contract.py` | unified statuses, explicit terminals, deterministic transition graph, required Project path |
| `test_capability_contract.py` | Registry dispatch, JSON Schema boundaries, explicit JSON references, conversation-published Workflow data |
| `test_store_contract.py` | Project isolation, stable conversation messages, one logical Conversation Agent, immutable Workflow/Task plans and revisions, Task pinning, WAL/indexes, cursor events |
| `test_files_contract.py` | canonical Project boundary, atomic write, hash/size metadata, bounded context reads |
| `test_runtime_contract.py` | capability-sequence and shared-AgentCore Columns, logical Agent sessions, explicit done/failed, directed rework, recovering, and rejected-before-effect tool handling |
| `test_conversation_contract.py` | persistent per-Project Conversation Session, transcript/tool-evidence replay across Gateway restarts, failed-Turn isolation, general Agent tool loop, platform policy preload, Loop selection/application without implicit Tasks, conversation-published Task Plans/Tasks, and automatic supervision |
| `test_api_web_contract.py` | mounted API, Project isolation, system automation path, Web routes/modules, read-only Kanban governance |
| `test_provider_contract.py` | native OpenAI/Anthropic tool-call normalization, routing, usage attribution, retryable/non-retryable errors |
| `test_loop_contract.py` | filesystem metadata discovery, schema-bound materialization, initial-Workflow admission, novel directed graph, DevOps requirement gate |
| `test_orchestration_policy_contract.py` | centralized scheduling policy, versioned policy evidence, explicit external Await outcomes |
| `test_p0_runtime_regressions.py` | runtime-owned completion evidence, execution fencing, bounded Agent/command/recovery loops, durable Await recovery, effect replay, immutable artifact versions, atomic Loop snapshots, successor dependency consistency |
| `test_failure_transparency_contract.py` | original failure propagation, structured failure summaries, no silent fallback |
| `test_logging_contract.py` | full V1 debug trace, fixed `devwerk.log` name, daily rotation, no queue wrapper |
| `test_memory_workcell_contract.py` | File-first replaceable Memory providers, scoped retrieval, supersession, and pre-Memory workspace initialization |

## Required Gate

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

The command must complete with zero failures. `skip`, `xfail`, and tests importing removed modules are not accepted as a clean result.

The Runtime P0 design and incident replay are recorded in [`DEVWERK_P0_Runtime_Fix_2026-09-08.md`](DEVWERK_P0_Runtime_Fix_2026-09-08.md). Agent/command/recovery limits are Runtime liveness controls; workflow revisions do not carry a second independent execution-budget protocol.

## External Acceptance

Real LLM and generated-project acceptance is intentionally outside the deterministic unit/integration gate because it spends quota and depends on external runtimes. Its evidence must be stored under `D:\workspace\codex-devwerk-project-files`, while the deterministic contracts for provider parsing, file boundaries, Runtime state, and API behavior remain in this repository.

Every Workflow test and audit must also satisfy the [Column Agent lifecycle and interaction gate](column-agent-lifecycle-interaction-test-gate-2026-09-16.md). In a complex-task test, verify actual Agent/Session continuity, feedback delivery across Columns, targeted rework, repeated verification, and successful implementation build/test evidence before reporting success. A missing or broken interaction/rework/implementation gate is P0; a test that did not exercise the required cycles is incomplete, not passing.

## Change Rule

09-21 contract revision: [repair/evidence/token design](DEVWERK_Repair_Evidence_Token_Fix_2026-09-21.md) defines Mailbox as passive notifications. `test_conversation_contract.py` and `test_p0_runtime_regressions.py` must assert zero automatic model calls and no event-authorized task control. Notification acknowledgement cannot resolve a failed Job. `test_persistent_agents.py` and `test_p0_assignment_regressions.py` verify explicit Worker input in its own store, separate from Mailbox. `test_agent_recovery_and_fencing.py` verifies archived Worker context remains retrievable while native replay is scoped to the current Assignment.

`test_repair_notification_contract.py` covers two real-file/real-subprocess cross-Column repair cycles, Runtime-only terminal evidence, explicit corrected-plan successors and atomic dependency materialization. `test_task_entry_admission.py` covers instantiated software behavioral checks and accepted baseline receipts. `test_completion_protocol.py` rejects unrelated resolution evidence while accepting an actual identical retry already resolved by Runtime. `scripts/eval_worker_repair.py` exercises two real-provider feedback/repair cycles in an isolated evidence directory; it does not claim complete web/software product acceptance.

Before changing a contract:

1. update the authoritative design document;
2. update this mapping;
3. update implementation and tests together;
4. run the entire test directory;
5. do not add a compatibility branch for an unshipped historical design.
