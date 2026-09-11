# Agent Core Completion Evolution v0.1.0

## Purpose

This document records the approved incremental migration that separates Provider/tool protocol handling from Workflow completion admission and execution evidence. The migration applies to every DevWerk Loop and must not contain product-specific rules.

## Current problem

`AgentCore.run()` currently assembles prompts, calls the Provider, executes capabilities, persists audit records, controls Conversation corrections, parses Column completion, decides whether failed operations block completion, and returns Workflow signals. These responsibilities have different authorities and currently interact through boolean fields such as `completion_requires_evidence` and `completion_auto_evidence`.

The completion boundary must distinguish three questions:

1. **Protocol validity:** did the model submit a structurally valid tool batch and completion declaration?
2. **Completion admission:** do the declared outcome and execution evidence justify advancing, reworking, or failing the Workflow?
3. **State transition:** which declared Workflow edge is taken after admission?

## Authority boundaries

### Agent protocol

The Agent execution layer owns Provider messages, tool-call pairing, server-side argument validation, batch ordering, declared outcome validation, and deterministic no-progress detection. It does not infer business success or decide whether an execution failure may be ignored.

### Completion admission

The completion admission layer owns evidence requirements, evidence collection mode, unresolved execution failures, explicit failure resolution, and whether a submitted outcome can advance or rework the Workflow.

### Workflow Runtime

Runtime constructs the completion contract from the declared Column, consumes an admitted completion, and performs the declared state transition. Runtime does not reinterpret Provider prose.

### Execution audit

The audit layer owns immutable invocation evidence, hashes, entity references, and explicit failed/resolved/superseded relationships. Audit identities are not themselves repair semantics.

## Explicit completion model

A completion contract must state, rather than infer from strings such as `success` or `done`:

- completion tool name;
- allowed outcomes and their transition targets;
- evidence collection mode: model or runtime;
- evidence requirement per outcome;
- whether an outcome is allowed to carry unresolved execution failures;
- output schema.

A forward/success outcome may not hide an unresolved executed failure. A declared failure or rework outcome may carry failed evidence when its transition explicitly allows it. A different successful operation resolves an earlier failure only through an explicit, auditable resolution link; an operation hash is an identity, not proof of semantic repair.

## Incremental delivery order

### Stage 1 — behavior guards

Before moving code:

- reject repeated identical completion submissions when the rejection reason and execution ledger have not changed;
- validate completion and await arguments on the server;
- allow declared failure/rework outcomes to carry failed execution evidence;
- prevent unrelated successful evidence from hiding an unresolved failure;
- require evidence according to explicit transition semantics, including custom intermediate outcomes;
- add characterization and regression tests for each rule.

Acceptance: no repeated completion retry can consume unbounded Provider calls without new execution progress, and neither success nor failure relies on outcome-name heuristics.

### Stage 2 — behavior-preserving extraction

Extract small public modules while keeping `AgentCore.run()` as the compatible facade:

- completion submission parsing;
- tool-batch validation;
- execution ledger construction/query;
- session-history replay.

Acceptance: existing Runtime and Conversation callers remain compatible and tests show no behavior change caused solely by extraction.

### Stage 3 — explicit Completion Contract

Replace `completion_targets`, `completion_requires_evidence`, and `completion_auto_evidence` combinations with an explicit contract supplied by Runtime. Introduce a completion admission service that returns a typed acceptance or rejection result.

Acceptance: Agent code does not guess success from outcome or target names, and evidence source does not alter failure policy.

### Stage 4 — runner decomposition

Decompose the long execution loop into named phases:

- prepare run;
- assemble prompt/session context;
- request and normalize Provider response;
- validate and execute a tool batch;
- evaluate completion or await;
- finish the Agent Run.

`AgentCore.run()` remains as the stable entry point until all callers migrate.

Acceptance: each phase has focused tests, and Workflow state transitions remain exclusively in Runtime.

## Implemented module map

- `agent.py`: stable `AgentCore` compatibility facade.
- `agent_runner.py`: Agent Run lifecycle orchestration and explicit finish paths.
- `agent_run_preparation.py`: capability resolution, Run creation, prompt and Session assembly.
- `agent_provider.py`: Provider request tracing and response normalization.
- `agent_tool_execution.py`: tool-batch validation, capability dispatch, completion/await submission handling, and audit persistence.
- `completion_protocol.py`: typed completion contracts, submissions, schemas, and no-progress guard.
- `services/completion_admission.py`: evidence and unresolved-failure admission decisions.
- `execution_ledger.py`: immutable execution evidence construction and queries.
- `session_replay.py` and `tool_protocol.py`: focused replay and tool-protocol rules.

Runtime remains the only owner of Workflow transition selection. Loop transitions may explicitly override evidence requirements and whether a rework/failure edge can carry unresolved execution failures.

## Non-goals

- No database migration is required merely to split modules.
- No model-iteration budget is introduced as a substitute for protocol progress detection.
- No novel, software-development, or other Loop-specific outcome names are embedded in Agent Core.
- No failure is removed from the audit trail when it becomes resolved or superseded.
