# DevWerk Version 1 Test Contract

**Status**: active pre-release review gate  
**Derived from**: [`generic-conversation-agent-and-declarative-column-runtime.md`](generic-conversation-agent-and-declarative-column-runtime.md), with product intent retained in the Conversation Agent and Kanban design records

## Purpose

The complete `tests` directory protects the current Version 1 architecture. It is intentionally not a compatibility suite. A test must map to a current design invariant, a mounted API, an active provider boundary, or the Web governance contract.

## Test Layers

| Test module | Protected contract |
| --- | --- |
| `test_domain_contract.py` | unified statuses, explicit terminals, deterministic transition graph, required Project path |
| `test_capability_contract.py` | Registry dispatch, JSON Schema boundaries, explicit JSON references, conversation-published Workflow data |
| `test_store_contract.py` | Project isolation, stable conversation messages, one logical Conversation Agent, immutable revisions, Task pinning, WAL/indexes, cursor events |
| `test_files_contract.py` | canonical Project boundary, atomic write, hash/size metadata, bounded context reads |
| `test_runtime_contract.py` | capability-sequence and shared-AgentCore Columns, logical Agent sessions, explicit done/failed, directed rework, recovering, and rejected-before-effect tool handling |
| `test_conversation_contract.py` | persistent general Agent tool loop, platform policy preload, Loop selection/application, conversation-published Workflow revisions/Tasks, and automatic supervision |
| `test_api_web_contract.py` | mounted API, Project isolation, system automation path, Web routes/modules, read-only Kanban governance |
| `test_provider_contract.py` | native OpenAI/Anthropic tool-call normalization, routing, usage attribution, retryable/non-retryable errors |
| `test_loop_contract.py` | filesystem metadata discovery, schema-bound materialization, initial-Workflow admission, novel directed graph, DevOps requirement gate |
| `test_orchestration_policy_contract.py` | centralized scheduling policy, absence of Agent execution budgets, versioned policy evidence |
| `test_failure_transparency_contract.py` | original failure propagation, structured failure summaries, no silent fallback |
| `test_logging_contract.py` | full V1 debug trace, fixed `devwerk.log` name, daily rotation, no queue wrapper |

## Required Gate

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

The command must complete with zero failures. `skip`, `xfail`, and tests importing removed modules are not accepted as a clean result.

## External Acceptance

Real LLM and generated-project acceptance is intentionally outside the deterministic unit/integration gate because it spends quota and depends on external runtimes. Its evidence must be stored under `D:\workspace\codex-devwerk-project-files`, while the deterministic contracts for provider parsing, file boundaries, Runtime state, and API behavior remain in this repository.

## Change Rule

Before changing a contract:

1. update the authoritative design document;
2. update this mapping;
3. update implementation and tests together;
4. run the entire test directory;
5. do not add a compatibility branch for an unshipped historical design.
