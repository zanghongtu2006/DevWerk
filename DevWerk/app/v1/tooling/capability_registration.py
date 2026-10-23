"""Built-in Tool manifest: model-derived schemas, handlers and effect policy."""

from __future__ import annotations
from typing import Any
from app.v1.domain import TaskCreate
from app.v1.protocols.tools.core.capability_protocol import StructuredObjectParameter
from app.v1.protocols.tools.planning.capability_contracts import TaskPlanListReceipt, TaskPlanSaveInput, TaskPlanSaveReceipt, TaskPlanValidateInput, TaskPlanContractReceipt, TaskReceipt, ValidationReceipt, WorkflowAcceptanceConfigureReceipt, WorkflowCreateInput, WorkflowPlanSaveInput, WorkflowPlanListReceipt, WorkflowPlanSaveReceipt, WorkflowPublishInput, WorkflowReceipt
from app.v1.protocols.tools.agent.capability_memory_contracts import MemoryAppendInput, MemoryReadInput, MemoryReadReceipt, MemoryRecordReceipt, MemorySearchReceipt, MemorySearchInput, MemorySupersedeInput, MemoryWriteInput
from app.v1.protocols.tools.workspace.capability_file_contracts import FilePathInput, FileReadInput, FileVerifyInput, FileVerifyOutput, FileWriteInput
from app.v1.protocols.tools.workspace.capability_workspace_contracts import CommandRunInput, ProjectFileListInput, ProjectFileSearchInput, SystemFileListInput, SystemFileReadInput, SystemFileSearchInput, SystemFileWriteInput, with_maximum_limit
from app.v1.protocols.tools.core.capability_misc_contracts import EventListInput, GovernanceDecisionInput, InstructionUpdateInput, LoopApplyInput, LoopInspectInput, LoopListInput, PageLimitInput, SupervisionScheduleInput, with_maximum_page_limit
from app.v1.protocols.tools.workspace.capability_workspace_receipts import CommandReceipt, FileListReceipt, FileMeasureReceipt, FilePageReceipt, FileReadReceipt, FileSearchReceipt, ProjectFileWriteReceipt, SystemFileWriteReceipt
from app.v1.protocols.tools.core.capability_misc_receipts import EventListReceipt, GovernanceDecisionReceipt, NoopReceipt, SupervisionScheduleReceipt
from app.v1.protocols.tools.planning.capability_project_receipts import ConversationAgentRecord, OptionalWorkflowRevisionReceipt, ProjectInspectReceipt
from app.v1.protocols.tools.planning.capability_loop_receipts import LoopApplyReceipt, LoopInspectReceipt, LoopListReceipt
from app.v1.protocols.tools.planning.capability_task_receipts import TaskInspectReceipt, TaskListReceipt, TaskRecord, TaskSuccessorReceipt
from app.v1.protocols.workflow.scheduler_receipt_contracts import SchedulingDecisionReceipt
from app.v1.protocols.storage.store_receipt_contracts import BacklogRecord
from app.v1.protocols.tools.planning.capability_requirement_contracts import RequirementCreateInput, RequirementSelectInput, RequirementCloseInput, ScopeInspectReceipt, ScopeReviseInput
from app.v1.protocols.conversation.conversation_intent_contracts import RequirementListReceipt, RequirementSnapshot
from app.v1.protocols.storage.repository_event_contracts import ScopeRevisionReceipt
from app.v1.protocols.agent.worker_repository_contracts import WorkerContextReadReceipt, WorkerEvidencePage, WorkerInputRecord, WorkerInspectReceipt, WorkerListReceipt, WorkerRecord, WorkerReplaceReceipt, WorkerSnapshotReceipt
from app.v1.protocols.conversation.feedback_context_contracts import TaskFeedbackList, TaskFeedbackRecord
from app.v1.protocols.context.context_state_contracts import AuthorizationReplayReceipt
from app.v1.protocols.tools.agent.capability_conversation_contracts import ConversationHistoryReceipt, ConversationReplyReceipt, ConversationTurnInspectReceipt
from app.v1.protocols.tools.planning.capability_task_contracts import BacklogRecordInput, FeedbackRecordInput, FeedbackResolveInput, SchedulingDecisionInput, TaskFailureInput, TaskIdInput, TaskListInput, TaskRetryInput, TaskSuccessorInput
from app.v1.protocols.tools.agent.capability_agent_contracts import AgentContextCompactInput, AgentContextReadInput, AgentEvidenceListInput, AgentResultReadInput, AgentResultReadReceipt, WorkerInput, WorkerLifecycleInput, WorkerReplaceInput, WorkerSendInput
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, V1RuntimePolicy
from app.v1.workflows.workflow_acceptance_editor import WorkflowAcceptanceEdit
from app.v1.protocols.tools.agent.capability_conversation_contracts import ConversationHistoryInput, ConversationTurnResolutionInput, EmptyInput, ExecutionAuthorizeInput, TaskControlAuthorizeInput

