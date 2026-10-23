"""Built-in Tool operations and generic Workflow argument checks."""

from __future__ import annotations
import copy
import json
import hashlib
import fnmatch
import re
from typing import Any
from app.v1.domain import AgentExecutor, CapabilitySequenceExecutor, PollWaitPolicy, TaskPlan, ToolResult, WorkflowDefinition, WorkflowPlan
from app.v1.protocols.tools.core.capability_protocol import MemoryScope, READ_LIMITS, SideEffectKind
from app.v1.protocols.tools.planning.capability_contracts import TaskPlanSaveInput, TaskPlanSaveReceipt, TaskPlanValidateInput, AuthorizedScopeRecord, EntryEvidenceCandidate, EntryEvidenceDirectory, TaskMaterializationReceipt, TaskPlanContractReceipt, TaskReceipt, ValidationDiagnostic, ValidationReceipt, DeferredCommandCheck, WorkflowAcceptanceConfigureReceipt, WorkflowCreateInput, WorkflowPlanSaveInput, WorkflowPlanSaveReceipt, WorkflowPublishInput
from app.v1.protocols.tools.agent.capability_memory_contracts import MemoryAppendInput, MemoryReadInput, MemorySearchHit, MemorySearchMetadata, MemorySearchReceipt, MemorySearchInput, MemorySupersedeInput, MemoryWriteInput
from app.v1.protocols.tools.workspace.capability_file_contracts import FilePathInput, FileReadInput, FileVerifyInput, FileWriteInput
from app.v1.protocols.tools.workspace.capability_workspace_contracts import CommandRunInput, MAX_SEARCH_FILE_BYTES, MAX_SEARCH_LINE_CHARACTERS, ProjectFileListInput, ProjectFileSearchInput, SUCCESS_EXIT_CODE, SystemFileListInput, SystemFileReadInput, SystemFileSearchInput, SystemFileWriteInput
from app.v1.protocols.tools.core.capability_misc_contracts import EventListInput, GovernanceDecisionInput, InstructionUpdateInput, LoopApplyInput, LoopListInput, SupervisionScheduleInput
from app.v1.protocols.tools.workspace.capability_workspace_receipts import FileListReceipt, FileMeasureReceipt, FilePageReceipt, FileReadReceipt, FileSearchMatch, FileSearchReceipt, ProjectFileWriteReceipt, SystemFileWriteReceipt
from app.v1.protocols.tools.planning.capability_project_receipts import ProjectInspectReceipt, WorkflowRevisionReceipt
from app.v1.protocols.tools.planning.capability_loop_receipts import LoopApplyReceipt
from app.v1.protocols.tools.planning.capability_task_receipts import ArtifactProjection, ColumnAttemptProjection, ColumnRunProjection, SchedulingProjection, TaskInspectReceipt, TaskProjection
from app.v1.protocols.tools.planning.capability_requirement_contracts import RequirementCreateInput, RequirementCloseInput
from app.v1.protocols.workflow.runtime_json_contracts import TaskFingerprint
from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode
from app.v1.protocols.context.context_state_contracts import AuthorizationReplayReceipt
from app.v1.protocols.tools.agent.capability_conversation_contracts import ConversationHistoryMessage, ConversationHistoryReceipt, ConversationTurnInspectReceipt
from app.v1.protocols.tools.planning.capability_task_contracts import BacklogRecordInput, SchedulingDecisionInput, TaskFailureInput, TaskIdInput, TaskListInput, TaskRetryInput, TaskSuccessorInput
from app.v1.protocols.tools.agent.capability_agent_contracts import AgentContextCompactInput, AgentContextReadInput, AgentEvidenceListInput, AgentResultReadInput, AgentResultReadReceipt, ArchivedCommandOutput, ArchivedToolResult, WorkerLifecycleInput, WorkerSendInput
from app.v1.execution.execution_control import ExecutionOwnershipLost
from app.v1.context.context_projection import slice_source_text
from app.v1.context.memory import MemoryScopeContext, resolve_selector_scope
from app.v1.workflows.workflow_authoring import canonicalize_workflow_capability_arguments, canonicalize_workflow_model, materialize_workflow
from app.v1.workflows.workflow_acceptance_editor import WorkflowAcceptanceEdit
from app.v1.protocols.tools.agent.capability_conversation_contracts import ConversationHistoryInput, ConversationTurnResolutionInput, PriorTurnRevision

from typing import TYPE_CHECKING
from app.v1.contracts import ContractError, check_schema
from app.v1.conversations.conversation_report import ConversationReply, render_report
from app.v1.protocols.tools.agent.capability_conversation_contracts import ConversationReplyReceipt
from app.v1.services.task_entry_admission import validate_entry_evidence
from app.v1.services.task_graph_admission import validate_task_graph_admission, existing_task_orders
from app.v1.workflows.task_identity import logical_task_key
from app.v1.protocols.tools.core.tool_result_message_contracts import ToolResultMessage

if TYPE_CHECKING:
    from app.v1.tooling.capability_registry import CapabilityContext, CapabilityRegistry

WORKFLOW_ACCEPTANCE_DIAGNOSTIC_LIMIT = 12

