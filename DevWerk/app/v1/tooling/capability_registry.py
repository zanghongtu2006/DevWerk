"""Capability execution, input/output validation and Agent visibility."""

from __future__ import annotations
import json
import hashlib
import logging
import threading
from dataclasses import dataclass, replace
from contextlib import contextmanager
from typing import Any, Callable, Iterable
from app.core.debug_trace import trace_json
from app.v1.contracts import validate_contract, validate_contract_template
from app.v1.domain import ToolResult
from app.v1.protocols.tools.core.capability_protocol import BuiltinCapability, CapabilityToolset, COMMAND_CAPABILITIES, ConversationPhase, ConversationTrigger, ExecutionReceiptStatus, FailureDisposition, MEMORY_CAPABILITIES, SideEffectKind, StructuredObjectParameter
from app.v1.protocols.tools.agent.capability_memory_contracts import MemorySupersedeInput, MemoryWriteInput
from app.v1.protocols.tools.workspace.capability_workspace_contracts import SUCCESS_EXIT_CODE
from app.v1.protocols.workflow.runtime_checkpoint_contracts import ExecutionCheckpoint
from app.v1.protocols.tools.core.tool_definition_contracts import FunctionToolDefinition, ToolFunctionDefinition
from app.v1.workspace.files import ProjectFiles, SystemFiles
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, V1RuntimePolicy
from app.v1.execution.execution_control import ExecutionControl, ExecutionOwnershipLost, ExecutionBudgetExceeded, ExecutionReplayUncertain
from app.v1.execution.execution_ledger import normalized_operation_arguments
from app.v1.tooling.capability_schema_adapter import declared_outcomes
from app.v1.protocols.tools.core.capability_catalog_contracts import CapabilityCatalogEntry
from app.v1.protocols.tools.planning.capability_workflow_models import WorkflowCapabilityContract, WorkflowToolId, workflow_tool_models
from app.v1.protocols.tools.agent.capability_conversation_contracts import EmptyInput

from app.v1.tooling.capability_handlers import (
    _contains_runtime_reference,
    _files_write_preflight,
    _memory_write_scope,
)

CapabilityHandler = Callable[[dict[str, Any], "CapabilityContext"], Any]
AvailabilityCheck = Callable[["CapabilityContext"], bool]
ArgumentPreflight = Callable[[dict[str, Any]], None]
trace_log = logging.getLogger("devwerk.capability.trace")


@dataclass(frozen=True)
class CapabilityContext:
    project_id: str
    project: dict[str, Any]
    store: Any
    agent_run_id: str | None = None
    task_id: str | None = None
    column_run_id: str | None = None
    column_attempt_id: str | None = None
    start_task: bool = True
    execution_key: str | None = None
    writable_paths: tuple[str, ...] | None = None
    user_initiated: bool = False
    execution_control: ExecutionControl | None = None
    assignment: dict[str, Any] | None = None
    agent_instance_id: str | None = None
    requirement_id: str | None = None
    requirement_revision: int | None = None
    workflow_revision_id: str | None = None
    conversation_session_id: str | None = None

    @property
    def files(self) -> ProjectFiles:
        return ProjectFiles(self.project["base_dir"], self.store.policy)

    @property
    def system_files(self) -> SystemFiles:
        return SystemFiles()

    @contextmanager
    def effect_guard(self):
        with self.store.tx(immediate=True):
            if self.execution_control:
                self.execution_control.check()
            if self.assignment:
                self.store.agents.assert_owner(self.assignment)
            yield
            if self.execution_control:
                self.execution_control.check()


@dataclass(frozen=True)
class CapabilityEntry:
    id: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: CapabilityHandler
    provider_input_schema: dict[str, Any] | None = None
    toolset: str = CapabilityToolset.CORE
    availability_check: AvailabilityCheck | None = None
    side_effect_kind: SideEffectKind = SideEffectKind.NONE
    parallel_safe: bool = False
    delegable_to_column: bool = True
    argument_preflight: ArgumentPreflight | None = None
    workflow_reference_fields: tuple[str, ...] = ()
    structured_object_parameters: tuple[StructuredObjectParameter, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "side_effect_kind", SideEffectKind(self.side_effect_kind))

    def tool_schema(self) -> dict[str, Any]:
        return FunctionToolDefinition(function=ToolFunctionDefinition(
            name=self.id, description=self.description,
            parameters=self.provider_input_schema or self.input_schema or EmptyInput.model_json_schema(),
        )).wire()


class _RollbackCapability(Exception):
    def __init__(self, result):
        self.result = result