from app.v1.tooling.builtin_tool_catalog import BuiltinTool
from app.v1.tooling.capability_registry import (
    ArgumentPreflight, CapabilityContext, CapabilityEntry, CapabilityHandler, CapabilityRegistry,
)
from app.v1.tooling.capability_handlers import (
    _agent_context_compact,
    _agent_context_read,
    _agent_evidence_list,
    _agent_result_read,
    _agent_worker_lifecycle,
    _agent_worker_send,
    _authorize_execution,
    _authorize_task_control,
    _backlog_record,
    _command_run,
    _conversation_history,
    _event_list,
    _files_list,
    _files_measure,
    _files_read,
    _files_search,
    _files_verify,
    _files_write,
    _inspect_conversation_turn,
    _instruction_update,
    _loop_apply,
    _loop_list,
    _memory_append,
    _memory_read,
    _memory_search,
    _memory_supersede,
    _memory_write,
    _project_inspect,
    _record_governance_decision,
    _requirement_close,
    _requirement_create,
    _resolve_current_turn,
    _schedule_supervision,
    _scheduling_decide,
    _system_command_run,
    _system_files_list,
    _system_files_read,
    _system_files_search,
    _system_files_write,
    _task_create,
    _task_fail,
    _task_inspect,
    _task_list,
    _task_pause,
    _task_plan_contract,
    _task_plan_save,
    _task_plan_validate,
    _task_reopen,
    _task_rerun,
    _task_resume,
    _task_retry,
    _task_successor,
    _verify_expectations_preflight,
    _workflow_acceptance_configure,
    _workflow_create,
    _workflow_inspect,
    _workflow_plan_save,
    _workflow_publish,
    _conversation_reply,
)
from app.v1.conversations.conversation_intent import DiscussionDraftUpdate, WorkIntentState
from app.v1.protocols.conversation.conversation_intent_contracts import TurnResolutionReceipt
from app.v1.conversations.conversation_report import ConversationReply

PLAN_OBJECT_PARAMETER = StructuredObjectParameter("plan")
WORKFLOW_OBJECT_PARAMETER = StructuredObjectParameter("workflow")


