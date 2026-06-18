# Workflow Runtime

DevWerk is a backend-owned coding workflow. A client supplies project facts and
tools; it does not decide kanban transitions.

## Runtime Records

- `kb_conversations`: one durable conversation per task, including active
  column, pause reason, rolling summary, and token estimate.
- `kb_messages`: ordered user, assistant, plan, tool, and status messages.
- `kb_column_runs`: one checkpointed execution for each column/agent attempt.
- `kb_revisions`: candidate file/patch operations before client apply.
- `kb_artifacts` and `kb_events`: stable phase outputs and complete audit trail.

Filesystem JSONL is an audit mirror only. It is not used to resume work.

## Interaction

1. `POST /v1/workflows` creates a task and conversation.
2. Context and planner columns run.
3. With `interaction_mode=confirm_plan`, the workflow pauses in `Planned` and
   returns `waiting_for=plan_confirmation`.
4. `POST /v1/workflows/{task_id}/messages` confirms or revises the plan.
5. Coder creates a candidate revision. Reviewer performs protocol checks and a
   model-backed semantic review.
6. The client snapshots and applies the revision, runs requested tools, and
   reports `apply_result`.
7. Failed verification returns evidence to Coding; successful verification
   advances to Done.

The IntelliJ client stores the complete interaction log at
`.devwerk/tasks/<task_id>/operation.log`. Each actual write receives a separate
`.devwerk/tasks/<task_id>/snapshots/<timestamp-uuid>/before|after` pair, so
conversation turns are grouped without allowing later recoding rounds to
overwrite an earlier safety snapshot.

Old messages are compacted into a rolling summary when the project context
budget is exceeded. Recent turns remain verbatim. Project memory receives only
compact reusable engineering facts, never the raw transcript.

## Agent And Column Mapping

- `context_indexed` -> local context agent: source-map summary, workspace facts,
  project memory, and client capabilities; no model call is required.
- `planned` -> planner: transcript summary + recent turns + context artifacts;
  it may use backend read/search tools and emits candidate/required file intent.
- `coding` -> coder: confirmed plan + latest revision/verification feedback +
  recent transcript; it emits file operations and client tool requests.
- `reviewed` -> reviewer: plan + candidate revision + workspace summary; protocol
  checks enforce path safety, then the configured reviewer model performs
  semantic review. Backend research evidence used by the coder is retained in
  the revision context. Missing compile/test evidence alone is not a recoding
  reason: the reviewer approves snapshot-protected apply and emits
  capability-bounded `verification_tool_requests` selected from project facts.
- `verified` is driven by client tool evidence after snapshot-protected apply.

Columns and agents remain independent concepts. `default.json` or a project DB
override determines column order, actions, artifacts, and agent binding. The
runtime dispatches by configured agent role and the custom-workflow regression
test ensures this remains configurable.

## Client Capability Direction

The current IntelliJ plugin declares generic capabilities (`read_file`,
`search`, `apply_ops`, `apply_patch`, `create_snapshot`, `ide_syntax_check`,
`run_command`). This is the migration boundary toward an MCP-style client:
backend agents select tools by capability, while IntelliJ, VS Code, CLI, or a
remote workspace adapter may implement them independently.

There is no Java package, Maven, Gradle, Spring, `src`, or `test` path policy in
the workflow engine. Project structure comes from source maps and tool evidence.
