# DevWerk Memory & Context Orchestration PRD

Version: 0.1 implementation draft  
Source: `docs/memory-management-prd-original.md`  
Target: DevWerk backend-owned Kanban workflow engine with dynamically spawned agents

## 1. Product Intent

DevWerk already has the core runtime shape:

```text
Project -> Kanban -> Task -> Workflow -> spawned agent run
```

The current memory implementation is useful but too coarse. It keeps compact
project memory and workflow phase outputs, but it does not yet define a stable
contract for:

- what a spawned agent is allowed to see,
- what a spawned agent must write back,
- how task-level memory survives interruption,
- how code-context evidence is passed between workflow nodes,
- how stable task conclusions become project memory candidates.

This PRD turns memory into an orchestration subsystem:

```text
Memory System =
  scoped memory repository
  + context compiler
  + agent run trace
  + writeback contract
  + promotion candidate workflow
```

The goal is not to inject more history into prompts. The goal is to provide the
smallest relevant, auditable context pack for each workflow node, then preserve
the node output as structured memory.

## 2. Scope

### Required MVP

- Scoped memory items:
  - `workspace`
  - `project`
  - `workflow`
  - `task`
  - `session`
  - `run`
- Project memory categories:
  - `project_profile`
  - `project_rules`
  - `architecture_summary`
  - `source_map`
  - `code_summary`
  - `test_strategy`
- Task memory categories:
  - `task_brief`
  - `task_constraints`
  - `task_plan`
  - `task_analysis_summary`
  - `task_code_context`
  - `task_decisions`
  - `task_handoff_summary`
  - `task_final_summary`
  - `promotion_candidates`
- Context compiler:
  - input: `project_id`, `task_id`, `workflow_id`, `agent_role`, `stage`, `token_budget`
  - output: persisted `context_pack` with included memory references and trim notes
- Agent run records:
  - `run_id`
  - `agent_role`
  - `stage`
  - `input_context_pack_id`
  - `tool_results`
  - `observations`
  - `output`
  - `writeback_payload`
  - `status`
- Writeback contract:
  - `task_progress_update`
  - `analysis_summary`
  - `code_context_update`
  - `decisions`
  - `handoff_summary`
  - `patch_summary`
  - `test_state`
  - `final_summary`
  - `promotion_candidates`
- Promotion candidate lifecycle:
  - created by writeback,
  - stored as candidate by default,
  - approved/rejected through backend API,
  - approved candidates become project memory items.

### Out of Scope for This MVP

- Vector search or graph memory.
- Cross-project experience memory.
- Fully automatic project memory promotion without review.
- Replacing source-map construction in capability providers.
- UI final polish beyond exposing inspectable backend data.

## 3. Memory Principles

1. A memory item must have exactly one explicit scope.
2. Spawned agents do not read raw memory stores directly.
3. Spawned agents receive only a context pack compiled by the backend.
4. Full task session and full source files are on-demand tool material, not default context.
5. Task memory overrides project memory for the active task.
6. Current user input overrides historical memory.
7. Project memory writes are conservative: workflow agents submit promotion candidates; backend policy approves or rejects.
8. Every context pack and writeback is auditable from events/artifacts/API.

## 4. Context Loading Strategy

### Always Load

Small and stable data included in every context pack:

- project profile,
- project rules,
- task brief,
- task constraints,
- workflow current stage,
- agent role instruction.

### Retrieve Load

Compact data selected by role/stage/task:

- source map summary,
- code summary,
- task decisions,
- recent handoff summaries,
- task analysis summary,
- task code context,
- test strategy.

### On-demand Load

Never injected by default:

- full source files,
- full task sessions,
- full historical run traces,
- full test logs,
- uploaded documents.

## 5. Role-Based Context Matrix

DevWerk does not hardcode agent identities such as planner/coder/reviewer as a
fixed workflow. A project workflow can define any node. The context compiler
uses role hints to decide memory priority:

| Role Hint | Always Load | Retrieve Load | On-demand |
|---|---|---|---|
| `planner`, `architect`, `designer` | project profile/rules, task brief/constraints | historical decisions, session summary | full session |
| `analyzer`, `context`, `diagnostic` | task brief/constraints/plan, source-map summary | code summaries, related docs | source files |
| `implementer`, `coder`, `writer` | task brief/constraints, decisions, code context | recent handoff | source files |
| `reviewer`, `qa` | task brief/constraints, patch summary | project rules, test strategy | changed file contents |
| `tester`, `verifier` | acceptance criteria, changed files | test strategy | test logs |
| `documenter`, `summarizer` | final diff summary, decisions, test state | promotion candidates | full task memory |

## 6. Task Code Context

`task_code_context` is the task-level battlefield map for coding work. It is
derived from source-map and code-summary evidence when available, but it remains
language and IDE neutral.

Required structure:

```json
{
  "related_modules": [],
  "related_files": [],
  "files_to_change": [],
  "files_to_avoid": [],
  "current_behavior": "",
  "possible_change": "",
  "risk_notes": []
}
```

The source map is project-level navigation. It should not be dumped into every
agent context. Analyzer-like nodes use it to produce `task_code_context`; later
nodes consume that compact task memory.

## 7. Writeback Contract

Workflow agents return a phase output. DevWerk additionally accepts a structured
writeback payload:

```json
{
  "task_updates": {
    "progress": {},
    "analysis_summary": {},
    "code_context": {},
    "decisions": [],
    "handoff_summary": {},
    "patch_summary": {},
    "test_state": {},
    "final_summary": {}
  },
  "workflow_updates": {},
  "run_updates": {
    "observations": [],
    "tool_results": []
  },
  "project_memory_candidates": []
}
```

The backend memory writer validates this payload and writes only to allowed
scopes. Agents never write project memory directly.

## 8. Promotion Candidate Contract

Allowed target memory types:

- `project_rule`
- `architecture_summary`
- `source_map`
- `code_summary`
- `test_strategy`
- `known_issue`
- `api_contract`
- `dependency_map`

Candidate structure:

```json
{
  "candidate_id": "generated-by-backend",
  "task_id": "task-id",
  "target_memory_type": "project_rule",
  "content": {},
  "reason": "",
  "confidence": 0.0,
  "status": "candidate"
}
```

Rejected by default:

- one-off task goals,
- temporary user preferences,
- unverified guesses,
- tool error text,
- file lists only valid for one task.

## 9. API Surface

MVP APIs use existing `/v1` conventions:

- `GET /v1/kanban/tasks/{task_id}/memory`
- `POST /v1/memory/projects/{project_id}/tasks/{task_id}/items`
- `POST /v1/memory/projects/{project_id}/tasks/{task_id}/context`
- `POST /v1/memory/projects/{project_id}/tasks/{task_id}/runs`
- `POST /v1/memory/runs/{run_id}/writeback`
- `GET /v1/memory/projects/{project_id}/candidates`
- `POST /v1/memory/projects/{project_id}/candidates/{candidate_id}/approve`
- `POST /v1/memory/projects/{project_id}/candidates/{candidate_id}/reject`

## 10. Events

The backend must emit auditable events:

- `context_pack_created`
- `agent_run_started`
- `agent_run_finished`
- `writeback_received`
- `memory_item_created`
- `memory_item_updated`
- `promotion_candidate_created`
- `promotion_candidate_approved`
- `promotion_candidate_rejected`
- `context_pack_trimmed`
- `memory_conflict_detected`

## 11. Acceptance Tests

MVP completion requires tests proving:

- scoped memory CRUD rejects invalid scopes/types;
- context compiler persists a context pack and includes project/task memory with token budget trim metadata;
- analyzer writeback stores `task_analysis_summary`, `task_code_context`, and `task_handoff_summary`;
- implementer context pack includes the latest handoff and task code context without injecting full session;
- task final writeback creates promotion candidates but does not write project memory automatically;
- approving a candidate writes a project memory item;
- workflow engine context uses the compiler output so spawned agents receive project memory, task memory, and compact session context.