def build_core_registry(policy: V1RuntimePolicy | None = None) -> CapabilityRegistry:
    policy = policy or DEFAULT_V1_RUNTIME_POLICY
    registry = CapabilityRegistry(policy)
    registry._bulk_registration = True
    # Defer Workflow schema refresh until every delegable Tool has been registered.
    workflow_publish_input_schema = WorkflowPublishInput.model_json_schema()
    workflow_create_input_schema = WorkflowCreateInput.model_json_schema()
    workflow_plan_save_input_schema = WorkflowPlanSaveInput.model_json_schema()
    task_plan_save_input_schema = TaskPlanSaveInput.model_json_schema()
    task_schema = TaskCreate.model_json_schema()

    workflow_plan_receipt = WorkflowPlanSaveReceipt.model_json_schema()
    workflow_receipt = WorkflowReceipt.model_json_schema()
    validation_receipt = ValidationReceipt.model_json_schema()
    task_receipt = TaskReceipt.model_json_schema()

    # Catalog owns stable IDs, Agent-visible descriptions, effect kinds and visibility.
    # Registration binds model-derived schemas and the implementation handler.
    def register_builtin(
        tool: BuiltinTool,
        input_schema: dict[str, Any],
        handler: CapabilityHandler,
        *,
        output_schema: dict[str, Any],
        argument_preflight: ArgumentPreflight | None = None,
        workflow_reference_fields: tuple[str, ...] = (),
        structured_object_parameters: tuple[StructuredObjectParameter, ...] = (),
    ) -> None:
        definition = tool.value
        registry.register(
            CapabilityEntry(
                id=definition.id,
                description=definition.description,
                input_schema=input_schema,
                output_schema=output_schema,
                handler=handler,
                side_effect_kind=definition.side_effect_kind,
                delegable_to_column=definition.delegable_to_column,
                argument_preflight=argument_preflight,
                workflow_reference_fields=workflow_reference_fields,
                structured_object_parameters=structured_object_parameters,
            )
        )

    # Conversation turn and user authorization tools.
    register_builtin(BuiltinTool.CONVERSATION_REPLY,
        ConversationReply.model_json_schema(), _conversation_reply,
        output_schema=ConversationReplyReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_DRAFT_UPDATE,
        DiscussionDraftUpdate.model_json_schema(), lambda args, ctx: ctx.store.intents.update_draft(ctx, args),
        output_schema=WorkIntentState.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_TURN_INSPECT,
        EmptyInput.model_json_schema(),
        lambda args, ctx: _inspect_conversation_turn(ctx),
        output_schema=ConversationTurnInspectReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_TURN_RESOLVE,
        ConversationTurnResolutionInput.model_json_schema(), _resolve_current_turn,
        output_schema=TurnResolutionReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_EXECUTION_AUTHORIZE,
        ExecutionAuthorizeInput.model_json_schema(),
        _authorize_execution, output_schema=AuthorizationReplayReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_TASK_CONTROL_AUTHORIZE,
        TaskControlAuthorizeInput.model_json_schema(),
        _authorize_task_control, output_schema=TurnResolutionReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.CONVERSATION_HISTORY_READ,
        ConversationHistoryInput.model_json_schema(),
        _conversation_history, output_schema=ConversationHistoryReceipt.model_json_schema(),
    )
    # Memory and workspace tools shared by eligible Agents.
    register_builtin(BuiltinTool.SYSTEM_NOOP,
        EmptyInput.model_json_schema(),
        lambda _a, _c: NoopReceipt(completed=True).model_dump(mode="json"),
        output_schema=NoopReceipt.model_json_schema())
    register_builtin(
        BuiltinTool.PROJECT_MEMORY_READ,
        MemoryReadInput.model_json_schema(),
        _memory_read,
        output_schema=MemoryReadReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_MEMORY_SEARCH,
        MemorySearchInput.model_json_schema(),
        _memory_search,
        output_schema=MemorySearchReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_MEMORY_WRITE,
        MemoryWriteInput.model_json_schema(),
        _memory_write,
        output_schema=MemoryRecordReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_MEMORY_APPEND,
        MemoryAppendInput.model_json_schema(),
        _memory_append,
        output_schema=MemoryRecordReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_MEMORY_SUPERSEDE,
        MemorySupersedeInput.model_json_schema(),
        _memory_supersede,
        output_schema=MemoryRecordReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SYSTEM_FILES_LIST,
        with_maximum_limit(SystemFileListInput, policy.service_limits.max_file_list_size).model_json_schema(),
        _system_files_list,
        output_schema=FileListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SYSTEM_FILES_READ,
        SystemFileReadInput.model_json_schema(),
        _system_files_read,
        output_schema=FileReadReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SYSTEM_FILES_WRITE,
        SystemFileWriteInput.model_json_schema(),
        _system_files_write,
        output_schema=SystemFileWriteReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SYSTEM_FILES_SEARCH,
        with_maximum_limit(SystemFileSearchInput, policy.service_limits.max_search_results).model_json_schema(),
        _system_files_search,
        output_schema=FileSearchReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SYSTEM_COMMAND_RUN,
        CommandRunInput.model_json_schema(),
        _system_command_run,
        output_schema=CommandReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.PROJECT_INSPECT,
        EmptyInput.model_json_schema(), _project_inspect,
        output_schema=ProjectInspectReceipt.model_json_schema())
    register_builtin(
        BuiltinTool.PROJECT_FILES_LIST,
        with_maximum_limit(ProjectFileListInput, policy.service_limits.max_file_list_size).model_json_schema(),
        _files_list,
        output_schema=FileListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_FILES_READ,
        FileReadInput.model_json_schema(),
        _files_read,
        output_schema=FilePageReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_FILES_MEASURE,
        FilePathInput.model_json_schema(),
        _files_measure,
        output_schema=FileMeasureReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_FILES_VERIFY,
        FileVerifyInput.model_json_schema(),
        _files_verify,
        output_schema=FileVerifyOutput.model_json_schema(),
        argument_preflight=_verify_expectations_preflight,
        workflow_reference_fields=("expected_content", "expected_sha256"),
    )
    register_builtin(
        BuiltinTool.PROJECT_FILES_WRITE,
        FileWriteInput.model_json_schema(),
        _files_write,
        output_schema=ProjectFileWriteReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_FILES_SEARCH,
        with_maximum_limit(ProjectFileSearchInput, policy.service_limits.max_search_results).model_json_schema(),
        _files_search,
        output_schema=FileSearchReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.PROJECT_COMMAND_RUN,
        CommandRunInput.model_json_schema(),
        _command_run,
        output_schema=CommandReceipt.model_json_schema(),
    )
    # Optional Loop templates and Workflow/Task planning contracts.
    register_builtin(
        BuiltinTool.LOOP_LIST,
        with_maximum_page_limit(LoopListInput, policy.service_limits.max_page_size).model_json_schema(),
        _loop_list,
        output_schema=LoopListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.LOOP_INSPECT,
        LoopInspectInput.model_json_schema(),
        lambda args, ctx: ctx.store.get_loop(LoopInspectInput.model_validate(args).loop_key),
        output_schema=LoopInspectReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.LOOP_APPLY,
        LoopApplyInput.model_json_schema(),
        _loop_apply,
        output_schema=LoopApplyReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.WORKFLOW_PLAN_SAVE,
        workflow_plan_save_input_schema,
        _workflow_plan_save,
        output_schema=workflow_plan_receipt,
        structured_object_parameters=(PLAN_OBJECT_PARAMETER,),
    )
    register_builtin(
        BuiltinTool.WORKFLOW_PLAN_LIST,
        with_maximum_page_limit(PageLimitInput, policy.service_limits.max_page_size).model_json_schema(),
        lambda args, ctx: ctx.store.list_workflow_plans(ctx.project_id, PageLimitInput.model_validate(args).limit or policy.service_limits.default_page_size),
        output_schema=WorkflowPlanListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_PLAN_SAVE,
        task_plan_save_input_schema,
        _task_plan_save,
        output_schema=TaskPlanSaveReceipt.model_json_schema(),
        structured_object_parameters=(PLAN_OBJECT_PARAMETER,),
    )
    register_builtin(
        BuiltinTool.TASK_PLAN_LIST,
        with_maximum_page_limit(PageLimitInput, policy.service_limits.max_page_size).model_json_schema(),
        lambda args, ctx: ctx.store.list_task_plans(ctx.project_id, PageLimitInput.model_validate(args).limit or policy.service_limits.default_page_size),
        output_schema=TaskPlanListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.WORKFLOW_INSPECT,
        EmptyInput.model_json_schema(),
        _workflow_inspect,
        output_schema=OptionalWorkflowRevisionReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.WORKFLOW_CREATE,
        workflow_create_input_schema,
        lambda args, ctx: _workflow_create(args, ctx, registry),
        output_schema=workflow_receipt,
        structured_object_parameters=(WORKFLOW_OBJECT_PARAMETER,),
    )
    register_builtin(
        BuiltinTool.WORKFLOW_PUBLISH,
        workflow_publish_input_schema,
        lambda args, ctx: _workflow_publish(args, ctx, registry),
        output_schema=workflow_receipt,
        structured_object_parameters=(WORKFLOW_OBJECT_PARAMETER,),
    )
    register_builtin(
        BuiltinTool.TASK_CREATE,
        task_schema,
        lambda args, ctx: _task_create(args, ctx, registry),
        output_schema=task_receipt,
    )
    register_builtin(
        BuiltinTool.BACKLOG_RECORD,
        BacklogRecordInput.model_json_schema(),
        _backlog_record,
        output_schema=BacklogRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SCHEDULING_DECIDE,
        SchedulingDecisionInput.model_json_schema(),
        _scheduling_decide,
        output_schema=SchedulingDecisionReceipt.model_json_schema(),
    )
    # Task inspection, control, feedback and scheduling.
    register_builtin(BuiltinTool.TASK_LIST,
        TaskListInput.model_json_schema(), _task_list,
        output_schema=TaskListReceipt.model_json_schema())
    register_builtin(
        BuiltinTool.TASK_INSPECT,
        TaskIdInput.model_json_schema(),
        _task_inspect,
        output_schema=TaskInspectReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_RETRY,
        TaskRetryInput.model_json_schema(),
        _task_retry,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_REOPEN,
        TaskRetryInput.model_json_schema(),
        _task_reopen,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_RERUN,
        TaskIdInput.model_json_schema(),
        _task_rerun,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(BuiltinTool.TASK_PLAN_VALIDATE,
        TaskPlanValidateInput.model_json_schema(),
        lambda args, ctx: _task_plan_validate(args, ctx, registry), output_schema=validation_receipt,
        structured_object_parameters=(PLAN_OBJECT_PARAMETER,))
    register_builtin(BuiltinTool.TASK_PLAN_CONTRACT,
        EmptyInput.model_json_schema(),
        lambda args, ctx: _task_plan_contract(ctx),
        output_schema=TaskPlanContractReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.WORKFLOW_ACCEPTANCE_CONFIGURE,
        WorkflowAcceptanceEdit.model_json_schema(),
        lambda args, ctx: _workflow_acceptance_configure(args, ctx, registry),
        output_schema=WorkflowAcceptanceConfigureReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.TASK_SUCCESSOR,
        TaskSuccessorInput.model_json_schema(),
        _task_successor, output_schema=TaskSuccessorReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.TASK_FEEDBACK_RECORD,
        FeedbackRecordInput.model_json_schema(),
        lambda args, ctx: ctx.store.feedback.record(ctx, FeedbackRecordInput.model_validate(args).model_dump(exclude_unset=True)),
        output_schema=TaskFeedbackRecord.model_json_schema())
    register_builtin(BuiltinTool.TASK_FEEDBACK_RESOLVE,
        FeedbackResolveInput.model_json_schema(),
        lambda args, ctx: ctx.store.feedback.resolve(ctx, FeedbackResolveInput.model_validate(args).model_dump(exclude_unset=True)),
        output_schema=TaskFeedbackRecord.model_json_schema())
    register_builtin(BuiltinTool.TASK_FEEDBACK_LIST,
        TaskIdInput.model_json_schema(),
        lambda args, ctx: ctx.store.feedback.list(ctx.project_id, TaskIdInput.model_validate(args).task_id),
        output_schema=TaskFeedbackList.model_json_schema())
    register_builtin(
        BuiltinTool.TASK_PAUSE,
        TaskIdInput.model_json_schema(),
        _task_pause,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_RESUME,
        TaskIdInput.model_json_schema(),
        _task_resume,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_FAIL,
        TaskFailureInput.model_json_schema(),
        _task_fail,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.TASK_CANCEL,
        TaskFailureInput.model_json_schema(),
        _task_fail,
        output_schema=TaskRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.EVENT_LIST,
        EventListInput.model_json_schema(),
        _event_list,
        output_schema=EventListReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.AGENT_INSTRUCTION_UPDATE,
        InstructionUpdateInput.model_json_schema(),
        _instruction_update,
        output_schema=ConversationAgentRecord.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.SUPERVISION_REVIEW_SCHEDULE,
        SupervisionScheduleInput.model_json_schema(),
        _schedule_supervision,
        output_schema=SupervisionScheduleReceipt.model_json_schema(),
    )
    register_builtin(
        BuiltinTool.GOVERNANCE_DECISION_RECORD,
        GovernanceDecisionInput.model_json_schema(),
        _record_governance_decision,
        output_schema=GovernanceDecisionReceipt.model_json_schema(),
    )
    # Persistent Worker lifecycle, archived results and Requirement scope.
    register_builtin(BuiltinTool.AGENT_WORKER_LIST,
        EmptyInput.model_json_schema(),
        lambda args, ctx: ctx.store.agents.list_workers(ctx.project_id),
        output_schema=WorkerListReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_WORKER_INSPECT,
        WorkerInput.model_json_schema(),
        lambda args, ctx: ctx.store.agents.inspect_worker(ctx.project_id, WorkerInput.model_validate(args).worker_id),
        output_schema=WorkerInspectReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_WORKER_SEND,
        WorkerSendInput.model_json_schema(),
        _agent_worker_send,
        output_schema=WorkerInputRecord.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_WORKER_LIFECYCLE,
        WorkerLifecycleInput.model_json_schema(),
        _agent_worker_lifecycle, output_schema=WorkerRecord.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_RESULT_READ,
        AgentResultReadInput.model_json_schema(),
        _agent_result_read, output_schema=AgentResultReadReceipt.model_json_schema())
    register_builtin(BuiltinTool.AGENT_EVIDENCE_LIST,
        AgentEvidenceListInput.model_json_schema(),
        _agent_evidence_list, output_schema=WorkerEvidencePage.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_CONTEXT_READ,
        AgentContextReadInput.model_json_schema(),
        _agent_context_read, output_schema=WorkerContextReadReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_CONTEXT_COMPACT,
        AgentContextCompactInput.model_json_schema(),
        _agent_context_compact, output_schema=WorkerSnapshotReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.AGENT_WORKER_REPLACE,
        WorkerReplaceInput.model_json_schema(),
        lambda args, ctx: ctx.store.agents.replace_worker(ctx, **WorkerReplaceInput.model_validate(args).model_dump(exclude_unset=True)),
        output_schema=WorkerReplaceReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.REQUIREMENT_LIST,
        EmptyInput.model_json_schema(),
        lambda args, ctx: ctx.store.agents.list_requirements(ctx.project_id),
        output_schema=RequirementListReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.REQUIREMENT_CREATE,
        RequirementCreateInput.model_json_schema(),
        _requirement_create, output_schema=RequirementSnapshot.model_json_schema(),
    )
    register_builtin(BuiltinTool.PROJECT_SCOPE_INSPECT,
        EmptyInput.model_json_schema(),
        lambda args, ctx: ctx.store.scopes.inspect(ctx.project_id),
        output_schema=ScopeInspectReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.PROJECT_SCOPE_REVISE,
        ScopeReviseInput.model_json_schema(),
        lambda args, ctx: ctx.store.scopes.revise(ctx, **ScopeReviseInput.model_validate(args).model_dump(exclude_unset=True)),
        output_schema=ScopeRevisionReceipt.model_json_schema(),
    )
    register_builtin(BuiltinTool.REQUIREMENT_SELECT,
        RequirementSelectInput.model_json_schema(),
        lambda args, ctx: ctx.store.agents.select_requirement(ctx, RequirementSelectInput.model_validate(args).requirement_id),
        output_schema=RequirementSnapshot.model_json_schema(),
    )
    register_builtin(BuiltinTool.REQUIREMENT_CLOSE,
        RequirementCloseInput.model_json_schema(),
        _requirement_close, output_schema=RequirementSnapshot.model_json_schema(),
    )
    registry._bulk_registration = False
    if set(registry.all_ids()) != {tool.value.id for tool in BuiltinTool}:
        raise ValueError("built-in Tool catalog and Registry entries differ")
    registry._refresh_workflow_tool_models()
    return registry
