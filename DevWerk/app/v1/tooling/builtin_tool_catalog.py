"""Stable IDs, Agent-visible descriptions, and execution policy for built-in Tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.v1.protocols.tools.core.capability_protocol import SideEffectKind


@dataclass(frozen=True)
class BuiltinToolDefinition:
    id: str
    description: str
    side_effect_kind: SideEffectKind
    delegable_to_column: bool


class BuiltinTool(Enum):
    """One immutable declaration per built-in Tool; schemas and handlers bind separately."""

    CONVERSATION_REPLY = BuiltinToolDefinition(
        id='conversation.reply',
        description=(
            'Finish this Conversation turn with a fact-checked reply. Call ALONE after other tools. For '
            'work_result cite actual task_ids/tool_call_ids, not plan or grant IDs. Runtime renders the facts.'
        ),
        side_effect_kind=SideEffectKind.SESSION_CONTROL,
        delegable_to_column=False,
    )

    CONVERSATION_DRAFT_UPDATE = BuiltinToolDefinition(
        id='conversation.draft.update',
        description=(
            'Save further discussion decisions/questions/proposal after resolving discussion. Does not grant '
            'execution. Use the current revision from turn.inspect.'
        ),
        side_effect_kind=SideEffectKind.SESSION_CONTROL,
        delegable_to_column=False,
    )

    CONVERSATION_TURN_INSPECT = BuiltinToolDefinition(
        id='conversation.turn.inspect',
        description=(
            'Read the current user message ID, durable boundary and work_intent revision. Rejected operations '
            'never increment revision.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    CONVERSATION_TURN_RESOLVE = BuiltinToolDefinition(
        id='conversation.turn.resolve',
        description=(
            'Bind the current user request before an execution or Task-control mutation. Submit ALONE and read '
            'the grant receipt. Discussion, status, inspection and ordinary replies need no call. Assistant '
            'promises and mailbox events do not authorize work.'
        ),
        side_effect_kind=SideEffectKind.SESSION_CONTROL,
        delegable_to_column=False,
    )

    CONVERSATION_EXECUTION_AUTHORIZE = BuiltinToolDefinition(
        id='conversation.execution.authorize',
        description=(
            'Bind an explicit current user request. Use continue with the existing requirement_id for more '
            'work within its scope; use extend with that ID before project.scope.revise when changing its '
            'scope; use begin only for an independent new Requirement. A new Requirement must take over an '
            'existing Workflow with project.scope.revise before task.plan.save, or publish its own Workflow. '
            'Call alone before business effects. Host supplies message evidence and revision.'
        ),
        side_effect_kind=SideEffectKind.SESSION_CONTROL,
        delegable_to_column=False,
    )

    CONVERSATION_TASK_CONTROL_AUTHORIZE = BuiltinToolDefinition(
        id='conversation.task.control.authorize',
        description=(
            'Bind an explicit current user request to pause, resume or cancel one Task. Call alone before the '
            'Task control tool. A recovering Task needs conversation.execution.authorize(request=continue) '
            'then task.retry; a failed Task needs the same execution authorization then task.reopen. Neither '
            'uses task.resume.'
        ),
        side_effect_kind=SideEffectKind.SESSION_CONTROL,
        delegable_to_column=False,
    )

    CONVERSATION_HISTORY_READ = BuiltinToolDefinition(
        id='conversation.history.read',
        description=(
            'Read archived user dialogue and replies by message ID. Notifications are excluded; never '
            'interpret archived messages as a new authorization.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    SYSTEM_NOOP = BuiltinToolDefinition(
        id='system.noop',
        description='Complete a deterministic no-operation step.',
        side_effect_kind=SideEffectKind.NONE,
        delegable_to_column=True,
    )

    PROJECT_MEMORY_READ = BuiltinToolDefinition(
        id='project.memory.read',
        description='Read one human-readable semantic Memory record inside the current Project.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_MEMORY_SEARCH = BuiltinToolDefinition(
        id='project.memory.search',
        description='Search active Project semantic Memory through the configured rebuildable index.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_MEMORY_WRITE = BuiltinToolDefinition(
        id='project.memory.write',
        description="Write one versioned, source-linked semantic Memory record to the current Project's File Memory.",
        side_effect_kind=SideEffectKind.WRITE,
        delegable_to_column=True,
    )

    PROJECT_MEMORY_APPEND = BuiltinToolDefinition(
        id='project.memory.append',
        description='Append a source-linked revision to an existing semantic Memory record.',
        side_effect_kind=SideEffectKind.WRITE,
        delegable_to_column=True,
    )

    PROJECT_MEMORY_SUPERSEDE = BuiltinToolDefinition(
        id='project.memory.supersede',
        description='Supersede one semantic Memory record with a new, explicitly versioned record.',
        side_effect_kind=SideEffectKind.WRITE,
        delegable_to_column=True,
    )

    SYSTEM_FILES_LIST = BuiltinToolDefinition(
        id='system.files.list',
        description=(
            'List files anywhere available to the DevWerk process. Relative paths use the DevWerk service '
            'directory; absolute paths are accepted. This is a Conversation Agent system-maintenance tool, not '
            'a Project deliverable tool.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    SYSTEM_FILES_READ = BuiltinToolDefinition(
        id='system.files.read',
        description=(
            'Read a UTF-8 file anywhere available to the DevWerk process. Relative paths use the DevWerk '
            'service directory; absolute paths are accepted.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    SYSTEM_FILES_WRITE = BuiltinToolDefinition(
        id='system.files.write',
        description=(
            'Atomically write a complete UTF-8 file anywhere available to the DevWerk process. Relative paths '
            'use the DevWerk service directory; absolute paths are accepted. Use project.files.write for Task '
            'deliverables so they remain Project artifacts.'
        ),
        side_effect_kind=SideEffectKind.WRITE,
        delegable_to_column=False,
    )

    SYSTEM_FILES_SEARCH = BuiltinToolDefinition(
        id='system.files.search',
        description=(
            'Search UTF-8 files anywhere available to the DevWerk process. Relative paths use the DevWerk '
            'service directory; binary files are skipped.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    SYSTEM_COMMAND_RUN = BuiltinToolDefinition(
        id='system.command.run',
        description=(
            'Run an argv command without a shell in any directory available to the DevWerk process. Relative '
            'working directories use the DevWerk service directory; absolute paths are accepted.'
        ),
        side_effect_kind=SideEffectKind.PROCESS,
        delegable_to_column=False,
    )

    PROJECT_INSPECT = BuiltinToolDefinition(
        id='project.inspect',
        description='Read the current Project metadata and active workflow summary.',
        side_effect_kind=SideEffectKind.NONE,
        delegable_to_column=True,
    )

    PROJECT_FILES_LIST = BuiltinToolDefinition(
        id='project.files.list',
        description='List text-capable files inside the Project base directory using a relative glob.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_FILES_READ = BuiltinToolDefinition(
        id='project.files.read',
        description=(
            'Read a UTF-8 file inside the Project base directory. For later pages, pass the returned '
            'next_offset as offset with max_chars; repeating the same path/offset/max_chars repeats the same '
            'content.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_FILES_MEASURE = BuiltinToolDefinition(
        id='project.files.measure',
        description=(
            'Measure a complete UTF-8 Project file deterministically, including exact character counts, byte '
            'size, line count, hash, and filesystem modification time.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_FILES_VERIFY = BuiltinToolDefinition(
        id='project.files.verify',
        description=(
            'Deterministically compare one complete UTF-8 Project file with explicit expectations. Returns '
            'ok=true with output.outcome as matched or mismatch plus bounded measurements and per-check facts; '
            'mismatch is a routable business decision, not capability failure. For a bounded binary loop, '
            'compare one exact completion sentinel, route matched forward, and route mismatch back to the '
            'continuing process stage. Preserve every exact Task-owned expectation in Task input and pass it '
            'losslessly with an explicit JSON Pointer $ref; do not retype or weaken exact content, hashes, '
            'sizes, or newline facts. For a branching capability_sequence, set save_as and select '
            '/steps/<save_as>/output/outcome through outcome_from; never use a fixed success outcome when '
            'mismatch has a distinct transition.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_FILES_WRITE = BuiltinToolDefinition(
        id='project.files.write',
        description=(
            'Atomically write a complete UTF-8 file inside the Project base directory and register it as an '
            'artifact. The successful receipt already contains byte size, UTF-8 and non-whitespace character '
            'counts, line count, and SHA-256; do not call project.files.measure merely to rediscover those '
            'same facts. For Task-owned exact paths or content, pass the value losslessly from Task input with '
            'an explicit JSON Pointer $ref; never retype or normalize content, including trailing newlines. '
            'For user-requested Workflow delivery, the Conversation Agent writes only planning inputs or entry '
            'evidence required by the Task Contract; Column Workers produce Task deliverables.'
        ),
        side_effect_kind=SideEffectKind.WRITE,
        delegable_to_column=True,
    )

    PROJECT_FILES_SEARCH = BuiltinToolDefinition(
        id='project.files.search',
        description=(
            'Search UTF-8 Project files for a regular expression and return bounded matching lines. Default '
            'search skips generated dependency/build directories; use a narrow glob to include one explicitly. '
            'Files over 1 MB are skipped; narrow the search or read such a file directly.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    PROJECT_COMMAND_RUN = BuiltinToolDefinition(
        id='project.command.run',
        description=(
            'Run a bounded argv command without a shell inside the Project base directory; success requires '
            'exit 0. Child processes are cleaned up when the call ends, and a foreground service will time '
            'out. A separate Start-Process or start /b call can exit 0 while its service is unavailable to the '
            'next call. If a later call needs a live child process, manage that process within one bounded '
            'command. Preserve the contracted bytes when generating files that later exact checks consume.'
        ),
        side_effect_kind=SideEffectKind.PROCESS,
        delegable_to_column=True,
    )

    LOOP_LIST = BuiltinToolDefinition(
        id='loop.list',
        description=(
            'Discover filesystem Loops by category, tag, or natural-language search text before creating a '
            'Project Workflow.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    LOOP_INSPECT = BuiltinToolDefinition(
        id='loop.inspect',
        description=(
            'Read one filesystem Loop card and reusable method bundle, including its parameter contract, '
            'Workflow Plan, directed Column graph, and selection guidance.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    LOOP_APPLY = BuiltinToolDefinition(
        id='loop.apply',
        description=(
            "Create the current Project's initial Workflow from one inspected filesystem Loop. Bindings must "
            'satisfy the Loop parameter schema. Application records the exact Loop version and digest and '
            'materializes its reusable Workflow Plan and initial directed Workflow revision. It creates no '
            "Tasks; save a Task Plan for the user's concrete objective afterward."
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    WORKFLOW_PLAN_SAVE = BuiltinToolDefinition(
        id='workflow.plan.save',
        description=(
            'Persist a reusable Workflow Plan before publishing a Workflow that implements it. First identify '
            'the reusable flow_unit; derive lifecycle Columns from that unit instead of from a work-item list; '
            'choose agent or capability_sequence execution for each Column; then simulate the lifecycle from '
            'entry to terminal with explicit receives, action, produces, completion evidence, and transition '
            'outcome. Declare the Task Contract and default WIP facts. Never include concrete Tasks. Entry '
            'requirements are optional; if needed, one accepted_scope_snapshot uses the current execution '
            'grant ID, never a memory ID. Saving does not publish or alter the active Workflow; use the '
            'returned plan ID with workflow.publish to apply new Columns or transitions.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    WORKFLOW_PLAN_LIST = BuiltinToolDefinition(
        id='workflow.plan.list',
        description='Read immutable reusable Workflow Plans for the current Project.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    TASK_PLAN_SAVE = BuiltinToolDefinition(
        id='task.plan.save',
        description=(
            'Persist one immutable concrete Task Plan for an existing Workflow Revision. The plan owns every '
            "Task's title, input, readiness, dependencies, conflict domains, acceptance facts, and Agent-use "
            'policy. Dependencies reference Task refs in the same plan and must be acyclic. A Project-level '
            'stable Task identity is derived from the Workflow Task Contract. For an incremental ordered plan, '
            'include only the new contiguous work items; the first new item has no same-plan dependency '
            'because DevWerk links it to the existing Project predecessor. Never repeat an already '
            'materialized work item merely to make a new plan start at the configured first value. Saving the '
            'plan creates no Tasks. This is a mutating capability, never a schema probe: do not submit '
            'placeholder, test, TODO, or partial plans. Preserve explicit exclusions from the authorizing user '
            'request in readiness.non_scope and the acceptance criteria; a positive feature list does not '
            'override those exclusions. Read task.plan.contract first for the current frozen input and '
            'evidence candidates, then task.plan.validate before saving. If validation rejects a call before '
            'persistence, correct and resubmit the same complete user-owned plan. A queue readiness decision '
            'is automatically admitted when dependencies and WIP constraints allow. A Task traverses the '
            'complete Column lifecycle: do not make separate Tasks for its stages. After a successful save, '
            'use the returned plan ID with task.create before preparing another plan for the same requested '
            'work.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_PLAN_LIST = BuiltinToolDefinition(
        id='task.plan.list',
        description='Read immutable Task Plans for the current Project.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    WORKFLOW_INSPECT = BuiltinToolDefinition(
        id='workflow.inspect',
        description='Read the active declarative Workflow revision; returns null when none is active.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    WORKFLOW_CREATE = BuiltinToolDefinition(
        id='workflow.create',
        description=(
            'Create or revise the active Workflow in one call from a directed graph. Specify each reusable '
            "Column's executor and every outcome-to-target transition explicitly. Each Task traverses the same "
            'graph from entry; each target is either {kind:column,key:<declared Column key>} or '
            '{kind:terminal,status:done|failed}. done and failed are not Columns. This tool saves its planning '
            'record and publishes the graph together. It creates no Tasks; use the returned Workflow revision '
            'ID in task.plan.save, then task.create. Existing running Tasks must settle before a revision can '
            'replace their graph. Use loop.apply when a suitable optional Loop exists.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    WORKFLOW_PUBLISH = BuiltinToolDefinition(
        id='workflow.publish',
        description=(
            'Publish a declarative Workflow implementing the referenced saved Workflow Plan. This call, not '
            'workflow.plan.save or a Requirement-only revision, applies new Columns and transitions. If the '
            'Project has no Workflow, this creates its initial revision; otherwise it publishes a new '
            'revision. A filesystem Loop can be applied first as an optional template, then revised for this '
            'Requirement. Workflow column keys must exactly match the Workflow Plan process-stage keys; work '
            'slices remain Tasks. The done and failed values are terminal targets, never Column definitions. '
            'Every Column must provide an executor and non-empty transitions; every declared outcome must have '
            'exactly one transition. A capability_sequence selects exactly one of completed_outcome or '
            'outcome_from. A capability whose output contract declares decision outcome values must be saved '
            'and must drive outcome_from; every declared decision value needs a transition. Task-owned exact '
            'values belong in Task input and are consumed losslessly through explicit JSON Pointer $ref '
            'objects. Workflow names, descriptions, and Column instructions describe only the reusable process '
            'and must not restate Task-owned exact content. When the Provider schema requires a primitive '
            'string at a referenced scalar or array position, send the exact transport string '
            '<$ref>/input/task/input/...</$ref>; DevWerk converts it to the canonical $ref object before '
            'publication. Use only schema-listed capability IDs and reach done or failed. If the referenced '
            'Plan declares task_contract.feedback_columns, each named Column must use an agent executor '
            'exposing task.feedback.record.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_CREATE = BuiltinToolDefinition(
        id='task.create',
        description=(
            'Start one immutable Task Plan. Supply its task_plan_id and the item that should be returned as '
            'the requested Task; DevWerk materializes every planned Task exactly once with the authoritative '
            'title, input, readiness, dependencies, conflict domains, exact strings, and fixed Workflow '
            'Revision. Stable Project-level Task identity prevents a later immutable Plan from silently '
            'duplicating existing work; use task.rerun or task.reopen when the intent is to execute an '
            'existing work item again. Dependency/WIP-queued Tasks then advance automatically without further '
            'task.create calls. Use scheduling.decide hold only for deliberate manual waiting. Runtime '
            'asynchronously creates Column Assignments and Workers. A pending Task needs no manual Worker '
            'start. After creating the intended Tasks, use conversation.reply with their IDs; do not implement '
            'their deliverables in this turn or wait for Workers to appear.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    BACKLOG_RECORD = BuiltinToolDefinition(
        id='backlog.record',
        description='Persist work that is queued, held, merged, split, or cancelled without creating a Task.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    SCHEDULING_DECIDE = BuiltinToolDefinition(
        id='scheduling.decide',
        description=(
            'Persist Task admission, priority, and WIP policy. Dependencies and resources are optional '
            'assertions: omit them to preserve the Task Plan facts, or provide the exact current values. An '
            'admitted decision is rejected while any declared dependency is unresolved or not done. For a '
            'Workflow-owned Task Plan queue, hold preserves automatic admission and queued releases the hold '
            'back to dependency-driven admission. An explicitly queued non-Workflow item remains queued until '
            'a later decision.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_LIST = BuiltinToolDefinition(
        id='task.list',
        description='List Tasks for the current Project.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    TASK_INSPECT = BuiltinToolDefinition(
        id='task.inspect',
        description='Read one Task, its Column Runs and artifacts in the current Project.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    TASK_RETRY = BuiltinToolDefinition(
        id='task.retry',
        description=(
            'Retry a non-terminal Task on its pinned Workflow revision. For a paused uncertain command, retry '
            'records its missing exit result as failed without rerunning it; inspect partial workspace effects '
            'before issuing a new command. Other uncertain effects remain blocked. A target Column cannot skip '
            'an uncorrected failed Column or jump into an unvisited later Column. Terminal Tasks remain '
            'immutable.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_REOPEN = BuiltinToolDefinition(
        id='task.reopen',
        description=(
            'Reopen the same failed Task at its failed Column, or at the Workflow entry for a full replay; '
            'later Columns cannot skip the failure. Preserve its ID, artifacts, failure history, and logical '
            'Agent sessions. In a user turn, first call conversation.execution.authorize(request=continue); '
            'task.resume control authorization cannot reopen a failed Task. Use this for a recoverable '
            'execution failure; review rejection must follow the Workflow graph instead of entering failed.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_RERUN = BuiltinToolDefinition(
        id='task.rerun',
        description=(
            'Repeat an immutable done/failed Task using its ORIGINAL frozen TaskPlan and Workflow revision. To '
            'use a corrected plan/revision use task.successor instead. Task Plan dependencies are satisfied '
            'only by successful predecessor Tasks; otherwise the successor remains dependency-queued until one '
            'succeeds.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_PLAN_VALIDATE = BuiltinToolDefinition(
        id='task.plan.validate',
        description=(
            'Read-only admission diagnostics for a concrete Task Plan. Use exactly the same typed plan '
            'with task.plan.save after valid=true; validation alone does not save it. Returns semantic '
            'errors, paths and legal columns. Does not persist plans or execute future commands.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    TASK_PLAN_CONTRACT = BuiltinToolDefinition(
        id='task.plan.contract',
        description=(
            'Read the active Workflow Task input contract, authorizing user request, real entry-evidence '
            'candidates before building a Task Plan. An accepted_scope_snapshot uses one of the returned '
            'execution grant IDs, not a memory ID. Read-only; it does not create or validate a plan.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    WORKFLOW_ACCEPTANCE_CONFIGURE = BuiltinToolDefinition(
        id='workflow.acceptance.configure',
        description=(
            'Optional editor for Workflow checks and the exact outcome edges that require them. Inspect the '
            'active revision, then provide base_revision_id and base_digest. Listed columns replace their '
            'check definitions; listed transitions replace only their required_checks. Frozen Tasks keep their '
            'old revision.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_SUCCESSOR = BuiltinToolDefinition(
        id='task.successor',
        description=(
            'Create a corrected successor of a done/failed Task. First authorize the owning Requirement, save '
            'a corrected TaskPlan on its current Workflow, and preserve the predecessor proposed_task_ref with '
            'repair_of_task_id. For an independent new Task use a new proposed_task_ref and task.create. The '
            'predecessor remains immutable.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_FEEDBACK_RECORD = BuiltinToolDefinition(
        id='task.feedback.record',
        description=(
            'Record a concrete Task defect for Conversation Agent supervision. Feedback does not select a '
            'Workflow edge or block done by itself. Optional checks reference frozen Workflow checks for later '
            'verification. Submit one declared Column outcome to choose the next edge; use a declared rework '
            'outcome when this Column cannot meet its selected edge. This tool does not send Mailbox '
            'instructions or run another Agent.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=True,
    )

    TASK_FEEDBACK_RESOLVE = BuiltinToolDefinition(
        id='task.feedback.resolve',
        description=(
            'Confirm a checkless qualitative Task feedback only after its responsible Column has completed a '
            'later repair visit. A different active review Column must provide a concrete verification note. '
            'Fixed-check feedback resolves through its checks, not this tool; this tool never changes Workflow '
            'routing.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=True,
    )

    TASK_FEEDBACK_LIST = BuiltinToolDefinition(
        id='task.feedback.list',
        description='Read durable Task feedback and Runtime verification references.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    TASK_PAUSE = BuiltinToolDefinition(
        id='task.pause',
        description='Pause a non-terminal Task until it is explicitly resumed.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_RESUME = BuiltinToolDefinition(
        id='task.resume',
        description='Resume a paused or pause-requested non-terminal Task.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_FAIL = BuiltinToolDefinition(
        id='task.fail',
        description=(
            'Route a non-running Task through the explicit failed terminal with a recorded reason. Never use '
            'this against a live Column Attempt; request task.pause and observe it outside running first.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    TASK_CANCEL = BuiltinToolDefinition(
        id='task.cancel',
        description=(
            'Cancel a non-running Task through the explicit failed terminal path. A running Task owns a live '
            'Column Attempt and must first be paused and observed outside running.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    EVENT_LIST = BuiltinToolDefinition(
        id='event.list',
        description='Read Project events after an event cursor.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    AGENT_INSTRUCTION_UPDATE = BuiltinToolDefinition(
        id='agent.instruction.update',
        description='Replace the current Project Conversation Agent instruction as a new persisted revision.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    SUPERVISION_REVIEW_SCHEDULE = BuiltinToolDefinition(
        id='supervision.review.schedule',
        description='Persist a future Project supervision wake-up for a concrete reason.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    GOVERNANCE_DECISION_RECORD = BuiltinToolDefinition(
        id='governance.decision.record',
        description='Record a normalized backlog, scheduling, direct-execution, or intervention decision.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    AGENT_WORKER_LIST = BuiltinToolDefinition(
        id='agent.worker.list',
        description='Inspect persistent Workers and their current Assignment slots.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    AGENT_WORKER_INSPECT = BuiltinToolDefinition(
        id='agent.worker.inspect',
        description='Inspect a Worker, its Assignments and durable message consumption state.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    AGENT_WORKER_SEND = BuiltinToolDefinition(
        id='agent.worker.send',
        description=(
            'Persist steering input for an existing Worker. For a running Assignment, pass its assignment_id; '
            'for a pending next Column visit, omit assignment_id so the next Assignment consumes the input. '
            'Accepted means queued, not consumed; inspect consumed_by_run_id later. This does not complete '
            'work or start an idle Worker.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    AGENT_WORKER_LIFECYCLE = BuiltinToolDefinition(
        id='agent.worker.lifecycle',
        description='Suspend, resume or retire an idle Worker. Retirement preserves its context and cannot be reversed.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    AGENT_RESULT_READ = BuiltinToolDefinition(
        id='agent.result.read',
        description=(
            'Read a bounded slice of an archived tool result by exact source run and tool-call ID from an '
            'evidence index. If that call ID is absent, the result lists recent valid IDs for that run. Use '
            'next_offset to continue; summaries are not execution evidence. For current Task status use '
            'task.inspect.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    AGENT_EVIDENCE_LIST = BuiltinToolDefinition(
        id='agent.evidence.list',
        description='List bounded receipt references from this ColumnRun; use agent.result.read for full evidence.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    AGENT_CONTEXT_READ = BuiltinToolDefinition(
        id='agent.context.read',
        description=(
            'Read bounded excerpts of earlier Worker context by source message ID. Continue after the last '
            'returned ID; use agent.result.read for full tool evidence. Main may specify a Worker.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=True,
    )

    AGENT_CONTEXT_COMPACT = BuiltinToolDefinition(
        id='agent.context.compact',
        description=(
            'Save an explicit coverage summary for an idle Worker, preserving the full source transcript for '
            'later inspection.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    AGENT_WORKER_REPLACE = BuiltinToolDefinition(
        id='agent.worker.replace',
        description=(
            'Create a successor for an idle retired Worker in the current Requirement, with an explicit '
            'handoff summary. Preserves the predecessor history; future assignments for its worker_key select '
            'the successor.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    REQUIREMENT_LIST = BuiltinToolDefinition(
        id='requirement.list',
        description='Inspect active and closed delivery Requirements before continuing planning.',
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    REQUIREMENT_CREATE = BuiltinToolDefinition(
        id='requirement.create',
        description=(
            'Record a new delivery objective with an explicit stable scope_key. Existing scopes retain their '
            'identity; closed scopes are not revived.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    PROJECT_SCOPE_INSPECT = BuiltinToolDefinition(
        id='project.scope.inspect',
        description=(
            'Inspect the current Requirement, Workflow revision and optional frozen Loop bindings before '
            'extending delivery.'
        ),
        side_effect_kind=SideEffectKind.READ,
        delegable_to_column=False,
    )

    PROJECT_SCOPE_REVISE = BuiltinToolDefinition(
        id='project.scope.revise',
        description=(
            'Atomically extend or revise the authorized Requirement on a user request. Use this for changed '
            'Requirement scope, not merely to edit Columns in the same Requirement. Before the first Workflow '
            'exists, pass expected_workflow_revision_id=null and an empty binding_patch; this revises only the '
            'Requirement, then call workflow.create or apply a Loop. If the current Workflow belongs to '
            'another Requirement, its Tasks must all be done or failed before this Requirement can take over '
            'in a new Workflow revision. Historical Tasks and Worker contexts stay intact. For a Workflow '
            'without a Loop, pass an empty binding_patch. Do not close the Requirement first. This does not '
            'create Tasks. A Requirement revision may retain its old Column graph; to change Columns or '
            'transitions, call workflow.create separately, then save a TaskPlan against that final revision. '
            'For a Loop-backed Workflow, optionally select a new Loop digest from loop.inspect.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    REQUIREMENT_SELECT = BuiltinToolDefinition(
        id='requirement.select',
        description='Select an existing active Requirement for this Conversation turn before planning its work.',
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )

    REQUIREMENT_CLOSE = BuiltinToolDefinition(
        id='requirement.close',
        description=(
            'Close a Requirement after all its Tasks and Assignments settle. Late Worker events cannot reopen '
            'it.'
        ),
        side_effect_kind=SideEffectKind.CONTROL,
        delegable_to_column=False,
    )