class CapabilityRegistry:
    """Infrastructure-only capability registry used by every Agent and Column."""

    def __init__(self, policy: V1RuntimePolicy | None = None) -> None:
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self._entries: dict[str, CapabilityEntry] = {}
        self._lock = threading.RLock()
        self._bulk_registration = False

    def register(self, entry: CapabilityEntry) -> None:
        with self._lock:
            if entry.id in self._entries:
                raise ValueError(f"capability {entry.id!r} is already registered")
            self._entries[entry.id] = entry
            if (entry.delegable_to_column and not self._bulk_registration and
                    any(identity in self._entries for identity in WorkflowToolId)):
                self._refresh_workflow_tool_models()

    def _refresh_workflow_tool_models(self) -> None:
        contracts = [WorkflowCapabilityContract(
            id=entry.id,
            input_schema=entry.input_schema,
            supports_acceptance_check=entry.side_effect_kind.supports_acceptance_check,
            inline_provider_arguments=entry.toolset != CapabilityToolset.MCP,
        ) for entry in self._entries.values() if entry.delegable_to_column]
        models = workflow_tool_models(contracts)
        for identity, model, provider_model in (
            (WorkflowToolId.CREATE, models.create_runtime, models.create),
            (WorkflowToolId.PUBLISH, models.publish, None),
        ):
            current = self._entries.get(identity)
            if current is not None:
                self._entries[identity] = replace(
                    current,
                    input_schema=model.model_json_schema(),
                    provider_input_schema=provider_model.model_json_schema() if provider_model else None,
                )

    def contains(self, capability_id: str) -> bool:
        with self._lock:
            return capability_id in self._entries

    def resolve(self, capability_ids: Iterable[str], context: CapabilityContext) -> list[CapabilityEntry]:
        resolved: list[CapabilityEntry] = []
        with self._lock:
            for capability_id in dict.fromkeys(capability_ids):
                entry = self._entries.get(capability_id)
                if entry is None:
                    raise ValueError(f"unknown capability: {capability_id}")
                if entry.availability_check and not entry.availability_check(context):
                    continue
                resolved.append(entry)
        return resolved

    def schemas(self, capability_ids: Iterable[str], context: CapabilityContext) -> list[dict[str, Any]]:
        return [entry.tool_schema() for entry in self.resolve(capability_ids, context)]

    def dispatch(self, capability_id: str, arguments: dict[str, Any], context: CapabilityContext) -> ToolResult:
        if not context.column_run_id:
            binding = context.store.intents.job_for_run(context.agent_run_id, context.project_id)
            if binding:
                contract = context.store.intents.contract(binding)
                context = replace(context, requirement_id=binding['requirement_id'], requirement_revision=binding['requirement_revision'],
                                  start_task=contract['phase'] in {ConversationPhase.EXECUTION, ConversationPhase.TARGETED_CONTROL} or binding['trigger_kind'] != ConversationTrigger.USER,
                                  user_initiated=binding['trigger_kind'] == ConversationTrigger.USER)
        entry = self._entries.get(capability_id)
        denied = context.store.intents.denial(context, capability_id, arguments, entry.side_effect_kind if entry else 'control')
        if denied:
            return ToolResult(ok=False, capability=capability_id,
                              error={'type': denied.split(':')[0], 'message': denied},
                              checkpoint={'failure_disposition': FailureDisposition.REJECTED_BEFORE_EFFECT.value})
        if entry and context.column_run_id and not entry.delegable_to_column:
            return ToolResult(ok=False, capability=capability_id,
                              error={"type": "LeafCapabilityDenied", "message": "A Column executes one Worker; this capability belongs to the project Conversation Agent"},
                              checkpoint={"failure_disposition": FailureDisposition.REJECTED_BEFORE_EFFECT.value})
        # All core control handlers commit through Store transactions/savepoints.
        # Keep caller fencing, business mutation and receipt in ONE transaction.
        if entry and (entry.side_effect_kind.uses_control_transaction or capability_id in MEMORY_CAPABILITIES):
            try:
                with context.effect_guard():
                    denied = context.store.intents.denial(context, capability_id, arguments, entry.side_effect_kind)
                    if denied:
                        raise _RollbackCapability(ToolResult(ok=False, capability=capability_id,
                            error={'type': denied.split(':')[0], 'message': denied}))
                    result = self._dispatch(capability_id, arguments, context)
                    if not result.ok:
                        raise _RollbackCapability(result)
                    return result
            except _RollbackCapability as exc:
                # A handler may write before discovering invalid input. Its
                # failed ToolResult must roll back business state and events too.
                checkpoint = ExecutionCheckpoint.read(exc.result.checkpoint)
                checkpoint.failure_disposition = FailureDisposition.REJECTED_BEFORE_EFFECT
                return exc.result.model_copy(update={"checkpoint": checkpoint.wire()})
        return self._dispatch(capability_id, arguments, context)

    def _dispatch(self, capability_id: str, arguments: dict[str, Any], context: CapabilityContext) -> ToolResult:
        receipt = None
        trace_json(
            trace_log,
            "capability.input",
            capability=capability_id,
            project_id=context.project_id,
            task_id=context.task_id,
            agent_run_id=context.agent_run_id,
            column_run_id=context.column_run_id,
            column_attempt_id=context.column_attempt_id,
            execution_key=context.execution_key,
            arguments=arguments,
        )
        try:
            if context.execution_control:
                context.execution_control.check()
            entry = self.resolve([capability_id], context)[0]
            for parameter in entry.structured_object_parameters:
                arguments = parameter.normalize(arguments)
            self.validate_arguments(capability_id, arguments)
            arguments = normalized_operation_arguments(capability_id, arguments)
            if capability_id == BuiltinCapability.PROJECT_FILE_WRITE:
                _files_write_preflight(arguments, context)
            if capability_id in COMMAND_CAPABILITIES:
                from app.v1.workspace.command_resolution import resolve_command
                files = context.files if capability_id == BuiltinCapability.PROJECT_COMMAND_RUN else context.system_files
                resolve_command(arguments['argv'], files.resolve(str(arguments.get('cwd') or '.')))
            if capability_id == BuiltinCapability.MEMORY_WRITE:
                _memory_write_scope(MemoryWriteInput.model_validate(arguments), context)
            if capability_id == BuiltinCapability.MEMORY_SUPERSEDE:
                _memory_write_scope(MemorySupersedeInput.model_validate(arguments).replacement, context)
            if context.agent_run_id and not context.column_run_id and entry.side_effect_kind.mutates_workspace:
                with context.store.connect() as db:
                    busy = db.execute("SELECT 1 FROM v1_tasks WHERE project_id=? AND (status IN ('running','waiting') OR failure_code='effect_outcome_unknown') LIMIT 1",
                                      (context.project_id,)).fetchone()
                if busy:
                    raise ValueError("Project workspace is owned by an active Workflow; wait for it to stop before direct execution")
            digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            execution_key = context.execution_key or f"adhoc:{capability_id}:{digest}"
            receipt = context.store.start_execution_receipt(context.project_id, execution_key, capability_id, arguments,
                                                            retry_failed=not execution_key.startswith("op:") and ":effect:" not in execution_key)
            if receipt["status"] == ExecutionReceiptStatus.COMPLETED:
                if capability_id in COMMAND_CAPABILITIES and (receipt.get("result") or {}).get("exit_code") != SUCCESS_EXIT_CODE:
                    return ToolResult(ok=False, capability=capability_id, output=receipt.get("result"),
                                      error={"type": "LegacyCommandFailed", "message": "Historical command receipt has a nonzero exit code"})
                result = ToolResult(ok=True, capability=capability_id, output=receipt["result"])
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, receipt_reused=True, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == ExecutionReceiptStatus.AWAITING:
                result = ToolResult.model_validate(receipt["result"])
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, receipt_reused=True, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == ExecutionReceiptStatus.STARTED and not receipt.get("claimed", False):
                if entry.side_effect_kind.may_have_uncommitted_effect:
                    raise ExecutionReplayUncertain("Previous side effect has no committed outcome; reconcile it before retrying")
                result = ToolResult(ok=False, capability=capability_id, error={"type": "ExecutionInProgress", "message": "an execution receipt already owns this side effect"})
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == ExecutionReceiptStatus.FAILED:
                return ToolResult(ok=False, capability=capability_id, output=receipt.get("result"),
                                  error={"type": "PriorExecutionFailed", "message": receipt.get("error") or "Prior execution failed"})
            output = entry.handler(arguments, context)
            if context.execution_control:
                context.execution_control.check()
            result = output if isinstance(output, ToolResult) else ToolResult(ok=True, capability=capability_id, output=output)
            if result.capability != capability_id:
                raise ValueError("CapabilityResult capability must match the dispatched capability")
            if result.status == ExecutionReceiptStatus.COMPLETED:
                validate_contract(result.output, entry.output_schema, label=f"{capability_id} output")
                with context.effect_guard():
                    context.store.finish_execution_receipt(context.project_id, execution_key, True, result.output, None)
            elif result.status == ExecutionReceiptStatus.AWAITING:
                # Runtime commits the receipt together with the AwaitHandle and Attempt checkpoint.
                pass
            else:
                with context.effect_guard():
                    context.store.finish_execution_receipt(
                        context.project_id, execution_key, False, result.output,
                        str((result.error or {}).get("message") or "capability failed"),
                    )
            trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, result=result.model_dump(mode="json"))
            return result
        except (ExecutionOwnershipLost, ExecutionBudgetExceeded, ExecutionReplayUncertain):
            # An in-flight effect may have occurred; do not mark it safe to retry.
            raise
        except (KeyError, ValueError, FileNotFoundError, PermissionError) as exc:
            if receipt and receipt.get("claimed") and entry.side_effect_kind.mutates_workspace:
                raise ExecutionReplayUncertain(f"{capability_id} outcome is unknown after {type(exc).__name__}: {exc}") from exc
            if receipt and receipt.get("claimed"):
                context.store.finish_execution_receipt(context.project_id, receipt["execution_key"], False, None, f"{type(exc).__name__}: {exc}")
            trace_json(trace_log, "capability.error", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=context.execution_key, error_type=type(exc).__name__, error=str(exc))
            if context.agent_run_id:
                return ToolResult(
                    ok=False,
                    capability=capability_id,
                    error={"type": type(exc).__name__, "message": str(exc), "code": getattr(exc, 'error_code', None)},
                    checkpoint={"failure_disposition": (FailureDisposition.EXECUTION_FAILED if receipt and receipt.get("claimed") else FailureDisposition.REJECTED_BEFORE_EFFECT).value},
                )
            raise
        except Exception as exc:  # noqa: BLE001
            if receipt and receipt.get("claimed") and entry.side_effect_kind.may_have_uncommitted_effect:
                # Leave started durable: an exception after dispatch cannot prove
                # whether the external effect committed before the crash.
                raise ExecutionReplayUncertain(f"{capability_id} outcome is unknown after {type(exc).__name__}: {exc}") from exc
            if receipt and receipt.get("claimed"):
                context.store.finish_execution_receipt(context.project_id, receipt["execution_key"], False, None, f"{type(exc).__name__}: {exc}")
            trace_json(trace_log, "capability.error", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=context.execution_key, error_type=type(exc).__name__, error=str(exc))
            raise

    def all_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)

    def input_schema(self, capability_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
            return entry.input_schema

    def output_schema(self, capability_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
            return entry.output_schema

    def validate_arguments(self, capability_id: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
        validate_contract(arguments, entry.input_schema, label=f"{capability_id} input")
        if entry.argument_preflight:
            entry.argument_preflight(arguments)

    def validate_argument_template(self, capability_id: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
        validate_contract_template(
            arguments,
            entry.input_schema,
            label=f"{capability_id} input",
        )
        if not _contains_runtime_reference(arguments) and entry.argument_preflight:
            entry.argument_preflight(arguments)

    def validate_workflow_references(self, capability_id: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
        for field in entry.workflow_reference_fields:
            if field not in arguments:
                continue
            value = arguments[field]
            if not (isinstance(value, dict) and set(value) == {"$ref"}):
                raise ValueError(
                    f"{capability_id} argument {field!r} is an exact Workflow value and "
                    "must be supplied through a Runtime $ref"
                )

    def outcome_tokens(self, capability_id: str) -> tuple[str, ...]:
        """Return business decision tokens declared by a capability output."""
        with self._lock:
            entry = self._entries.get(capability_id)
            if entry is None:
                raise ValueError(f"unknown capability: {capability_id}")
            return declared_outcomes(entry.output_schema)

    def side_effect_kind(self, capability_id: str) -> str:
        with self._lock:
            entry = self._entries.get(capability_id)
            return entry.side_effect_kind.value if entry is not None else SideEffectKind.CONTROL.value

    def column_ids(self) -> list[str]:
        with self._lock:
            return sorted(item.id for item in self._entries.values() if item.delegable_to_column)

    def column_catalog(self, context: CapabilityContext) -> list[dict[str, Any]]:
        return [
            CapabilityCatalogEntry(id=entry.id, description=entry.description,
                input_schema=entry.input_schema, output_schema=entry.output_schema,
                side_effect_kind=entry.side_effect_kind.value,
                parallel_safe=entry.parallel_safe).model_dump(mode="json")
            for entry in self.resolve(self.column_ids(), context)
        ]