def _conversation_reply(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    ctx.store.agents.assert_conversation(ctx)
    rendered, evidence = render_report(
        ctx.store, ctx.project_id, ctx.agent_run_id,
        ConversationReply.model_validate(args).model_dump_json(exclude_unset=True),
        execution_control=ctx.execution_control,
    )
    return ConversationReplyReceipt(rendered=rendered, conversation_report=evidence).model_dump(mode="json")


def _memory_scope_context(ctx: CapabilityContext) -> MemoryScopeContext:
    workflow_id = ctx.workflow_revision_id
    if ctx.task_id and not workflow_id:
        workflow_id = ctx.store.get_project_task(ctx.project_id, ctx.task_id)["workflow_revision_id"]
    return MemoryScopeContext(project_id=ctx.project_id, task_id=ctx.task_id,
                              workflow_revision_id=workflow_id,
                              conversation_session_id=ctx.conversation_session_id)


def _validate_memory_scope_id(ctx: CapabilityContext, scope: str, scope_id: str) -> None:
    try:
        if scope == MemoryScope.PROJECT:
            if scope_id != ctx.project_id:
                raise KeyError(scope_id)
        elif scope == MemoryScope.TASK:
            ctx.store.get_project_task(ctx.project_id, scope_id)
        elif scope == MemoryScope.WORKFLOW:
            ctx.store.get_workflow_revision(ctx.project_id, scope_id)
        elif scope == MemoryScope.CONVERSATION:
            if ctx.store.conversation_agent(ctx.project_id)["logical_id"] != scope_id:
                raise KeyError(scope_id)
        else:
            raise ValueError(f"unknown Memory scope: {scope}")
    except KeyError as exc:
        raise ValueError(f"Memory {scope} scope_id does not belong to this Project: {scope_id}") from exc


def _memory_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = MemoryReadInput.model_validate(args)
    item = ctx.store.memory_read_page(ctx.project_id, request.reference,
        offset=request.offset, max_characters=request.max_characters,
        expected_sha256=request.expected_sha256)
    if item.get('source_changed'):
        return item
    metadata = item.get("metadata") or {}
    scope, scope_id = metadata.get("scope"), metadata.get("scope_id")
    if scope in (MemoryScope.TASK, MemoryScope.WORKFLOW, MemoryScope.CONVERSATION):
        try:
            _validate_memory_scope_id(ctx, scope, scope_id)
            item["scope_resolution"] = "resolved"
        except ValueError:
            item["scope_resolution"] = "unbound_or_legacy"
    return item


def _memory_search(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    request = MemorySearchInput.model_validate(args)
    scope = request.scope
    scope_id = request.scope_id
    if scope_id and not scope:
        raise ValueError("Memory scope_id requires scope")
    if scope and scope != MemoryScope.PROJECT:
        if scope_id:
            _validate_memory_scope_id(ctx, scope, scope_id)
        else:
            scope_id = resolve_selector_scope(scope, _memory_scope_context(ctx))
    elif scope == MemoryScope.PROJECT and scope_id:
        _validate_memory_scope_id(ctx, scope, scope_id)
        scope_id = None
    requested_limit = request.limit
    applied_limit = min(requested_limit, READ_LIMITS.memory_search_results)
    search_offset = request.offset
    candidates = ctx.store.memory_search(ctx.project_id, request.query,
        scope=scope, scope_id=scope_id, kinds=request.kinds,
        limit=search_offset+applied_limit+1)
    more_matches = len(candidates) > search_offset+applied_limit
    items = candidates[search_offset:search_offset+applied_limit]
    search_next_offset = search_offset+len(items) if more_matches else None
    projected: list[MemorySearchHit] = []
    for item in items:
        metadata = item.get("metadata") or {}
        scope_resolution = "unbound_or_legacy" if (
            metadata.get("scope") in (MemoryScope.TASK, MemoryScope.WORKFLOW, MemoryScope.CONVERSATION) and
            not metadata.get("scope_id")) else "resolved"
        content = item['content']
        page, next_offset = slice_source_text(content, offset=0,
            max_characters=min(len(content), READ_LIMITS.memory_preview_characters),
            max_bytes=READ_LIMITS.memory_preview_bytes)
        projected.append(MemorySearchHit(
            reference=item['reference'], sha256=item['sha256'],
            metadata=MemorySearchMetadata.model_validate(metadata),
            scope_resolution=scope_resolution, content=page,
            content_complete=next_offset is None, next_offset=next_offset,
            search_more_matches=more_matches, search_limit_applied=applied_limit,
            search_offset=search_offset, search_next_offset=search_next_offset,
        ))
    return MemorySearchReceipt(root=projected).model_dump(mode="json", exclude_unset=True)


def _memory_write(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = MemoryWriteInput.model_validate(args)
    scope_id = _memory_write_scope(request, ctx)
    request.scope_id = scope_id
    request.source_id = request.source_id or ctx.agent_run_id
    request.source_type = request.source_type or "agent_run"
    return ctx.store.memory_write(ctx.project_id, request.model_dump(mode="json", exclude_unset=True))


def _memory_write_scope(request: MemoryWriteInput, ctx: CapabilityContext) -> str | None:
    scope = request.scope
    scope_id = request.scope_id
    if scope == MemoryScope.PROJECT:
        if scope_id:
            _validate_memory_scope_id(ctx, scope, scope_id)
        scope_id = None
    elif scope_id:
        _validate_memory_scope_id(ctx, scope, str(scope_id))
    else:
        scope_id = resolve_selector_scope(scope, _memory_scope_context(ctx))
    return scope_id


def _memory_append(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = MemoryAppendInput.model_validate(args)
    return ctx.store.memory_append(
        ctx.project_id, request.reference, request.content,
        source_type=request.source_type or "agent_run",
        source_id=request.source_id or ctx.agent_run_id or None,
    )


def _memory_supersede(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = MemorySupersedeInput.model_validate(args)
    replacement = request.replacement.model_copy(update={
        "scope_id": _memory_write_scope(request.replacement, ctx),
        "source_id": request.replacement.source_id or ctx.agent_run_id,
        "source_type": request.replacement.source_type or "agent_run",
    })
    return ctx.store.memory_supersede(ctx.project_id, request.reference, replacement.model_dump(exclude_unset=True))


def validate_workflow_capabilities(workflow: WorkflowDefinition, registry: CapabilityRegistry) -> None:
    known = set(registry.column_ids())
    for column in workflow.columns:
        for check in column.acceptance_checks:
            if check.capability not in known or not SideEffectKind(registry.side_effect_kind(check.capability)).supports_acceptance_check:
                raise ValueError(
                    f"column {column.key!r} acceptance check {check.key!r} requires "
                    "a registered Column capability with none/read/process effect"
                )
            try:
                registry.validate_arguments(check.capability, check.arguments)
            except ContractError as exc:
                location = f"workflow.columns[{column.key}].acceptance_checks[{check.key}].arguments"
                if exc.path and exc.path != "$":
                    location += f"/{exc.path}"
                raise ContractError(
                    f"{location}: {exc}", path=location,
                    expected=exc.expected, actual=exc.actual,
                ) from exc
        if column.executor is None:
            continue
        if isinstance(column.executor, AgentExecutor):
            requested = set(column.executor.capabilities)
        elif isinstance(column.executor, CapabilitySequenceExecutor):
            requested = {step.capability for step in column.executor.steps}
        if isinstance(column.wait_policy, PollWaitPolicy):
            requested.update(item for item in (column.wait_policy.poll_capability, column.wait_policy.cancel_capability, column.wait_policy.cleanup_capability) if item)
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"column {column.key!r} references unknown or non-delegable capabilities: {unknown}")
        if isinstance(column.executor, CapabilitySequenceExecutor):
            decision_steps: list[tuple[int, Any, tuple[str, ...]]] = []
            for index, step in enumerate(column.executor.steps):
                try:
                    registry.validate_workflow_references(step.capability, step.arguments)
                    _validate_sequence_argument_references(
                        column.key,
                        column.executor.steps,
                        index,
                        step.arguments,
                        registry,
                    )
                    registry.validate_argument_template(step.capability, step.arguments)
                except ContractError as exc:
                    location = f"workflow.columns[{column.key}].executor.steps[{index}].arguments"
                    if exc.path and exc.path != "$":
                        location += f"/{exc.path}"
                    raise ContractError(
                        f"{location}: {exc}", path=location,
                        expected=exc.expected, actual=exc.actual,
                    ) from exc
                outcome_tokens = registry.outcome_tokens(step.capability)
                if outcome_tokens:
                    decision_steps.append((index, step, outcome_tokens))
            if len(decision_steps) > 1:
                raise ValueError(
                    f"column {column.key!r} has multiple decision-producing capability steps; "
                    "split them into independently routed process Columns"
                )
            if decision_steps:
                _index, decision_step, outcome_tokens = decision_steps[0]
                if not decision_step.save_as:
                    raise ValueError(
                        f"column {column.key!r} decision-producing capability "
                        f"{decision_step.capability!r} requires save_as"
                    )
                expected_pointer = f"/steps/{decision_step.save_as}/output/outcome"
                if column.executor.outcome_from != expected_pointer:
                    raise ValueError(
                        f"column {column.key!r} must derive its sequence outcome from "
                        f"{expected_pointer!r}; a fixed or unrelated outcome cannot replace "
                        f"{decision_step.capability!r} evidence"
                    )
            if column.executor.outcome_from:
                selected_tokens = _validate_sequence_outcome_pointer(
                    column.key,
                    column.executor.steps,
                    column.executor.outcome_from,
                    registry,
                )
                declared = {transition.outcome for transition in column.transitions}
                missing = sorted(set(selected_tokens) - declared)
                if missing:
                    raise ValueError(
                        f"column {column.key!r} has no transition for selected outcome values "
                        f"{missing} declared by its Capability output schema"
                    )


def _validate_sequence_argument_references(
    column_key: str,
    steps: list[Any],
    current_index: int,
    value: Any,
    registry: CapabilityRegistry,
) -> None:
    if isinstance(value, str):
        if "<$ref>" in value or "</$ref>" in value:
            raise ValueError(
                f"column {column_key!r} capability step {current_index} contains a tagged "
                "$ref string; use an object containing only $ref"
            )
        if any(character in value for character in {"\r", "\n", "\t", "\b", "\f"}):
            raise ValueError(
                f"column {column_key!r} capability step {current_index} contains an inline "
                "control-character-sensitive string; declare the complete exact value in "
                "Task Plan exact_input_strings and consume it through a Task-input $ref"
            )
        return
    if isinstance(value, list):
        for item in value:
            _validate_sequence_argument_references(
                column_key, steps, current_index, item, registry
            )
        return
    if not isinstance(value, dict):
        return
    if set(value) == {"$ref"}:
        pointer = str(value["$ref"])
        tokens = _json_pointer_tokens(pointer)
        if tokens and tokens[0] == "input":
            return
        if len(tokens) < 4 or tokens[0] != "steps" or tokens[2] != "output":
            raise ValueError(
                f"column {column_key!r} capability step {current_index} has unsupported "
                f"runtime reference {pointer!r}; use /input/... or /steps/<save_as>/output/..."
            )
        source_index, source_step = _sequence_step_by_result_key(steps, tokens[1])
        if source_index >= current_index:
            raise ValueError(
                f"column {column_key!r} capability step {current_index} reference {pointer!r} "
                "must select an earlier saved step"
            )
        _schema_at_output_pointer(
            registry.output_schema(source_step.capability),
            tokens[3:],
            pointer,
        )
        return
    for item in value.values():
        _validate_sequence_argument_references(
            column_key, steps, current_index, item, registry
        )


def _validate_sequence_outcome_pointer(
    column_key: str,
    steps: list[Any],
    pointer: str,
    registry: CapabilityRegistry,
) -> tuple[str, ...]:
    tokens = _json_pointer_tokens(pointer)
    if len(tokens) < 4 or tokens[0] != "steps" or tokens[2] != "output":
        raise ValueError(
            f"column {column_key!r} outcome_from must use "
            "/steps/<save_as>/output/... and select a schema-declared string enum"
        )
    _source_index, source_step = _sequence_step_by_result_key(steps, tokens[1])
    if not source_step.save_as or source_step.save_as != tokens[1]:
        raise ValueError(
            f"column {column_key!r} outcome_from requires an explicit save_as on "
            f"the selected capability step"
        )
    selected = _schema_at_output_pointer(
        registry.output_schema(source_step.capability),
        tokens[3:],
        pointer,
    )
    values = ProviderSchemaNode.model_validate(selected).enum
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(item, str) or not item for item in values)
    ):
        raise ValueError(
            f"column {column_key!r} outcome_from {pointer!r} must select a "
            "schema-declared non-empty string enum"
        )
    return tuple(values)


def _sequence_step_by_result_key(steps: list[Any], key: str) -> tuple[int, Any]:
    matches = [
        (index, step)
        for index, step in enumerate(steps)
        if (step.save_as or str(index)) == key
    ]
    if len(matches) != 1:
        raise ValueError(
            f"sequence result key {key!r} must identify exactly one capability step"
        )
    return matches[0]


def _schema_at_output_pointer(
    schema: dict[str, Any],
    tokens: list[str],
    pointer: str,
) -> dict[str, Any]:
    current = ProviderSchemaNode.model_validate(schema)
    for token in tokens:
        child = current.child(token)
        if not isinstance(child, ProviderSchemaNode):
            raise ValueError(
                f"runtime reference {pointer!r} does not exist in the Capability output schema"
            )
        current = child
    return current.wire()


def validate_task_capability_bindings(
    workflow: WorkflowDefinition,
    registry: CapabilityRegistry,
    input_data: dict[str, Any],
    *,
    exact_strings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Materialize planned exact strings and preflight deterministic bindings."""
    normalized_input = copy.deepcopy(input_data)
    if exact_strings is not None:
        for pointer, value in sorted(exact_strings.items()):
            _set_json_pointer(normalized_input, pointer, value)
    scope = {"input": {"task": {"input": normalized_input}}}
    task_input_references: set[str] = set()
    for column in workflow.columns:
        if column.executor is None:
            continue
        sequences: list[tuple[str, list[Any]]] = []
        if isinstance(column.executor, CapabilitySequenceExecutor):
            sequences.append((column.key, column.executor.steps))
        for owner, steps in sequences:
            for index, step in enumerate(steps):
                task_input_references.update(_task_input_references(step.arguments))
                try:
                    arguments = _resolve_task_input_references(step.arguments, scope)
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Task input cannot resolve Column {owner} capability step {index} "
                        f"({step.capability}): {exc}"
                    ) from exc
                if _contains_runtime_reference(arguments):
                    continue
                try:
                    registry.validate_arguments(step.capability, arguments)
                except Exception as exc:
                    raise ValueError(
                        f"Task input rejects Column {owner} capability step {index} "
                        f"({step.capability}): {exc}"
                    ) from exc
    if exact_strings is not None:
        for pointer in sorted(task_input_references):
            try:
                value = _json_pointer(scope, pointer)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise ValueError(
                    f"Task input cannot resolve deterministic reference {pointer!r}: {exc}"
                ) from exc
            task_pointer = pointer.removeprefix("/input/task/input")
            for string_pointer, string_value in _string_bindings(value, task_pointer):
                planned = exact_strings.get(string_pointer)
                if planned is None:
                    raise ValueError(
                        f"deterministic Task input string at {string_pointer!r} must be "
                        "declared in the immutable Task Plan exact_input_strings"
                    )
                if planned != string_value:
                    raise ValueError(
                        f"deterministic Task input string at {string_pointer!r} does not "
                        "match its immutable Task Plan exact_input_strings value"
                    )
    return normalized_input


def _resolve_task_input_references(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            pointer = str(value["$ref"])
            if pointer == "/input/task/input" or pointer.startswith("/input/task/input/"):
                return _json_pointer(scope, pointer)
            return value
        return {
            key: _resolve_task_input_references(item, scope)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_task_input_references(item, scope) for item in value]
    return value


def _task_input_references(value: Any) -> set[str]:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            pointer = str(value["$ref"])
            if pointer == "/input/task/input" or pointer.startswith("/input/task/input/"):
                return {pointer}
            return set()
        result: set[str] = set()
        for item in value.values():
            result.update(_task_input_references(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_task_input_references(item))
        return result
    return set()


def _string_bindings(value: Any, pointer: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(pointer, value)]
    if isinstance(value, dict):
        result: list[tuple[str, str]] = []
        for key, item in value.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            result.extend(_string_bindings(item, f"{pointer}/{token}"))
        return result
    if isinstance(value, list):
        result: list[tuple[str, str]] = []
        for index, item in enumerate(value):
            result.extend(_string_bindings(item, f"{pointer}/{index}"))
        return result
    return []


def _set_json_pointer(document: dict[str, Any], pointer: str, value: str) -> None:
    tokens = _json_pointer_tokens(pointer)
    if not tokens:
        raise ValueError("exact Task input string pointer must select a value below the input root")
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"ExactInputTargetMissing: {pointer!r} must select an existing string relative to Task.input")
            current = current[token]
        elif isinstance(current, list):
            index = int(token)
            if index < 0 or index >= len(current):
                raise ValueError(f"exact Task input string pointer index is unavailable: {pointer!r}")
            current = current[index]
        else:
            raise ValueError(f"exact Task input string pointer crosses a scalar: {pointer!r}")
    final = tokens[-1]
    if isinstance(current, dict):
        if final not in current:
            raise ValueError(f"ExactInputTargetMissing: {pointer!r} must select an existing string relative to Task.input")
        if not isinstance(current[final], str):
            raise ValueError(f"ExactInputTargetType: {pointer!r} selects {type(current[final]).__name__}, not a string")
        current[final] = value
        return
    if isinstance(current, list):
        index = int(final)
        if index < 0 or index >= len(current):
            raise ValueError(f"exact Task input string pointer index is unavailable: {pointer!r}")
        if not isinstance(current[index], str):
            raise ValueError(f"ExactInputTargetType: {pointer!r} must select a string")
        current[index] = value
        return
    raise ValueError(f"exact Task input string pointer crosses a scalar: {pointer!r}")


def task_binding_exact_strings(
    store: Any,
    project_id: str,
    task_plan_id: str,
    proposed_task_ref: str,
) -> dict[str, str]:
    plan = TaskPlan.model_validate(
        store.get_task_plan(project_id, task_plan_id)["plan"]
    )
    proposed = next(
        (
            item
            for item in plan.tasks
            if item.proposed_task_ref == proposed_task_ref
        ),
        None,
    )
    if proposed is None:
        raise ValueError("Task must reference an entry in the Task Plan")
    return {
        item.pointer: item.value
        for item in proposed.exact_input_strings
    }


def _project_inspect(_args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    project = {key: ctx.project[key] for key in ("id", "name", "description", "base_dir", "created_at", "updated_at") if key in ctx.project}
    try:
        workflow = ctx.store.get_workflow(ctx.project_id)
    except KeyError:
        workflow = None
    return ProjectInspectReceipt.model_validate({"project": project, "workflow": workflow}).model_dump(mode="json")


def _system_files_list(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SystemFileListInput.model_validate(args)
    limit = request.limit or ctx.store.policy.service_limits.default_file_list_size
    paths = ctx.system_files.list_paths(
        request.path or ".",
        request.pattern or "**/*",
        limit=limit,
    )
    return FileListReceipt(paths=paths, count=len(paths)).model_dump(mode="json")


def _system_files_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SystemFileReadInput.model_validate(args)
    path = request.path
    resolved = ctx.system_files.resolve(path)
    return FileReadReceipt(
        path=str(resolved), content=ctx.system_files.read_text(path, request.max_chars),
    ).model_dump(mode="json")


def _system_files_write(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SystemFileWriteInput.model_validate(args)
    with ctx.effect_guard():
        return SystemFileWriteReceipt(
            file=ctx.system_files.write_text(request.path, request.content)
        ).model_dump(mode="json")


def _system_files_search(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SystemFileSearchInput.model_validate(args)
    return FileSearchReceipt.model_validate(ctx.system_files.search_text(
        request.path or ".",
        request.pattern,
        request.glob or "**/*",
        limit=request.limit or ctx.store.policy.service_limits.default_search_results,
    )).model_dump(mode="json")


def _system_command_run(args: dict[str, Any], ctx: CapabilityContext) -> ToolResult:
    request = CommandRunInput.model_validate(args)
    output = ctx.system_files.run(
        request.argv,
        request.cwd or ".",
        check=ctx.execution_control.check if ctx.execution_control else None,
        limits=ctx.store.policy.execution,
        guard=ctx.effect_guard,
    )
    exit_code = int(output["exit_code"])
    if exit_code == SUCCESS_EXIT_CODE and not output.get("timed_out") and not output.get("output_truncated"):
        return ToolResult(ok=True, capability="system.command.run", output=output)
    message = _command_failure_message(output, exit_code)
    return ToolResult(
        ok=False,
        capability="system.command.run",
        output=output,
        error={"type": "CommandFailed", "message": message, "exit_code": exit_code,
               "timed_out": bool(output.get("timed_out")),
               "output_truncated": bool(output.get("output_truncated"))},
    )


def _files_list(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = ProjectFileListInput.model_validate(args)
    pattern = request.pattern or "**/*"
    limit = request.limit or ctx.store.policy.service_limits.default_file_list_size
    paths = ctx.files.list_paths(pattern, limit=limit)
    return FileListReceipt(paths=paths, count=len(paths)).model_dump(mode="json")


def _files_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = FileReadInput.model_validate(args)
    return FilePageReceipt.model_validate(ctx.files.read_text_page(
        request.path,
        offset=request.offset,
        max_chars=request.max_chars,
    )).model_dump(mode="json")


def _files_measure(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return FileMeasureReceipt.model_validate(
        ctx.files.measure_text(FilePathInput.model_validate(args).path)
    ).model_dump(mode="json")


def _files_verify(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = FileVerifyInput.model_validate(args)
    return ctx.files.verify_text(request.path, request.expectations())


def _verify_expectations_preflight(args: dict[str, Any]) -> None:
    FileVerifyInput.model_validate(args).verify_consistency()


def _files_write_preflight(args: dict[str, Any], ctx: CapabilityContext) -> None:
    requested_path = FileWriteInput.model_validate(args).path.replace("\\", "/")
    if ctx.writable_paths is not None and not any(
        fnmatch.fnmatchcase(requested_path, pattern.replace("\\", "/"))
        for pattern in ctx.writable_paths
    ):
        raise ValueError(
            f"project.files.write path {requested_path!r} is outside this Column's declared writable paths: "
            f"{list(ctx.writable_paths)!r}"
        )
    ctx.files.resolve(requested_path)


def _files_write(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    with ctx.store.tx(immediate=True):
        if ctx.execution_control:
            ctx.execution_control.check()
        return _files_write_owned(args, ctx)


def _files_write_owned(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = FileWriteInput.model_validate(args)
    info = ctx.files.write_text(request.path, request.content)
    artifact = ctx.store.register_artifact(
        ctx.project_id,
        ctx.task_id,
        ctx.column_run_id,
        request.kind or "file",
        info["path"],
        info["sha256"],
        info["size"],
        {"agent_run_id": ctx.agent_run_id},
    )
    return ProjectFileWriteReceipt(
        file=info, artifact=artifact, execution_key=ctx.execution_key,
    ).model_dump(mode="json", exclude_none=True)


def _files_search(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = ProjectFileSearchInput.model_validate(args)
    try:
        expression = re.compile(request.pattern)
    except re.error as exc:
        raise ValueError(f"invalid search pattern: {exc}") from exc
    limit = request.limit or ctx.store.policy.service_limits.default_search_results
    matches: list[dict[str, Any]] = []
    paths = ctx.files.search_paths(request.glob or "**/*",
                                   limit=ctx.store.policy.service_limits.max_file_list_size)
    for path in paths:
        if ctx.execution_control:
            ctx.execution_control.check()
        if ctx.files.resolve(path).stat().st_size > MAX_SEARCH_FILE_BYTES:
            continue
        try:
            content = ctx.files.read_text(path)
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if expression.search(line):
                matches.append(FileSearchMatch(
                    path=path, line=number, text=line[:MAX_SEARCH_LINE_CHARACTERS],
                ).model_dump(mode="json"))
                if len(matches) >= limit:
                    return FileSearchReceipt(matches=matches, truncated=True).model_dump(mode="json")
    return FileSearchReceipt(matches=matches, truncated=False).model_dump(mode="json")


def _command_run(args: dict[str, Any], ctx: CapabilityContext) -> ToolResult:
    request = CommandRunInput.model_validate(args)
    output = ctx.files.run(
        request.argv,
        request.cwd or ".",
        check=ctx.execution_control.check if ctx.execution_control else None,
        limits=ctx.store.policy.execution,
        guard=ctx.effect_guard,
    )
    exit_code = int(output["exit_code"])
    if exit_code == SUCCESS_EXIT_CODE and not output.get("timed_out") and not output.get("output_truncated"):
        return ToolResult(ok=True, capability="project.command.run", output=output)
    message = _command_failure_message(output, exit_code)
    return ToolResult(
        ok=False,
        capability="project.command.run",
        output=output,
        error={
            "type": "CommandFailed",
            "message": message,
            "exit_code": exit_code,
            "timed_out": bool(output.get("timed_out")),
            "output_truncated": bool(output.get("output_truncated")),
        },
    )


def _workflow_inspect(_args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any] | None:
    try:
        return WorkflowRevisionReceipt.model_validate(ctx.store.get_workflow(ctx.project_id)).model_dump(mode="json")
    except KeyError:
        return None


def _require_user_planning_turn(ctx: CapabilityContext, capability: str) -> None:
    if not ctx.start_task or ctx.column_run_id:
        raise PermissionError(f'{capability} requires an active Conversation planning turn')
    if ctx.agent_run_id is not None:
        if not ctx.agent_instance_id or not ctx.requirement_id:
            # Compatibility for explicit user calls from older service clients.
            if ctx.user_initiated:
                return
            raise PermissionError('Conversation planning requires an explicit active Requirement')
        main = ctx.store.agents.get_worker(ctx.project_id, ctx.agent_instance_id)
        requirement = ctx.store.agents.get_requirement(ctx.project_id, ctx.requirement_id)
        if main['role'] != 'main' or requirement['status'] != 'active':
            raise PermissionError('Requirement is no longer active; use project.scope.revise for a user-requested extension')
        if ctx.requirement_revision is not None and requirement['revision'] != ctx.requirement_revision:
            raise ExecutionOwnershipLost('Conversation turn uses an obsolete Requirement revision; late events cannot plan new scope')
        ctx.store.agents.charge_planning(ctx)


def _requirement_create(args, ctx):
    request = RequirementCreateInput.model_validate(args)
    ctx.store.agents.assert_conversation(ctx)
    if not ctx.user_initiated or not ctx.start_task:
        raise PermissionError('A new delivery objective requires a user-initiated turn; continue the active Requirement for event-driven work')
    requirement = ctx.store.agents.requirement(ctx.project_id, **request.model_dump(), strict=True)
    return ctx.store.agents.select_requirement(ctx, requirement['id'])


def _requirement_close(args, ctx):
    request = RequirementCloseInput.model_validate(args)
    _require_user_planning_turn(ctx, 'requirement.close')
    if ctx.requirement_id and ctx.requirement_id != request.requirement_id:
        raise ValueError('Select the Requirement before closing it')
    return ctx.store.agents.close_requirement(ctx.project_id, **request.model_dump())


def _inspect_conversation_turn(ctx):
    job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
    if not job:
        raise ValueError('A current Conversation Job is required')
    return ConversationTurnInspectReceipt(
        source_message_id=job['user_message_id'], turn_contract=ctx.store.intents.contract(job),
        work_intent=ctx.store.intents.context(job), requested_mode=job['requested_mode'],
    ).model_dump(mode="json")


def _loop_apply(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "loop.apply")
    request = LoopApplyInput.model_validate(args)
    result = ctx.store.apply_loop(
        ctx.project_id,
        request.loop_key,
        request.bindings,
    )
    _bind_requirement(ctx, 'v1_workflow_plans', result['workflow_plan']['id'])
    scope = ctx.store.scopes.for_workflow(result['workflow']['id'])
    ctx.store.scopes.bind_workflow(result['workflow']['id'], scope['binding_id'], ctx.requirement_id, ctx.requirement_revision)
    return LoopApplyReceipt.model_validate(result).model_dump(mode="json")


def _loop_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    request = LoopListInput.model_validate(args)
    return ctx.store.list_loops(
        category=request.category,
        tag=request.tag,
        query=request.query,
        limit=request.limit or ctx.store.policy.service_limits.default_page_size,
    )


def _command_failure_message(output: dict[str, Any], exit_code: int) -> str:
    if output.get('timed_out'):
        return ('command timed out; inspect output.stdout/stderr. A foreground service must be '
                'started, checked, and stopped within one bounded command')
    if output.get('output_truncated'):
        return 'command output exceeded limit; inspect bounded output.stdout/stderr'
    return f'command exited with code {exit_code}; inspect output.stdout/stderr'


def _workflow_plan_save(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "workflow.plan.save")
    request = WorkflowPlanSaveInput.model_validate(args)
    result = ctx.store.create_workflow_plan(ctx.project_id, request.plan)
    _bind_requirement(ctx, 'v1_workflow_plans', result['id'])
    return WorkflowPlanSaveReceipt.from_saved_plan(result).model_dump(mode="json")


def _task_plan_save(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "task.plan.save")
    request = TaskPlanSaveInput.model_validate(args)
    _check_workflow_scope(ctx, request.plan.workflow_revision_id)
    result = ctx.store.create_task_plan(ctx.project_id, request.plan)
    _bind_requirement(ctx, 'v1_task_plans', result['id'])
    return TaskPlanSaveReceipt.from_saved_plan(result, request.plan.tasks[0].proposed_task_ref).model_dump(mode="json")


def _task_plan_validate(args, ctx, registry):
    # The compiler imports capability validation at call time; retain this lazy
    # import to avoid a cycle between plan diagnostics and Tool handlers.
    from app.v1.services.task_plan_compiler import compile_task_plan, diagnose_task_plan, diagnose_acceptance_launches
    plan = TaskPlanValidateInput.model_validate(args).plan
    revision = ctx.store.get_workflow_revision(ctx.project_id, plan.workflow_revision_id)
    method = WorkflowPlan.model_validate(ctx.store.get_workflow_plan(ctx.project_id, revision['workflow_plan_id'])['plan'])
    result = ValidationReceipt.model_validate(diagnose_task_plan(
        plan, WorkflowDefinition.model_validate(revision['definition']), method, registry,
    ))
    if result.valid:
        plan = compile_task_plan(plan, WorkflowDefinition.model_validate(revision['definition']), method, registry)
    launches = diagnose_acceptance_launches(ctx.store,ctx.project_id,WorkflowDefinition.model_validate(revision['definition']))
    result.diagnostics.extend(ValidationDiagnostic.model_validate(item) for item in launches['diagnostics'])
    result.deferred_command_checks = [DeferredCommandCheck.model_validate(item) for item in launches['deferred_command_checks']]
    binding = ctx.store.get_project_loop_binding(ctx.project_id, plan.workflow_revision_id)
    for check in (() if result.diagnostics else (
        lambda: validate_entry_evidence(ctx.store, ctx.project_id, plan, method),
        lambda: validate_task_graph_admission(plan,method.task_contract.dependency_contract,method.task_contract.admission_constraints,
            loop_bindings=dict(binding.get('bindings') or {}) if binding else {},
            existing_orders=existing_task_orders(ctx.store,ctx.project_id,method.task_contract.dependency_contract)),
    )):
        try:
            check()
        except ValueError as exc:
            result.diagnostics.append(ValidationDiagnostic(path='/tasks', message=str(exc)))
    if not result.diagnostics:
        if plan.repair_of_task_id:
            try:
                predecessor = ctx.store.get_project_task(ctx.project_id,plan.repair_of_task_id)
                if predecessor['status'] not in {'done','failed'} or not any(
                        item.proposed_task_ref == predecessor['proposed_task_ref'] for item in plan.tasks):
                    raise ValueError('repair_of_task_id must name a terminal Task whose proposed_task_ref is preserved')
            except (KeyError,ValueError) as exc:
                result.diagnostics.append(ValidationDiagnostic(path='/repair_of_task_id', message=str(exc)))
        with ctx.store.connect() as db:
            for item in plan.tasks:
                identity = logical_task_key(method.task_contract,item.input)
                prior = db.execute('''SELECT id,status,proposed_task_ref FROM v1_tasks
                    WHERE project_id=? AND logical_task_key=? ORDER BY created_at DESC LIMIT 1''',
                    (ctx.project_id,identity)).fetchone() if identity else None
                if prior and not (plan.repair_of_task_id == prior['id'] and prior['status'] in {'done','failed'}
                                  and item.proposed_task_ref == prior['proposed_task_ref']):
                    result.diagnostics.append(ValidationDiagnostic(
                        path='/repair_of_task_id',
                        message=f'DuplicateTaskIdentity: {identity} already belongs to Task {prior["id"]}; '
                                'repair_of_task_id must name this latest terminal predecessor',
                    ))
    result.valid = not result.diagnostics
    return result.model_dump(mode='json', exclude_none=True, exclude_unset=True)


def _task_plan_contract(ctx):
    """Expose a compact, factual planning guide without fabricating Task inputs."""
    try:
        workflow = ctx.store.get_workflow(ctx.project_id)
    except KeyError:
        return TaskPlanContractReceipt(
            workflow_revision_id=None,
            next_action='Create or select a Workflow before planning a Task.',
        ).model_dump(mode='json', exclude_unset=True)
    method = WorkflowPlan.model_validate(ctx.store.get_workflow_plan(
        ctx.project_id, workflow['workflow_plan_id'])['plan'])
    contract = method.task_contract
    scope = ctx.store.scopes.for_workflow(workflow['id']) or {}
    evidence: dict[str, EntryEvidenceDirectory] = {}
    with ctx.store.connect() as db:
        grants = [dict(row) for row in db.execute(
            "SELECT g.id,g.created_at,j.message AS user_request FROM v1_execution_grants g "
            "JOIN v1_conversation_jobs j ON j.id=g.source_job_id "
            "WHERE g.project_id=? AND g.requirement_id=? "
            "AND g.requirement_revision=? AND g.state='active' ORDER BY g.created_at DESC LIMIT 5",
            (ctx.project_id, scope.get('requirement_id'), scope.get('requirement_revision')))]
        for rule in contract.entry_requirements:
            if rule.kind == 'accepted_scope_snapshot':
                evidence[rule.key] = EntryEvidenceDirectory(
                    kind=rule.kind, candidate_ids=[g['id'] for g in grants],
                )
                continue
            candidates: list[EntryEvidenceCandidate] = []
            for row in db.execute(
                "SELECT execution_key,arguments_json FROM v1_execution_receipts WHERE project_id=? "
                "AND capability='project.files.write' AND status='completed' ORDER BY started_at DESC LIMIT 100",
                (ctx.project_id,)):
                args = json.loads(row['arguments_json'])
                operation = db.execute(
                    'SELECT source_run_id FROM v1_agent_operations WHERE id=? AND project_id=?',
                    (row['execution_key'], ctx.project_id)).fetchone()
                source_run = operation['source_run_id'] if operation else row['execution_key'].split(':', 1)[0]
                job = ctx.store.intents.job_for_run(source_run, ctx.project_id)
                if not job or job.get('execution_grant_id') not in {g['id'] for g in grants}:
                    continue
                try:
                    target = ctx.files.resolve(args['path'])
                    if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(
                        args['content'].encode('utf-8')).hexdigest():
                        continue
                except (KeyError, TypeError, OSError, ValueError):
                    continue
                candidates.append(EntryEvidenceCandidate(
                    execution_key=row['execution_key'], path=args.get('path'),
                    execution_grant_id=job['execution_grant_id'],
                ))
                if len(candidates) >= 5:
                    break
            evidence[rule.key] = EntryEvidenceDirectory(
                kind=rule.kind, input_pointer=rule.input_pointer, candidates=candidates,
            )
    active_job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id) if ctx.agent_run_id else None
    authorized_scope: list[AuthorizedScopeRecord] = []
    for grant in grants:
        current_request = (active_job['message'] if active_job and
                           active_job.get('execution_grant_id') == grant['id'] else grant['user_request'])
        authorized_scope.append(AuthorizedScopeRecord(
            grant_id=grant['id'], user_request=current_request,
            original_user_request=grant['user_request'] if current_request != grant['user_request'] else None,
        ))
    return TaskPlanContractReceipt(
        workflow_revision_id=workflow['id'], flow_unit=method.flow_unit,
        input_schema=contract.input_schema, identity_pointer=contract.identity_pointer,
        entry_evidence=evidence, authorized_scope=authorized_scope,
        next_action='Build Tasks from independent accepted flow units, not Column stages. Match each declared entry requirement to its exact Task input and current grant; validate the complete plan, save it, then call task.create with the returned plan ID.',
    ).model_dump(mode='json', exclude_none=True)


def _workflow_acceptance_configure(args, ctx, registry):
    # See _task_plan_validate: acceptance diagnostics share that compiler.
    from app.v1.services.task_plan_compiler import acceptance_diagnostics, PlanAdmissionError
    _require_user_planning_turn(ctx, 'workflow.acceptance.configure')
    request = WorkflowAcceptanceEdit.model_validate(args)
    # Keep the base check and publication in one transaction, so stale partial edits
    # cannot overwrite a concurrent publication. Existing task revisions are immutable.
    with ctx.store.tx(immediate=True):
        base = ctx.store.get_workflow(ctx.project_id)
        if (base['id'], base['definition_hash']) != (request.base_revision_id, request.base_digest):
            raise ValueError('WorkflowRevisionChanged: inspect the current revision before configuring acceptance')
        _check_workflow_scope(ctx, base['id'])
        current = WorkflowDefinition.model_validate(base['definition'])
        updated, changed_columns = request.apply(current)
        method = WorkflowPlan.model_validate(ctx.store.get_workflow_plan(ctx.project_id, base['workflow_plan_id'])['plan'])
        definition = canonicalize_workflow_model(updated, registry)
        errors = acceptance_diagnostics(definition, method)
        impossible = [item for item in errors if item['message'].startswith('FeedbackContractMissing:')]
        if impossible:
            raise PlanAdmissionError(impossible)
        result = _workflow_publish(
            WorkflowPublishInput(workflow_plan_id=base['workflow_plan_id'], workflow=definition).model_dump(mode='json'),
            ctx, registry,
        )
        return WorkflowAcceptanceConfigureReceipt(
            id=result['id'], definition_hash=result['definition_hash'],
            workflow_plan_id=result['workflow_plan_id'], changed_columns=changed_columns,
            ready_for_task_plan=not errors, remaining_diagnostic_count=len(errors),
            remaining_diagnostics=errors[:WORKFLOW_ACCEPTANCE_DIAGNOSTIC_LIMIT],
        ).model_dump(mode='json')


def _workflow_create(args: dict[str, Any], ctx: CapabilityContext, registry: CapabilityRegistry) -> dict[str, Any]:
    """Materialize one author-owned graph and derive its non-routing Plan facts."""
    _require_user_planning_turn(ctx, "workflow.create")
    draft = WorkflowCreateInput.model_validate(args).workflow
    workflow, plan = materialize_workflow(draft)
    workflow = canonicalize_workflow_model(workflow, registry)
    validate_workflow_capabilities(workflow, registry)
    check_schema(draft.task_input_schema, label="Workflow Task input_schema")
    for column in workflow.columns:
        check_schema(column.input_contract, label=f"Column {column.key} input_contract")
        check_schema(column.output_contract, label=f"Column {column.key} output_contract")
    saved = ctx.store.create_workflow_plan(ctx.project_id, plan)
    _bind_requirement(ctx, "v1_workflow_plans", saved["id"])
    return _workflow_publish(WorkflowPublishInput(
        workflow_plan_id=saved["id"], workflow=workflow).model_dump(mode="json"),
        ctx, registry, planning_authorized=True)


def _workflow_publish(args: dict[str, Any], ctx: CapabilityContext, registry: CapabilityRegistry,
                      *, planning_authorized: bool = False) -> dict[str, Any]:
    if not planning_authorized:
        _require_user_planning_turn(ctx, "workflow.publish")
    _bind_requirement(ctx, 'v1_workflow_plans', str(args['workflow_plan_id']))
    workflow = WorkflowDefinition.model_validate(
        canonicalize_workflow_capability_arguments(args["workflow"], registry)
    )
    result = ctx.store.publish_workflow(ctx.project_id, workflow, str(args["workflow_plan_id"]))
    scope = ctx.store.scopes.for_workflow(result['id'])
    ctx.store.scopes.bind_workflow(result['id'], scope['binding_id'], ctx.requirement_id, ctx.requirement_revision)
    return result


def _task_create(
    args: dict[str, Any],
    ctx: CapabilityContext,
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "task.create")
    _bind_requirement(ctx, 'v1_task_plans', str(args['task_plan_id']))
    cause = ctx.store.agents.planning_cause(ctx)
    plan = ctx.store.get_task_plan(ctx.project_id, str(args['task_plan_id']))['plan']
    _check_workflow_scope(ctx, plan['workflow_revision_id'])
    proposed = next((item for item in plan['tasks'] if item['proposed_task_ref'] == args['proposed_task_ref']), None)
    if proposed is None:
        raise ValueError('Task reference does not belong to the plan')
    fingerprint_payload = TaskFingerprint(workflow=plan['workflow_revision_id'], task=proposed)
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    existing = ctx.store.agents.caused_task(ctx.requirement_id, cause, args['proposed_task_ref'], fingerprint)
    if existing:
        return TaskReceipt.from_task(
            ctx.store.get_task(existing),
            TaskMaterializationReceipt(created_task_ids=[], reused_task_ids=[existing]),
        ).model_dump(mode="json")
    task = ctx.store.materialize_task_plan(
        ctx.project_id,
        task_plan_id=str(args["task_plan_id"]),
        proposed_task_ref=str(args["proposed_task_ref"]),
    )
    _bind_requirement(ctx, 'v1_tasks', task['id'])
    ctx.store.agents.record_caused_task(ctx.requirement_id, cause, args['proposed_task_ref'], fingerprint, task['id'])
    return TaskReceipt.from_task(
        ctx.store.get_task(task['id']),
        TaskMaterializationReceipt.model_validate(task['materialization']),
    ).model_dump(mode="json")


def _bind_requirement(ctx, table, identity):
    if not ctx.requirement_id:
        return
    assert table in {'v1_tasks', 'v1_task_plans', 'v1_workflow_plans'}
    with ctx.store.tx(immediate=True) as db:
        row = db.execute(f'SELECT * FROM {table} WHERE id=? AND project_id=?', (identity, ctx.project_id)).fetchone()
        if not row:
            label = {'v1_tasks':'Task','v1_task_plans':'TaskPlan','v1_workflow_plans':'WorkflowPlan'}[table]
            hint = (' Use the ID returned by task.plan.save; task.plan.validate does not save a plan.'
                    if table == 'v1_task_plans' else ' Use a saved ID returned by the corresponding planning tool.')
            raise ValueError(f'{label}NotFound: no saved {label} {identity!r} in this Project.'+hint)
        if row['requirement_id'] not in (None, ctx.requirement_id):
            if table == 'v1_workflow_plans':
                active = ctx.store.get_workflow(ctx.project_id)
                scope = ctx.store.scopes.for_workflow(active['id'])
                if (active['workflow_plan_id'] == identity and scope and
                        (scope['requirement_id'], scope['requirement_revision']) ==
                        (ctx.requirement_id, ctx.requirement_revision)):
                    return  # Reuse the current immutable method after a scope handover.
            raise ValueError('Work belongs to a different Requirement')
        if table != 'v1_workflow_plans':
            version = ctx.requirement_revision or ctx.store.agents.get_requirement(ctx.project_id, ctx.requirement_id)['revision']
            if row['requirement_revision'] not in (None, version):
                raise ValueError('Work belongs to a different Requirement revision; create a new TaskPlan')
            db.execute(f'UPDATE {table} SET requirement_id=?,requirement_revision=? WHERE id=?', (ctx.requirement_id, version, identity))
        else:
            db.execute(f'UPDATE {table} SET requirement_id=? WHERE id=?', (ctx.requirement_id, identity))


def _check_workflow_scope(ctx, workflow_revision_id):
    scope = ctx.store.scopes.for_workflow(workflow_revision_id)
    if scope and ctx.requirement_id and scope['requirement_id']:
        if (scope['requirement_id'], scope['requirement_revision']) != (ctx.requirement_id, ctx.requirement_revision):
            raise ValueError(
                'WorkflowScopeMismatch: Workflow belongs to Requirement '
                f'{scope["requirement_id"]} revision {scope["requirement_revision"]}, '
                f'but this turn uses {ctx.requirement_id} revision {ctx.requirement_revision}. '
                'Use conversation.execution.authorize(request=continue) for that existing scope in a new turn, '
                'or project.scope.revise to take over/publish a new Workflow revision in this authorized turn.')


def _agent_worker_send(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = WorkerSendInput.model_validate(args)
    return ctx.store.agents.send(
        ctx.project_id, request.worker_id, request.content,
        assignment_id=request.assignment_id, dedupe_key=request.dedupe_key,
    )


def _agent_worker_lifecycle(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = WorkerLifecycleInput.model_validate(args)
    return ctx.store.agents.set_lifecycle(ctx.project_id, request.worker_id, request.state)


def _agent_context_compact(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = AgentContextCompactInput.model_validate(args)
    return ctx.store.agents.save_context_snapshot(
        ctx.project_id, request.worker_id, request.through_message_id, request.summary,
    )


def _agent_context_read(args, ctx):
    request = AgentContextReadInput.model_validate(args)
    worker_id = request.worker_id or ctx.agent_instance_id
    if not worker_id or (ctx.column_run_id and worker_id != ctx.agent_instance_id):
        raise PermissionError('Leaf Workers may read only their own context')
    if {'snapshot_id', 'source_message_id'} & request.model_fields_set:
        if {'after_message_id', 'limit'} & request.model_fields_set:
            raise ValueError('Exact source mode cannot be combined with the message directory cursor')
        return [ctx.store.agents.context_source_page(ctx.project_id, worker_id,
            snapshot_id=request.snapshot_id,source_message_id=request.source_message_id,
            offset=request.offset,max_characters=request.max_characters,
            expected_sha256=request.expected_sha256)]
    if {'offset', 'max_characters', 'expected_sha256'} & request.model_fields_set:
        raise ValueError('Source offsets require snapshot_id or source_message_id')
    return ctx.store.agents.context_page(ctx.project_id, worker_id, after=request.after_message_id, limit=request.limit)


def _agent_evidence_list(args, ctx):
    request = AgentEvidenceListInput.model_validate(args)
    run_id = request.column_run_id or ctx.column_run_id
    if not run_id:
        raise ValueError('column_run_id is required outside a Column')
    if ctx.column_run_id and run_id != ctx.column_run_id:
        raise PermissionError('Leaf Workers may inspect only their current ColumnRun evidence')
    return ctx.store.agents.evidence_page(ctx.project_id, run_id,
        after=request.after_invocation_id, limit=request.limit,
        worker_id=ctx.agent_instance_id if ctx.column_run_id else None)


def _agent_result_read(args, ctx):
    request = AgentResultReadInput.model_validate(args)
    with ctx.store.connect() as db:
        source = db.execute('SELECT agent_instance_id FROM v1_agent_runs WHERE id=? AND project_id=?',
                            (request.agent_run_id, ctx.project_id)).fetchone()
        if not source or (ctx.column_run_id and source['agent_instance_id'] != ctx.agent_instance_id):
            raise ValueError('Tool result is absent or outside this Agent scope')
        row = db.execute('''SELECT t.result_json FROM v1_tool_invocations t
            JOIN v1_agent_runs r ON r.id=t.agent_run_id
            WHERE t.project_id=? AND t.agent_run_id=? AND t.tool_call_id=?
            AND (? IS NULL OR r.agent_instance_id=?) ORDER BY t.id DESC LIMIT 1''',
            (ctx.project_id,request.agent_run_id,request.tool_call_id,ctx.column_run_id,ctx.agent_instance_id)).fetchone()
        if not row:
            available = [dict(item) for item in db.execute('''SELECT tool_call_id,capability
                FROM v1_tool_invocations WHERE project_id=? AND agent_run_id=?
                ORDER BY id DESC LIMIT 8''', (ctx.project_id, request.agent_run_id))]
            return AgentResultReadReceipt(
                agent_run_id=request.agent_run_id, tool_call_id=request.tool_call_id,
                found=False, available=available, content="", next_offset=None,
            ).model_dump(mode="json", exclude_unset=True)
    content = row['result_json']
    if request.field != 'result':
        archived = ArchivedToolResult.model_validate_json(content)
        selected = ArchivedCommandOutput.model_validate(archived.output or {})
        content = getattr(selected, request.field)
    offset = request.offset
    limit = request.limit
    byte_limit = max(READ_LIMITS.archived_result_min_bytes,
                     min(READ_LIMITS.archived_result_max_bytes,
                         ctx.store.policy.context.tool_result_tokens *
                         READ_LIMITS.archived_result_byte_ratio_numerator //
                         READ_LIMITS.archived_result_byte_ratio_denominator))
    chunk, next_offset = slice_source_text(content, offset=offset,
        max_characters=limit, max_bytes=byte_limit)
    return AgentResultReadReceipt(
        agent_run_id=request.agent_run_id, tool_call_id=request.tool_call_id,
        field=request.field, offset=offset, content=chunk,
        total_characters=len(content), sha256=hashlib.sha256(content.encode('utf8')).hexdigest(),
        next_offset=next_offset,
    ).model_dump(mode="json", exclude_unset=True)


def _backlog_record(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = BacklogRecordInput.model_validate(args)
    return ctx.store.record_backlog(
        ctx.project_id, request.title, request.brief, request.readiness.model_dump(mode="json")
    )


def _scheduling_decide(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SchedulingDecisionInput.model_validate(args)
    supplied = request.model_fields_set
    return ctx.store.schedule_task(
        ctx.project_id, request.task_id, request.state, request.priority or 0,
        request.wip_group if "wip_group" in supplied else None,
        request.wip_limit if "wip_limit" in supplied else None,
        request.dependencies if "dependencies" in supplied else None,
        request.resources if "resources" in supplied else None,
    )


def _task_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    request = TaskListInput.model_validate(args)
    return ctx.store.list_tasks(ctx.project_id, request.limit or ctx.store.policy.service_limits.default_page_size)


def _task_inspect(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(TaskIdInput.model_validate(args).task_id)
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return TaskInspectReceipt(
        task=TaskProjection.model_validate(task),
        scheduling=SchedulingProjection.model_validate(ctx.store.task_scheduling(ctx.project_id, task["id"])),
        runs=[ColumnRunProjection.model_validate(item) for item in ctx.store.runs(ctx.project_id, task["id"])],
        attempts=[ColumnAttemptProjection.model_validate(item) for item in ctx.store.attempts(ctx.project_id, task["id"])],
        artifacts=[ArtifactProjection.model_validate(item) for item in ctx.store.artifacts(ctx.project_id, task["id"])],
    ).model_dump(mode="json", exclude_unset=True)


def _task_retry(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = TaskRetryInput.model_validate(args)
    task = ctx.store.get_task(request.task_id)
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return ctx.store.retry_task(
        task["id"],
        request.column_key or None,
        clear_context=bool(request.clear_context),
    )


def _task_reopen(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = TaskRetryInput.model_validate(args)
    task = ctx.store.get_project_task(ctx.project_id, request.task_id)
    return ctx.store.reopen_task(
        task["id"], request.column_key or None, clear_context=bool(request.clear_context)
    )


def _task_rerun(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, TaskIdInput.model_validate(args).task_id)
    return ctx.store.rerun_task(task["id"])


def _task_successor(args, ctx):
    _require_user_planning_turn(ctx, 'task.successor')
    request = TaskSuccessorInput.model_validate(args)
    task = ctx.store.get_project_task(ctx.project_id, request.task_id)
    if task['task_plan_id'] == request.task_plan_id:
        raise ValueError('task.successor requires a corrected TaskPlan; use task.rerun to repeat the original plan')
    _bind_requirement(ctx, 'v1_task_plans', request.task_plan_id)
    plan = ctx.store.get_task_plan(ctx.project_id, request.task_plan_id)['plan']
    _check_workflow_scope(ctx, plan['workflow_revision_id'])
    if not any(item['proposed_task_ref'] == task['proposed_task_ref'] for item in plan['tasks']):
        raise ValueError('Corrected TaskPlan must preserve the predecessor proposed_task_ref')
    return ctx.store.materialize_task_plan(ctx.project_id, task_plan_id=request.task_plan_id,
        proposed_task_ref=task['proposed_task_ref'], rerun_of_task_id=task['id'])


def _conversation_history(args, ctx):
    request = ConversationHistoryInput.model_validate(args)
    job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
    before = request.before(job['user_message_id'] if job else None)
    with ctx.store.connect() as db:
        rows = db.execute("""SELECT id,role,content FROM v1_conversations
            WHERE project_id=? AND id<? AND role IN ('user','assistant')
            AND COALESCE(json_extract(meta_json,'$.kind'),'')!='notification'
            ORDER BY id DESC LIMIT ?""", (ctx.project_id,before,request.limit)).fetchall()
    return ConversationHistoryReceipt(
        messages=[ConversationHistoryMessage.model_validate(dict(row)) for row in reversed(rows)],
        next_before_message_id=rows[-1]['id'] if rows else None,
    ).model_dump(mode="json")


def _resolve_current_turn(args, ctx):
    job = ctx.store.agents.assert_conversation(ctx)
    request = args if isinstance(args, ConversationTurnResolutionInput) else ConversationTurnResolutionInput.model_validate(args)
    # Preserve the original revision for an idempotent replay after resolution.
    with ctx.store.connect() as db:
        prior = db.execute('SELECT resolution_json FROM v1_turn_resolutions WHERE job_id=?', (job['id'],)).fetchone()
    revision = (PriorTurnRevision.model_validate_json(prior[0]).based_on_intent_revision
                if prior else ctx.store.intents.context(job)['revision'])
    bound = request.bind_host(job['user_message_id'], revision)
    return ctx.store.intents.resolve(ctx, bound.model_dump(mode='json'))


def _authorize_execution(args, ctx):
    job = ctx.store.agents.assert_conversation(ctx)
    if ctx.store.intents.contract(job).get('phase') == 'execution':
        with ctx.store.connect() as db:
            prior = db.execute('SELECT result_json FROM v1_turn_resolutions WHERE job_id=?',
                (job['id'],)).fetchone()
        if not prior:
            raise ValueError('ExecutionGrantMissing: committed turn has no resolution receipt')
        return AuthorizationReplayReceipt.from_saved(prior['result_json'])
    state = ctx.store.intents.context(job)
    action = json.loads(job.get('user_action_json') or 'null')
    return _resolve_current_turn(ConversationTurnResolutionInput(
        act='execute', execution_request=args['request'],
        focus='new_scope' if args.get('new_scope') else 'current',
        constraint_change='release_hold' if state.get('execution_hold') else 'retain',
        scope_summary=args.get('scope_summary') or '',
        requirement_id=args.get('requirement_id'),
        accepted_proposal_id=action['proposal_id'] if action else None,
    ), ctx)


def _authorize_task_control(args, ctx):
    return _resolve_current_turn(ConversationTurnResolutionInput(act='control',
        control_capability=args['action'], control_task_id=args['task_id']), ctx)


def _task_pause(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, TaskIdInput.model_validate(args).task_id)
    job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
    contract = ctx.store.intents.contract(job) if job else {}
    targeted = (contract.get('phase') == 'targeted_control' and
                contract.get('control_capability') == 'task.pause' and
                contract.get('control_task_id') == task['id'])
    same_scope_execution = (contract.get('phase') == 'execution' and
                            (ctx.requirement_id, ctx.requirement_revision) ==
                            (task['requirement_id'], task['requirement_revision']))
    if not (ctx.user_initiated and job and job['trigger_kind'] == 'user' and
            (targeted or same_scope_execution)):
        raise PermissionError('TaskPauseRequiresAuthorization: authorize this Task control or the active Requirement execution first')
    return ctx.store.pause_task(task["id"])


def _task_resume(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, TaskIdInput.model_validate(args).task_id)
    if task.get("supervision_action") == "startup_hold" and not ctx.user_initiated:
        raise ValueError(
            "A startup-held Task can only be resumed from a user-initiated Conversation turn"
        )
    return ctx.store.resume_task(task["id"])


def _task_fail(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = TaskFailureInput.model_validate(args)
    task = ctx.store.get_task(request.task_id)
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return ctx.store.route_task_to_failed(task["id"], request.reason)


def _event_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    request = EventListInput.model_validate(args)
    events = ctx.store.events(
        project_id=ctx.project_id,
        after=request.after,
        limit=request.limit or ctx.store.policy.service_limits.detail_page_size,
    )
    for event in events:
        if event.get("type") != "conversation.progress":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        event["data"] = {
            key: data.get(key)
            for key in (
                "agent_run_id", "conversation_job_id", "kind", "iteration", "sequence",
                "capability", "tool_call_id",
            )
            if data.get(key) is not None
        }
    return events


def _instruction_update(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return ctx.store.update_conversation_instruction(ctx.project_id, InstructionUpdateInput.model_validate(args).instruction)


def _schedule_supervision(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = SupervisionScheduleInput.model_validate(args)
    return ctx.store.schedule_review(ctx.project_id, request.reason, request.due_at)


def _record_governance_decision(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    request = GovernanceDecisionInput.model_validate(args)
    return ctx.store.record_governance_decision(
        ctx.project_id, request.kind.value, request.subject_id, request.decision, request.data,
    )


def resolve_references(value: Any, scope: dict[str, Any]) -> Any:
    """Resolve explicit JSON references without evaluating templates or code."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return _json_pointer(scope, str(value["$ref"]))
        return {key: resolve_references(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_references(item, scope) for item in value]
    return value


def _contains_runtime_reference(value: Any) -> bool:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return True
        return any(_contains_runtime_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_runtime_reference(item) for item in value)
    return False


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    current = document
    for token in _json_pointer_tokens(pointer):
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _json_pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError("$ref must be an absolute JSON Pointer")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        while index < len(raw):
            if raw[index] != "~":
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise ValueError("$ref contains an invalid JSON Pointer escape")
            index += 2
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def tool_result_json(
    result: ToolResult,
    max_chars: int | None = None,
    *,
    reference: dict[str, Any] | None = None,
) -> str:

    message = ToolResultMessage.from_result(result, reference)
    return message.model_dump_json(exclude={"evidence"} if message.evidence is None else None)
