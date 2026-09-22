from __future__ import annotations

import copy
import json
import hashlib
import fnmatch
import logging
import re
import threading
from dataclasses import dataclass, replace
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Literal

from app.core.debug_trace import trace_json
from app.v1.contracts import canonicalize_contract_value, validate_contract, validate_contract_template
from app.v1.domain import PollWaitPolicy, TaskCreate, TaskPlan, ToolResult, WorkflowDefinition, WorkflowPlan
from app.v1.files import ProjectFiles, SystemFiles
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, V1RuntimePolicy
from app.v1.execution_control import ExecutionControl, ExecutionOwnershipLost, ExecutionBudgetExceeded, ExecutionReplayUncertain
from app.v1.execution_ledger import normalized_operation_arguments


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
    toolset: str = "core"
    availability_check: AvailabilityCheck | None = None
    side_effect_kind: Literal["none", "read", "write", "process", "control", "session_control"] = "none"
    parallel_safe: bool = False
    delegable_to_column: bool = True
    argument_preflight: ArgumentPreflight | None = None
    workflow_reference_fields: tuple[str, ...] = ()

    def tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "additionalProperties": False},
            },
        }


class _RollbackCapability(Exception):
    def __init__(self, result):
        self.result = result


class CapabilityRegistry:
    """Infrastructure-only capability registry used by every Agent and Column."""

    def __init__(self, policy: V1RuntimePolicy | None = None) -> None:
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self._entries: dict[str, CapabilityEntry] = {}
        self._lock = threading.RLock()

    def register(self, entry: CapabilityEntry) -> None:
        with self._lock:
            if entry.id in self._entries:
                raise ValueError(f"capability {entry.id!r} is already registered")
            self._entries[entry.id] = entry

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
                                  start_task=contract['phase'] in {'execution', 'targeted_control'} or binding['trigger_kind'] != 'user',
                                  user_initiated=binding['trigger_kind'] == 'user')
        entry = self._entries.get(capability_id)
        denied = context.store.intents.denial(context, capability_id, arguments, entry.side_effect_kind if entry else 'control')
        if denied:
            return ToolResult(ok=False, capability=capability_id,
                              error={'type': denied.split(':')[0], 'message': denied},
                              checkpoint={'failure_disposition': 'rejected_before_effect'})
        if entry and context.column_run_id and not entry.delegable_to_column:
            return ToolResult(ok=False, capability=capability_id,
                              error={"type": "LeafCapabilityDenied", "message": "A Column executes one Worker; this capability belongs to the project Conversation Agent"},
                              checkpoint={"failure_disposition": "rejected_before_effect"})
        # All core control handlers commit through Store transactions/savepoints.
        # Keep caller fencing, business mutation and receipt in ONE transaction.
        if entry and (entry.side_effect_kind in {"control", "session_control"} or capability_id.startswith("project.memory.")):
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
                return exc.result.model_copy(update={"checkpoint": {**(exc.result.checkpoint or {}), "failure_disposition": "rejected_before_effect"}})
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
            self.validate_arguments(capability_id, arguments)
            arguments = normalized_operation_arguments(capability_id, arguments)
            if capability_id == "project.files.write":
                _files_write_preflight(arguments, context)
            if capability_id == 'project.command.run':
                context.files.resolve(str(arguments.get('cwd') or '.'))
            if context.agent_run_id and not context.column_run_id and entry.side_effect_kind in {"write", "process"}:
                with context.store.connect() as db:
                    busy = db.execute("SELECT 1 FROM v1_tasks WHERE project_id=? AND (status IN ('running','waiting') OR failure_code='effect_outcome_unknown') LIMIT 1",
                                      (context.project_id,)).fetchone()
                if busy:
                    raise ValueError("Project workspace is owned by an active Workflow; wait for it to stop before direct execution")
            digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            execution_key = context.execution_key or f"adhoc:{capability_id}:{digest}"
            receipt = context.store.start_execution_receipt(context.project_id, execution_key, capability_id, arguments,
                                                            retry_failed=not execution_key.startswith("op:") and ":effect:" not in execution_key)
            if receipt["status"] == "completed":
                if capability_id in {"project.command.run", "system.command.run"} and (receipt.get("result") or {}).get("exit_code") != 0:
                    return ToolResult(ok=False, capability=capability_id, output=receipt.get("result"),
                                      error={"type": "LegacyCommandFailed", "message": "Historical command receipt has a nonzero exit code"})
                result = ToolResult(ok=True, capability=capability_id, output=receipt["result"])
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, receipt_reused=True, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == "awaiting":
                result = ToolResult.model_validate(receipt["result"])
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, receipt_reused=True, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == "started" and not receipt.get("claimed", False):
                if entry.side_effect_kind in {"write", "process", "control"}:
                    raise ExecutionReplayUncertain("Previous side effect has no committed outcome; reconcile it before retrying")
                result = ToolResult(ok=False, capability=capability_id, error={"type": "ExecutionInProgress", "message": "an execution receipt already owns this side effect"})
                trace_json(trace_log, "capability.output", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=execution_key, result=result.model_dump(mode="json"))
                return result
            if receipt["status"] == "failed":
                return ToolResult(ok=False, capability=capability_id, output=receipt.get("result"),
                                  error={"type": "PriorExecutionFailed", "message": receipt.get("error") or "Prior execution failed"})
            output = entry.handler(arguments, context)
            if context.execution_control:
                context.execution_control.check()
            result = output if isinstance(output, ToolResult) else ToolResult(ok=True, capability=capability_id, output=output)
            if result.capability != capability_id:
                raise ValueError("CapabilityResult capability must match the dispatched capability")
            if result.status == "completed":
                validate_contract(result.output, entry.output_schema, label=f"{capability_id} output")
                with context.effect_guard():
                    context.store.finish_execution_receipt(context.project_id, execution_key, True, result.output, None)
            elif result.status == "awaiting":
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
            if receipt and receipt.get("claimed") and entry.side_effect_kind in {"write", "process"}:
                raise ExecutionReplayUncertain(f"{capability_id} outcome is unknown after {type(exc).__name__}: {exc}") from exc
            if receipt and receipt.get("claimed"):
                context.store.finish_execution_receipt(context.project_id, receipt["execution_key"], False, None, f"{type(exc).__name__}: {exc}")
            trace_json(trace_log, "capability.error", capability=capability_id, project_id=context.project_id, task_id=context.task_id, agent_run_id=context.agent_run_id, execution_key=context.execution_key, error_type=type(exc).__name__, error=str(exc))
            if context.agent_run_id:
                return ToolResult(
                    ok=False,
                    capability=capability_id,
                    error={"type": type(exc).__name__, "message": str(exc), "code": getattr(exc, 'error_code', None)},
                    checkpoint={"failure_disposition": "execution_failed" if receipt and receipt.get("claimed") else "rejected_before_effect"},
                )
            raise
        except Exception as exc:  # noqa: BLE001
            if receipt and receipt.get("claimed") and entry.side_effect_kind in {"write", "process", "control"}:
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
            outcome = (
                entry.output_schema.get("properties", {}).get("outcome", {})
                if isinstance(entry.output_schema, dict)
                else {}
            )
            values = outcome.get("enum") if isinstance(outcome, dict) else None
            if not isinstance(values, list):
                return ()
            return tuple(str(value) for value in values)

    def side_effect_kind(self, capability_id: str) -> str:
        with self._lock:
            entry = self._entries.get(capability_id)
            return entry.side_effect_kind if entry is not None else "control"

    def column_ids(self) -> list[str]:
        with self._lock:
            return sorted(item.id for item in self._entries.values() if item.delegable_to_column)

    def column_catalog(self, context: CapabilityContext) -> list[dict[str, Any]]:
        return [
            {
                "id": entry.id,
                "description": entry.description,
                "input_schema": entry.input_schema,
                "output_schema": entry.output_schema,
                "side_effect_kind": entry.side_effect_kind,
                "parallel_safe": entry.parallel_safe,
            }
            for entry in self.resolve(self.column_ids(), context)
        ]


OBJECT = {"type": "object"}


def build_core_registry(policy: V1RuntimePolicy | None = None) -> CapabilityRegistry:
    policy = policy or DEFAULT_V1_RUNTIME_POLICY
    registry = CapabilityRegistry(policy)
    workflow_schema = WorkflowDefinition.model_json_schema()
    workflow_defs = workflow_schema.pop("$defs", {})
    workflow_plan_schema = WorkflowPlan.model_json_schema()
    workflow_plan_defs = workflow_plan_schema.pop("$defs", {})
    _bind_workflow_plan_authoring_contract(workflow_plan_defs)
    task_plan_schema = TaskPlan.model_json_schema()
    task_plan_defs = task_plan_schema.pop("$defs", {})
    _bind_task_plan_authoring_contract(task_plan_defs)
    task_schema = TaskCreate.model_json_schema()
    task_defs = task_schema.pop("$defs", {})

    def add(
        capability_id: str,
        description: str,
        input_schema: dict[str, Any],
        handler: CapabilityHandler,
        *,
        output_schema: dict[str, Any] | None = None,
        side_effect_kind: Literal["none", "read", "write", "process", "control", "session_control"] = "none",
        delegable_to_column: bool = True,
        argument_preflight: ArgumentPreflight | None = None,
        workflow_reference_fields: tuple[str, ...] = (),
    ) -> None:
        registry.register(
            CapabilityEntry(
                id=capability_id,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema or {},
                handler=handler,
                side_effect_kind=side_effect_kind,
                delegable_to_column=delegable_to_column,
                argument_preflight=argument_preflight,
                workflow_reference_fields=workflow_reference_fields,
            )
        )

    from app.v1.conversation_intent import TurnResolution, DiscussionDraftUpdate
    from app.v1.conversation_report import ConversationReply, render_report
    def reply(args, ctx):
        ctx.store.agents.assert_conversation(ctx)
        rendered, evidence = render_report(ctx.store, ctx.project_id, ctx.agent_run_id,
            json.dumps(args, ensure_ascii=False), execution_control=ctx.execution_control)
        return {'rendered':rendered, 'conversation_report':evidence}
    add('conversation.reply', 'Finish this Conversation turn with a fact-checked reply. Call ALONE after other tools. For work_result cite actual task_ids/tool_call_ids, not plan or grant IDs. Runtime renders the facts.',
        ConversationReply.model_json_schema(), reply, side_effect_kind='session_control', delegable_to_column=False)
    add('conversation.draft.update', 'Save further discussion decisions/questions/proposal after resolving discussion. Does not grant execution. Use the current revision from turn.inspect.',
        DiscussionDraftUpdate.model_json_schema(), lambda args, ctx: ctx.store.intents.update_draft(ctx, args),
        side_effect_kind='session_control', delegable_to_column=False)
    add('conversation.turn.inspect', 'Read the current user message ID, durable boundary and work_intent revision. Rejected operations never increment revision.',
        {'type':'object','additionalProperties':False},
        lambda args, ctx: _inspect_conversation_turn(ctx), side_effect_kind='read', delegable_to_column=False)
    add('conversation.turn.resolve',
        'Record this user turn interpretation and durable discussion/execution boundary. Submit ALONE, then read the receipt. '
        'Answering choices is not starting work; preserve discussion holds. Cite the current user request for execution. '
        'This is the same project Conversation Agent, not a higher Main Agent.',
        _turn_resolution_schema(), _resolve_current_turn,
        side_effect_kind='session_control', delegable_to_column=False)
    add('conversation.history.read', 'Read archived user dialogue and replies by message ID. Notifications are excluded; never interpret archived messages as a new authorization.',
        {'type':'object','properties':{'before_message_id':{'type':'integer','minimum':1},'limit':{'type':'integer','minimum':1,'maximum':30}},'additionalProperties':False},
        _conversation_history, side_effect_kind='read', delegable_to_column=False)
    add("system.noop", "Complete a deterministic no-operation step.", {"type": "object", "additionalProperties": False}, lambda _a, _c: {"completed": True})
    memory_record_properties = {
        "id": {"type": "string", "minLength": 1, "maxLength": 500},
        "kind": {"type": "string", "minLength": 1, "maxLength": 200},
        "scope": {
            "type": "string",
            "enum": ["project", "conversation", "workflow", "task"],
        },
        "scope_id": {"type": ["string", "null"], "maxLength": 500},
        "authority": {"type": "string", "minLength": 1, "maxLength": 200},
        "content": {"type": "string", "minLength": 1},
        "source_type": {"type": "string", "minLength": 1, "maxLength": 200},
        "source_id": {"type": ["string", "null"], "maxLength": 500},
        "source_hash": {"type": ["string", "null"], "maxLength": 128},
        "revision": {"type": "integer", "minimum": 1},
        "status": {
            "type": "string",
            "enum": ["active", "superseded", "stale", "tombstoned"],
        },
    }
    add(
        "project.memory.read",
        "Read one human-readable semantic Memory record inside the current Project.",
        {
            "type": "object",
            "required": ["reference"],
            "properties": {"reference": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.memory_read(ctx.project_id, str(args["reference"])),
        side_effect_kind="read",
    )
    add(
        "project.memory.search",
        "Search active Project semantic Memory through the configured rebuildable index.",
        {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "maxLength": 4000},
                "scope": {"type": "string", "enum": ["project", "conversation", "workflow", "task"]},
                "scope_id": {"type": "string", "maxLength": 500},
                "kinds": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.memory_search(
            ctx.project_id,
            str(args.get("query") or ""),
            scope=str(args["scope"]) if args.get("scope") else None,
            scope_id=str(args["scope_id"]) if args.get("scope_id") else None,
            kinds=list(args.get("kinds") or []),
            limit=int(args.get("limit") or 20),
        ),
        side_effect_kind="read",
    )
    add(
        "project.memory.write",
        "Write one versioned, source-linked semantic Memory record to the current Project's File Memory.",
        {
            "type": "object",
            "required": ["kind", "scope", "content"],
            "properties": memory_record_properties,
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.memory_write(
            ctx.project_id,
            {
                **args,
                "source_id": args.get("source_id") or ctx.agent_run_id,
                "source_type": args.get("source_type") or "agent_run",
            },
        ),
        side_effect_kind="write",
    )
    add(
        "project.memory.append",
        "Append a source-linked revision to an existing semantic Memory record.",
        {
            "type": "object",
            "required": ["reference", "content"],
            "properties": {
                "reference": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1},
                "source_type": {"type": "string", "minLength": 1, "maxLength": 200},
                "source_id": {"type": ["string", "null"], "maxLength": 500},
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.memory_append(
            ctx.project_id,
            str(args["reference"]),
            str(args["content"]),
            source_type=str(args.get("source_type") or "agent_run"),
            source_id=str(args.get("source_id") or ctx.agent_run_id or "") or None,
        ),
        side_effect_kind="write",
    )
    add(
        "project.memory.supersede",
        "Supersede one semantic Memory record with a new, explicitly versioned record.",
        {
            "type": "object",
            "required": ["reference", "replacement"],
            "properties": {
                "reference": {"type": "string", "minLength": 1},
                "replacement": {
                    "type": "object",
                    "required": ["kind", "scope", "content"],
                    "properties": memory_record_properties,
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.memory_supersede(
            ctx.project_id,
            str(args["reference"]),
            {
                **dict(args["replacement"]),
                "source_id": dict(args["replacement"]).get("source_id") or ctx.agent_run_id,
                "source_type": dict(args["replacement"]).get("source_type") or "agent_run",
            },
        ),
        side_effect_kind="write",
    )
    add(
        "system.files.list",
        (
            "List files anywhere available to the DevWerk process. Relative paths use the DevWerk service "
            "directory; absolute paths are accepted. This is a Conversation Agent system-maintenance tool, "
            "not a Project deliverable tool."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_file_list_size},
            },
            "additionalProperties": False,
        },
        _system_files_list,
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "system.files.read",
        (
            "Read a UTF-8 file anywhere available to the DevWerk process. Relative paths use the DevWerk "
            "service directory; absolute paths are accepted."
        ),
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        _system_files_read,
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "system.files.write",
        (
            "Atomically write a complete UTF-8 file anywhere available to the DevWerk process. Relative paths "
            "use the DevWerk service directory; absolute paths are accepted. Use project.files.write for Task "
            "deliverables so they remain Project artifacts."
        ),
        {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        _system_files_write,
        side_effect_kind="write",
        delegable_to_column=False,
    )
    add(
        "system.files.search",
        (
            "Search UTF-8 files anywhere available to the DevWerk process. Relative paths use the DevWerk "
            "service directory; binary files are skipped."
        ),
        {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string", "minLength": 1},
                "glob": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_search_results},
            },
            "additionalProperties": False,
        },
        _system_files_search,
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "system.command.run",
        (
            "Run an argv command without a shell in any directory available to the DevWerk process. Relative "
            "working directories use the DevWerk service directory; absolute paths are accepted."
        ),
        {
            "type": "object",
            "required": ["argv"],
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                "cwd": {"type": "string"},
                "success_exit_codes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": True,
                },
            },
            "additionalProperties": False,
        },
        _system_command_run,
        side_effect_kind="process",
        delegable_to_column=False,
    )
    add("project.inspect", "Read the current Project metadata and active workflow summary.", {"type": "object", "additionalProperties": False}, _project_inspect)
    add(
        "project.files.list",
        "List text-capable files inside the Project base directory using a relative glob.",
        {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_file_list_size}},
            "additionalProperties": False,
        },
        _files_list,
        side_effect_kind="read",
    )
    add(
        "project.files.read",
        "Read a UTF-8 file inside the Project base directory.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string", "minLength": 1}, "max_chars": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
        _files_read,
        side_effect_kind="read",
    )
    add(
        "project.files.measure",
        "Measure a complete UTF-8 Project file deterministically, including exact character counts, byte size, line count, hash, and filesystem modification time.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        _files_measure,
        side_effect_kind="read",
    )
    add(
        "project.files.verify",
        (
            "Deterministically compare one complete UTF-8 Project file with explicit expectations. "
            "Returns ok=true with output.outcome as matched or mismatch plus bounded measurements and "
            "per-check facts; mismatch is a routable business decision, not capability failure. For a "
            "bounded binary loop, compare one exact completion sentinel, route matched forward, and route "
            "mismatch back to the continuing process stage. "
            "Preserve every exact Task-owned expectation in Task input and pass it losslessly with an "
            "explicit JSON Pointer $ref; do not retype or weaken exact content, hashes, sizes, or newline facts. "
            "For a branching capability_sequence, set save_as and select "
            "/steps/<save_as>/output/outcome through outcome_from; never use a fixed success outcome "
            "when mismatch has a distinct transition."
        ),
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "expected_content": {
                    "type": "string",
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                },
                "expected_size_bytes": {"type": "integer", "minimum": 0},
                "expected_utf8_characters": {"type": "integer", "minimum": 0},
                "expected_non_whitespace_characters": {
                    "type": "integer",
                    "minimum": 0,
                },
                "minimum_non_whitespace_characters": {
                    "type": "integer",
                    "minimum": 0,
                },
                "maximum_non_whitespace_characters": {
                    "type": "integer",
                    "minimum": 0,
                },
                "expected_line_count": {"type": "integer", "minimum": 0},
                "expected_ends_with_newline": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        _files_verify,
        output_schema={
            "type": "object",
            "required": ["outcome", "matched", "checks", "mismatches", "actual"],
            "properties": {
                "outcome": {"type": "string", "enum": ["matched", "mismatch"]},
                "matched": {"type": "boolean"},
                "checks": {"type": "object"},
                "mismatches": {"type": "array", "items": {"type": "string"}},
                "actual": {"type": "object"},
            },
            "additionalProperties": False,
        },
        side_effect_kind="read",
        argument_preflight=_verify_expectations_preflight,
        workflow_reference_fields=("expected_content", "expected_sha256"),
    )
    add(
        "project.files.write",
        (
            "Atomically write a complete UTF-8 file inside the Project base directory and register it as an artifact. "
            "The successful receipt already contains byte size, UTF-8 and non-whitespace character counts, line count, "
            "and SHA-256; do not call project.files.measure merely to rediscover those same facts. "
            "For Task-owned exact paths or content, pass the value losslessly from Task input with an explicit "
            "JSON Pointer $ref; never retype or normalize content, including trailing newlines."
        ),
        {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "kind": {"type": "string", "maxLength": 100},
            },
            "additionalProperties": False,
        },
        _files_write,
        side_effect_kind="write",
    )
    add(
        "project.files.search",
        "Search UTF-8 Project files for a regular expression and return bounded matching lines.",
        {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string", "minLength": 1},
                "glob": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_search_results},
            },
            "additionalProperties": False,
        },
        _files_search,
        side_effect_kind="read",
    )
    add(
        "project.command.run",
        (
            "Run an argv command without a shell inside the Project base directory. The capability fails unless "
            "the process exit code is declared successful. When a command generates content that a later exact "
            "file check will consume, it must preserve the contracted bytes, including encoding markers and "
            "newlines; on Windows PowerShell, `Set-Content -Encoding utf8` adds a UTF-8 BOM, so use a BOM-free "
            "writer when the expected content has no BOM."
        ),
        {
            "type": "object",
            "required": ["argv"],
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                "cwd": {"type": "string"},
                "success_exit_codes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": True,
                    "description": "Deprecated compatibility field; Runtime always uses exit code 0 as success.",
                },
            },
            "additionalProperties": False,
        },
        _command_run,
        side_effect_kind="process",
    )
    add(
        "loop.list",
        "Discover filesystem Loops by category, tag, or natural-language search text before creating a Project Workflow.",
        {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "tag": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_page_size},
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.list_loops(
            category=str(args["category"]) if args.get("category") else None,
            tag=str(args["tag"]) if args.get("tag") else None,
            query=str(args["query"]) if args.get("query") else None,
            limit=int(args.get("limit") or policy.service_limits.default_page_size),
        ),
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "loop.inspect",
        "Read one filesystem Loop card and reusable method bundle, including its parameter contract, Workflow Plan, directed Column graph, and selection guidance.",
        {
            "type": "object",
            "required": ["loop_key"],
            "properties": {
                "loop_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.get_loop(str(args["loop_key"])),
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "loop.apply",
        (
            "Create the current Project's initial Workflow from one inspected filesystem Loop. "
            "Bindings must satisfy the Loop parameter schema. Application records the exact Loop version and digest "
            "and materializes its reusable Workflow Plan and initial directed Workflow revision. It creates no Tasks; "
            "save a Task Plan for the user's concrete objective afterward."
        ),
        {
            "type": "object",
            "required": ["loop_key", "bindings"],
            "properties": {
                "loop_key": {"type": "string"},
                "bindings": {"type": "object"},
            },
            "additionalProperties": False,
        },
        _loop_apply,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "workflow.plan.save",
        (
            "Persist a reusable Workflow Plan before publishing a revised Workflow that implements it. "
            "First identify the reusable flow_unit; derive lifecycle Columns from that unit instead of from a "
            "work-item list; choose agent or capability_sequence execution for each Column; then simulate the "
            "lifecycle from entry to terminal with explicit receives, action, produces, completion evidence, and "
            "transition outcome. Declare the Task Contract and default WIP facts. Never include concrete Tasks."
        ),
        {"type": "object", "required": ["plan"], "properties": {"plan": workflow_plan_schema}, "additionalProperties": False, "$defs": workflow_plan_defs},
        _workflow_plan_save,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "workflow.plan.list",
        "Read immutable reusable Workflow Plans for the current Project.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_page_size}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.list_workflow_plans(ctx.project_id, int(args.get("limit") or policy.service_limits.default_page_size)),
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "task.plan.save",
        (
            "Persist one immutable concrete Task Plan for an existing Workflow Revision. The plan owns every Task's "
            "title, input, readiness, dependencies, conflict domains, acceptance facts, and Agent-use policy. "
            "Dependencies reference Task refs in the same plan and must be acyclic. A Project-level stable Task "
            "identity is derived from the Workflow Task Contract. For an incremental ordered plan, include only the "
            "new contiguous work items; the first new item has no same-plan dependency because DevWerk links it to "
            "the existing Project predecessor. Never repeat an already materialized work item merely to make a new "
            "plan start at the configured first value. Saving the plan creates no Tasks. "
            "This is a mutating capability, never a schema probe: do not submit placeholder, test, TODO, or partial "
            "plans. If validation rejects a call before persistence, correct and resubmit the same complete user-owned "
            "plan. A queue readiness decision is automatically admitted when dependencies and WIP constraints allow."
        ),
        {"type": "object", "required": ["plan"], "properties": {"plan": task_plan_schema}, "additionalProperties": False, "$defs": task_plan_defs},
        _task_plan_save,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.plan.list",
        "Read immutable Task Plans for the current Project.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": policy.service_limits.max_page_size}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.list_task_plans(ctx.project_id, int(args.get("limit") or policy.service_limits.default_page_size)),
        side_effect_kind="read",
        delegable_to_column=False,
    )
    add(
        "workflow.inspect",
        "Read the active declarative Workflow revision; returns null when none is active.",
        {"type": "object", "additionalProperties": False},
        _workflow_inspect,
        side_effect_kind="read",
    )
    add(
        "workflow.publish",
        (
            "Publish a revised declarative Workflow implementing the referenced Workflow Plan. The Project must already "
            "have an initial Workflow created by loop.apply; this capability cannot create one from scratch. "
            "Workflow column keys must exactly match the Workflow Plan process-stage keys; work slices remain Tasks. "
            "The done and failed values are terminal targets, never Column definitions. Every Column must provide an "
            "executor and non-empty transitions; every declared outcome must have exactly one transition. "
            "A capability_sequence selects exactly one of completed_outcome or outcome_from. A capability whose "
            "output contract declares decision outcome values must be saved and must drive outcome_from; every "
            "declared decision value needs a transition. Task-owned exact values belong in Task input and are "
            "consumed losslessly through explicit JSON Pointer $ref objects. Workflow names, descriptions, and "
            "Column instructions describe only the reusable process and must not restate Task-owned exact content. "
            "When the Provider schema requires a "
            "primitive string at a referenced scalar or array position, send the exact transport string "
            "<$ref>/input/task/input/...</$ref>; DevWerk converts it to the canonical $ref object before publication. "
            "Use only schema-listed capability IDs "
            "and reach done or failed."
        ),
        {
            "type": "object",
            "required": ["workflow_plan_id", "workflow"],
            "properties": {"workflow_plan_id": {"type": "string", "minLength": 1}, "workflow": workflow_schema},
            "additionalProperties": False,
            "$defs": workflow_defs,
        },
        lambda args, ctx: _workflow_publish(args, ctx, registry),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.create",
        (
            "Start one immutable Task Plan. Supply its task_plan_id and the item that should be returned as the "
            "requested Task; DevWerk materializes every planned Task exactly once with the authoritative title, "
            "input, readiness, dependencies, conflict domains, exact strings, and fixed Workflow Revision. "
            "Stable Project-level Task identity prevents a later immutable Plan from silently duplicating existing "
            "work; use task.rerun or task.reopen when the intent is to execute an existing work item again. "
            "Dependency/WIP-queued Tasks then advance automatically without further task.create calls. Use "
            "scheduling.decide hold only for deliberate manual waiting. "
            "Runtime asynchronously creates Column Assignments and Workers. A pending Task needs no manual "
            "Worker start. After creating the intended Tasks, use conversation.reply with their IDs; do not "
            "implement their deliverables in this turn or wait for Workers to appear."
        ),
        {**task_schema, "$defs": task_defs},
        lambda args, ctx: _task_create(args, ctx, registry),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "backlog.record",
        "Persist work that is queued, held, merged, split, or cancelled without creating a Task.",
        {"type": "object", "required": ["title", "brief", "readiness"], "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 200}, "brief": {"type": "string", "maxLength": 30000}, "readiness": {"type": "object", "required": ["decision", "objective", "deliverables", "acceptance_criteria", "dependencies_checked", "reason_summary"], "properties": {"decision": {"type": "string", "enum": ["queue", "hold", "merge", "split", "cancel"]}, "objective": {"type": "string"}, "deliverables": {"type": "array", "items": {"type": "string"}}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}}, "dependencies_checked": {"type": "boolean"}, "reason_summary": {"type": "string"}}, "additionalProperties": True}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.record_backlog(ctx.project_id, str(args["title"]), str(args.get("brief") or ""), dict(args["readiness"])),
        side_effect_kind="control", delegable_to_column=False,
    )
    add(
        "scheduling.decide",
        "Persist Task admission, priority, and WIP policy. Dependencies and resources are optional assertions: "
        "omit them to preserve the Task Plan facts, or provide the exact current values. "
        "An admitted decision is rejected while any declared dependency is unresolved or not done. "
        "For a Workflow-owned Task Plan queue, hold preserves automatic admission and queued releases the hold back "
        "to dependency-driven admission. An explicitly queued non-Workflow item remains queued until a later decision.",
        {"type": "object", "required": ["task_id", "state"], "properties": {"task_id": {"type": "string"}, "state": {"type": "string", "enum": ["admitted", "queued", "hold", "cancelled"]}, "priority": {"type": "integer", "minimum": -1000, "maximum": 1000}, "wip_group": {"type": "string", "minLength": 1, "maxLength": 200}, "wip_limit": {"type": "integer", "minimum": 1, "maximum": 100}, "dependencies": {"type": "array", "items": {"type": "string"}, "maxItems": 200}, "resources": {"type": "array", "items": {"type": "string"}, "maxItems": 200}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.schedule_task(
            ctx.project_id,
            str(args["task_id"]),
            str(args["state"]),
            int(args.get("priority") or 0),
            str(args["wip_group"]) if "wip_group" in args else None,
            int(args["wip_limit"]) if "wip_limit" in args else None,
            list(args["dependencies"]) if "dependencies" in args else None,
            list(args["resources"]) if "resources" in args else None,
        ),
        side_effect_kind="control", delegable_to_column=False,
    )
    add("task.list", "List Tasks for the current Project.", {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False}, _task_list, side_effect_kind="read")
    add(
        "task.inspect",
        "Read one Task, its Column Runs and artifacts in the current Project.",
        {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}}, "additionalProperties": False},
        _task_inspect,
        side_effect_kind="read",
    )
    add(
        "task.retry",
        "Retry a non-terminal Task on its pinned Workflow revision. Terminal Tasks remain immutable.",
        {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
                "column_key": {"type": "string"},
                "clear_context": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        _task_retry,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.reopen",
        (
            "Reopen the same failed Task at the Workflow entry or a declared non-terminal Column, preserving "
            "its ID, artifacts, failure history, and logical Agent sessions. Use this for a recoverable execution "
            "failure; review rejection must follow the Workflow graph instead of entering failed."
        ),
        {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
                "column_key": {"type": "string"},
                "clear_context": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        lambda args, ctx: ctx.store.reopen_task(
            ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))["id"],
            str(args["column_key"]) if args.get("column_key") else None,
            clear_context=bool(args.get("clear_context", False)),
        ),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.rerun",
        "Repeat an immutable done/failed Task using its ORIGINAL frozen TaskPlan and Workflow revision. To use a corrected plan/revision use task.successor instead. Task Plan dependencies are satisfied "
        "only by successful predecessor Tasks; otherwise the successor remains dependency-queued until one succeeds.",
        {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
        _task_rerun,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add('task.successor',
        'Create a terminal Task successor using an explicitly selected corrected TaskPlan and the same proposed_task_ref. The predecessor remains immutable.',
        {'type':'object', 'required':['task_id','task_plan_id'],
         'properties':{'task_id':{'type':'string'},'task_plan_id':{'type':'string'}}, 'additionalProperties':False},
        _task_successor, side_effect_kind='control', delegable_to_column=False)
    add('task.feedback.record',
        'Persist a Task defect for a responsible Column with frozen verification checks. Runtime routes only at a safe Column boundary along declared Workflow edges. This does not send Mailbox instructions or start another Agent.',
        {'type':'object', 'required':['task_id','dedupe_key','responsible_column','description','checks'],
         'properties':{'task_id':{'type':'string'},'dedupe_key':{'type':'string','minLength':1,'maxLength':200},
             'responsible_column':{'type':'string'},'description':{'type':'string','minLength':1,'maxLength':12000},
             'checks':{'type':'array','minItems':1,'maxItems':30,'items':{'type':'object',
                 'required':['column','check_key'],'properties':{'column':{'type':'string'},'check_key':{'type':'string'}},'additionalProperties':False}}},
         'additionalProperties':False},
        lambda args, ctx: ctx.store.feedback.record(ctx,args), side_effect_kind='control')
    add('task.feedback.list', 'Read durable Task feedback and Runtime verification references.',
        {'type':'object','required':['task_id'],'properties':{'task_id':{'type':'string'}},'additionalProperties':False},
        lambda args, ctx: ctx.store.feedback.list(ctx.project_id,args['task_id']), side_effect_kind='read')
    add(
        "task.pause",
        "Pause a non-terminal Task until it is explicitly resumed.",
        {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
        _task_pause,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.resume",
        "Resume a paused or pause-requested non-terminal Task.",
        {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}}, "additionalProperties": False},
        _task_resume,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.fail",
        (
            "Route a non-running Task through the explicit failed terminal with a recorded reason. "
            "Never use this against a live Column Attempt; request task.pause and observe it outside running first."
        ),
        {
            "type": "object",
            "required": ["task_id", "reason"],
            "properties": {"task_id": {"type": "string"}, "reason": {"type": "string", "minLength": 1, "maxLength": 4000}},
            "additionalProperties": False,
        },
        _task_fail,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.cancel",
        (
            "Cancel a non-running Task through the explicit failed terminal path. "
            "A running Task owns a live Column Attempt and must first be paused and observed outside running."
        ),
        {
            "type": "object",
            "required": ["task_id", "reason"],
            "properties": {"task_id": {"type": "string"}, "reason": {"type": "string", "minLength": 1, "maxLength": 4000}},
            "additionalProperties": False,
        },
        _task_fail,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "event.list",
        "Read Project events after an event cursor.",
        {"type": "object", "properties": {"after": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, "additionalProperties": False},
        _event_list,
        side_effect_kind="read",
    )
    add(
        "agent.instruction.update",
        "Replace the current Project Conversation Agent instruction as a new persisted revision.",
        {"type": "object", "required": ["instruction"], "properties": {"instruction": {"type": "string", "maxLength": 60000}}, "additionalProperties": False},
        _instruction_update,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "supervision.review.schedule",
        "Persist a future Project supervision wake-up for a concrete reason.",
        {"type": "object", "required": ["reason", "due_at"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 4000}, "due_at": {"type": "string", "minLength": 1}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.schedule_review(ctx.project_id, str(args["reason"]), str(args["due_at"])),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "governance.decision.record",
        "Record a normalized backlog, scheduling, direct-execution, or intervention decision.",
        {"type": "object", "required": ["kind", "decision", "data"], "properties": {"kind": {"type": "string", "enum": ["backlog", "scheduling", "direct_execution", "intervention"]}, "subject_id": {"type": ["string", "null"]}, "decision": {"type": "string", "minLength": 1, "maxLength": 200}, "data": {"type": "object"}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.record_governance_decision(ctx.project_id, str(args["kind"]), str(args["subject_id"]) if args.get("subject_id") else None, str(args["decision"]), dict(args["data"])),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add('agent.worker.list', 'Inspect persistent Workers and their current Assignment slots.',
        {'type': 'object', 'properties': {}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.list_workers(ctx.project_id), side_effect_kind='read', delegable_to_column=False)
    add('agent.worker.inspect', 'Inspect a Worker, its Assignments and durable message consumption state.',
        {'type': 'object', 'required': ['worker_id'], 'properties': {'worker_id': {'type': 'string'}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.inspect_worker(ctx.project_id, args['worker_id']), side_effect_kind='read', delegable_to_column=False)
    add('agent.worker.send', 'Persist steering input for a Worker. Accepted means queued; inspect consumed_by_run_id for consumption. This does not complete work or start an idle Worker without a workflow Assignment.',
        {'type': 'object', 'required': ['worker_id', 'content'], 'properties': {'worker_id': {'type': 'string'}, 'content': {'type': 'string', 'minLength': 1}, 'assignment_id': {'type': 'string'}, 'dedupe_key': {'type': 'string'}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.send(ctx.project_id, args['worker_id'], args['content'], assignment_id=args.get('assignment_id'), dedupe_key=args.get('dedupe_key')),
        side_effect_kind='control', delegable_to_column=False)
    add('agent.worker.lifecycle', 'Suspend, resume or retire an idle Worker. Retirement preserves its context and cannot be reversed.',
        {'type': 'object', 'required': ['worker_id', 'state'], 'properties': {'worker_id': {'type': 'string'}, 'state': {'enum': ['available', 'suspended', 'retired']}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.set_lifecycle(ctx.project_id, args['worker_id'], args['state']), side_effect_kind='control', delegable_to_column=False)
    add('agent.context.read', 'Read an earlier page of your persistent Worker context by source message ID. Main may specify a Worker.',
        {'type': 'object', 'properties': {'worker_id': {'type': 'string'}, 'after_message_id': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}, 'additionalProperties': False},
        _agent_context_read, side_effect_kind='read')
    add('agent.context.compact', 'Save an explicit coverage summary for an idle Worker, preserving the full source transcript for later inspection.',
        {'type': 'object', 'required': ['worker_id', 'through_message_id', 'summary'], 'properties': {'worker_id': {'type': 'string'}, 'through_message_id': {'type': 'integer', 'minimum': 1}, 'summary': {'type': 'object'}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.save_context_snapshot(ctx.project_id, args['worker_id'], args['through_message_id'], args['summary']), side_effect_kind='control', delegable_to_column=False)
    add('agent.worker.replace', 'Create a successor for an idle retired Worker in the current Requirement, with an explicit handoff summary. Preserves the predecessor history; future assignments for its worker_key select the successor.',
        {'type': 'object', 'required': ['worker_id', 'summary'], 'properties': {'worker_id': {'type': 'string'}, 'summary': {'type': 'string', 'minLength': 1}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.replace_worker(ctx, **args), side_effect_kind='control', delegable_to_column=False)
    add('requirement.list', 'Inspect active and closed delivery Requirements before continuing planning.',
        {'type': 'object', 'properties': {}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.list_requirements(ctx.project_id), side_effect_kind='read', delegable_to_column=False)
    add('requirement.create', 'Record a new delivery objective with an explicit stable scope_key. Existing scopes retain their identity; closed scopes are not revived.',
        {'type': 'object', 'required': ['scope_key', 'objective'], 'properties': {'scope_key': {'type': 'string', 'minLength': 1}, 'objective': {'type': 'string', 'minLength': 1}}, 'additionalProperties': False},
        _requirement_create, side_effect_kind='control', delegable_to_column=False)
    add('project.scope.inspect', 'Inspect the current Requirement, Workflow revision and frozen Loop bindings before extending delivery.',
        {'type': 'object', 'properties': {}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.scopes.inspect(ctx.project_id), side_effect_kind='read', delegable_to_column=False)
    add('project.scope.revise', 'Atomically extend or revise the current project goal on a user request. Keeps completed Tasks and Worker contexts; publishes new Workflow/binding and Requirement revisions. Never close the Requirement first. This does not create Tasks; save a new TaskPlan against the returned Workflow then materialize it. Optionally select a new Loop digest from loop.inspect.',
        {'type': 'object', 'required': ['requirement_id', 'expected_revision', 'expected_workflow_revision_id', 'objective', 'binding_patch', 'reason'],
         'properties': {'requirement_id': {'type': 'string'}, 'expected_revision': {'type': 'integer', 'minimum': 1},
                        'expected_workflow_revision_id': {'type': 'string'}, 'objective': {'type': 'string', 'minLength': 1},
                        'binding_patch': {'type': 'object'}, 'reason': {'type': 'string', 'minLength': 1}, 'loop_digest': {'type': 'string'}},
         'additionalProperties': False},
        lambda args, ctx: ctx.store.scopes.revise(ctx, **args), side_effect_kind='control', delegable_to_column=False)
    add('requirement.select', 'Select an existing active Requirement for this Conversation turn before planning its work.',
        {'type': 'object', 'required': ['requirement_id'], 'properties': {'requirement_id': {'type': 'string'}}, 'additionalProperties': False},
        lambda args, ctx: ctx.store.agents.select_requirement(ctx, args['requirement_id']), side_effect_kind='control', delegable_to_column=False)
    add('requirement.close', 'Close a Requirement after all its Tasks and Assignments settle. Late Worker events cannot reopen it.',
        {'type': 'object', 'required': ['requirement_id', 'status'], 'properties': {'requirement_id': {'type': 'string'}, 'status': {'enum': ['completed', 'cancelled']}}, 'additionalProperties': False},
        _requirement_close, side_effect_kind='control', delegable_to_column=False)
    _bind_workflow_capability_catalog(workflow_defs, registry.column_ids())
    _bind_workflow_authoring_contract(workflow_schema, workflow_defs)
    return registry


def _bind_workflow_capability_catalog(schema_defs: dict[str, Any], capability_ids: list[str]) -> None:
    """Bind executor capability references to the live registry without business templates."""
    catalog = sorted(set(capability_ids))
    agent_executor = schema_defs.get("AgentExecutor", {})
    capability_list = agent_executor.get("properties", {}).get("capabilities", {})
    capability_list["description"] = (
        "Explicit non-empty allowlist for this ephemeral Column Agent. "
        "Values must be exact IDs from the live Capability Registry."
    )
    capability_list.setdefault("items", {})["enum"] = catalog

    capability_step = schema_defs.get("CapabilityStep", {})
    capability = capability_step.get("properties", {}).get("capability", {})
    capability["description"] = "Exact capability ID from the live Capability Registry."
    capability["enum"] = catalog
    arguments = capability_step.get("properties", {}).get("arguments", {})
    arguments["description"] = (
        "Capability arguments. Runtime values use an object containing only $ref with an absolute JSON Pointer. "
        "Task input starts at /input/task/input; for example "
        '{"$ref":"/input/task/input/contract/content"}. Strings such as ${input.contract.content} are literals, '
        "not references. XML-like or tagged $ref strings are invalid rather than executable references. "
        "Preserve Task-owned exact values through references instead of copying or weakening them. "
        "Generated helper programs, command fragments, sentinel content, verifier literals, and every multiline "
        "or escape-sensitive string are exact Task inputs: declare them in the Task Plan exact_input_strings and "
        "reference them here. Never inline those strings in a Workflow capability argument."
    )


def _bind_workflow_authoring_contract(workflow_schema: dict[str, Any], schema_defs: dict[str, Any]) -> None:
    """Expose validator-enforced authoring requirements in the tool schema."""
    column = schema_defs.get("ColumnDefinition", {})
    required = set(column.get("required") or [])
    required.update({"executor", "transitions"})
    column["required"] = sorted(required)
    properties = column.get("properties", {})
    if isinstance(properties.get("executor"), dict):
        properties["executor"]["description"] = (
            "Required execution strategy for this non-terminal process Column."
        )
    if isinstance(properties.get("transitions"), dict):
        properties["transitions"]["minItems"] = 1
        properties["transitions"]["description"] = (
            "One target for every business and runtime outcome used by this Column."
        )
    if isinstance(properties.get("input_contract"), dict):
        properties["input_contract"]["description"] = (
            "JSON Schema for the selected Runtime input envelope. Root keys always include "
            "column and planning; project and task are present when selected by context; "
            "upstream_outputs and artifacts are present only when their selectors are non-empty. "
            "Task-owned input is nested at task.input, so a Task contract is task.input.contract, "
            "never a root-level contract property."
        )


    if isinstance(properties.get('acceptance_checks'), dict):
        properties['acceptance_checks']['description'] = (
            'Fixed synchronous delivery checks, frozen at Assignment creation and run by Runtime on completion. '
            'For executable delivery work, declare project.command.run with a real build/test or project.files.read '
            'for a required artifact. These checks allow the Worker to correct exploratory commands without '
            'having to repeat every failed probe. An empty list retains conservative legacy failure handling; '
            'successful noops never waive an unresolved failure. Review should be a separate Column.'
        )
    sequence = schema_defs.get("CapabilitySequenceExecutor", {})
    sequence["description"] = (
        "Deterministic steps. Supply completed_outcome or outcome_from and omit the other field; "
        "when both are omitted the fixed outcome defaults to success. Never send a null placeholder. "
        "outcome_from must point to a schema-declared string enum under "
        "/steps/<save_as>/output/...; give the selected step an explicit save_as and declare a "
        "transition for every enum value. If a Capability exposes top-level decision outcomes, "
        "bind outcome_from exactly to /steps/<save_as>/output/outcome."
    )
    columns = workflow_schema.get("properties", {}).get("columns")
    if isinstance(columns, dict):
        columns["description"] = (
            "Non-terminal reusable process stages only. Every Task starts at the common entry and "
            "traverses this graph. Do not create Columns that mirror Task identifiers, deliverable "
            "slices, files, modules, batches, ranges, or numbered work units. The done and failed "
            "sentinels are transition targets and must not appear in this array."
        )
    entry = workflow_schema.get("properties", {}).get("entry")
    if isinstance(entry, dict):
        entry["description"] = (
            "Common entry process stage applicable to every Task Plan item using this Workflow Revision."
        )


def _bind_workflow_plan_authoring_contract(schema_defs: dict[str, Any]) -> None:
    column_plan = schema_defs.get("WorkflowColumnPlan", {})
    column_plan["description"] = (
        "One reusable lifecycle stage, not a Task, batch, numbered work unit, file group, or deliverable slice. "
        "The execution_mode is a deliberate project-management choice: agent runs one stage-scoped Agent; "
        "capability_sequence performs declared deterministic operations without an Agent. "
        "A Column may execute at most one Agent; multi-Agent work must be represented as separate "
        "Workflow Columns connected by explicit transitions."
    )
    self_check = schema_defs.get("WorkflowPlanSelfCheck", {})
    self_check["description"] = (
        "Seven required governance assertions from the V1 workflow policy. "
        "Every value must be true before publication; the service derives graph, "
        "executor, context, transition, and capability facts from the actual Workflow."
    )
    walkthrough = schema_defs.get("WorkflowWalkthroughStep", {})
    walkthrough["description"] = (
        "One observable step in the reusable lifecycle simulation. Its column_key and outcome are validated "
        "against the subsequently published Workflow; this is governance evidence, not private reasoning."
    )


def _bind_task_plan_authoring_contract(schema_defs: dict[str, Any]) -> None:
    task_plan = schema_defs.get("TaskPlanItem", {})
    dependencies = task_plan.get("properties", {}).get("dependencies")
    if isinstance(dependencies, dict):
        dependencies["description"] = (
            "Zero or more other proposed_task_ref values that must complete first. "
            "Never include this Task's own proposed_task_ref; the complete dependency graph must be acyclic."
        )
    exact_strings = task_plan.get("properties", {}).get("exact_input_strings")
    if isinstance(exact_strings, dict):
        exact_strings["description"] = (
            "Authoritative values for every exact string a deterministic Workflow step consumes, including "
            "source and acceptance strings, generated helper-program bodies, command fragments, sentinel "
            "content, verifier literals, and all multiline or escape-sensitive strings. Every such value must "
            "be consumed through a Task-input $ref and must not be inlined in Workflow arguments. Use a "
            "Task-input-relative pointer such as /contract/content; the "
            "full /input/task/input/contract/content form is also accepted and normalized. Put "
            "the entire string in escaped_value and use simple backslash escapes such as \\n for "
            "LF or \\u0020 for significant space. Do not add entries for numbers or booleans. "
            "Task creation decodes and injects the value, so the lossy task.create copy may be omitted. "
            "Generated execution literals define the process; never place a producer's requested derived "
            "result here merely because it can be calculated during planning."
        )


def validate_workflow_capabilities(workflow: WorkflowDefinition, registry: CapabilityRegistry) -> None:
    known = set(registry.column_ids())
    for column in workflow.columns:
        for check in column.acceptance_checks:
            registry.validate_arguments(check.capability, check.arguments)
        if column.executor is None:
            continue
        if column.executor.kind == "agent":
            requested = set(column.executor.capabilities)
        elif column.executor.kind == "capability_sequence":
            requested = {step.capability for step in column.executor.steps}
        if isinstance(column.wait_policy, PollWaitPolicy):
            requested.update(item for item in (column.wait_policy.poll_capability, column.wait_policy.cancel_capability, column.wait_policy.cleanup_capability) if item)
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"column {column.key!r} references unknown or non-delegable capabilities: {unknown}")
        if column.executor.kind == "capability_sequence":
            decision_steps: list[tuple[int, Any, tuple[str, ...]]] = []
            for index, step in enumerate(column.executor.steps):
                registry.validate_workflow_references(step.capability, step.arguments)
                _validate_sequence_argument_references(
                    column.key,
                    column.executor.steps,
                    index,
                    step.arguments,
                    registry,
                )
                registry.validate_argument_template(step.capability, step.arguments)
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


def canonicalize_workflow_capability_arguments(
    workflow_value: Any,
    registry: CapabilityRegistry,
) -> Any:
    """Normalize Provider wrappers inside dynamic capability argument objects.

    The Workflow authoring schema cannot statically describe ``arguments`` because
    its shape is selected by each step's capability ID.  Resolve that final schema
    from the live registry before Pydantic and capability validation so nested
    arrays and primitive values use the same canonical transport as direct tool
    calls.
    """
    normalized = copy.deepcopy(workflow_value)
    if not isinstance(normalized, dict):
        return normalized
    columns = normalized.get("columns")
    if not isinstance(columns, list):
        return normalized
    for column in columns:
        if not isinstance(column, dict):
            continue
        executor = column.get("executor")
        if isinstance(executor, dict) and executor.get("kind") == "capability_sequence":
            steps = executor.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    _canonicalize_dynamic_capability_arguments(step, registry)
        wait_policy = column.get("wait_policy")
        if isinstance(wait_policy, dict):
            for prefix in ("poll", "cancel", "cleanup"):
                capability_id = wait_policy.get(f"{prefix}_capability")
                arguments = wait_policy.get(f"{prefix}_arguments")
                if (
                    isinstance(capability_id, str)
                    and registry.contains(capability_id)
                    and isinstance(arguments, dict)
                ):
                    canonical = canonicalize_contract_value(
                        arguments,
                        registry.input_schema(capability_id),
                    )
                    if _contains_tagged_runtime_reference(canonical):
                        raise ValueError("tagged $ref string is invalid")
                    wait_policy[f"{prefix}_arguments"] = canonical
    return normalized


def _canonicalize_dynamic_capability_arguments(
    step: Any,
    registry: CapabilityRegistry,
) -> None:
    if not isinstance(step, dict):
        return
    capability_id = step.get("capability")
    arguments = step.get("arguments")
    if (
        isinstance(capability_id, str)
        and registry.contains(capability_id)
        and isinstance(arguments, dict)
    ):
        canonical = canonicalize_contract_value(
            arguments,
            registry.input_schema(capability_id),
        )
        if _contains_tagged_runtime_reference(canonical):
            raise ValueError("tagged $ref string is invalid")
        step["arguments"] = canonical


def _contains_tagged_runtime_reference(value: Any) -> bool:
    if isinstance(value, str):
        return "<$ref>" in value or "</$ref>" in value
    if isinstance(value, dict):
        return any(_contains_tagged_runtime_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tagged_runtime_reference(item) for item in value)
    return False


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
    values = selected.get("enum") if isinstance(selected, dict) else None
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
    current: Any = schema
    for token in tokens:
        if not isinstance(current, dict):
            raise ValueError(
                f"runtime reference {pointer!r} is not provable from the Capability output schema"
            )
        schema_type = current.get("type")
        properties = current.get("properties")
        if schema_type == "object" and isinstance(properties, dict) and token in properties:
            current = properties[token]
            continue
        if schema_type == "array" and token.isdigit() and isinstance(current.get("items"), dict):
            current = current["items"]
            continue
        raise ValueError(
            f"runtime reference {pointer!r} does not exist in the Capability output schema"
        )
    if not isinstance(current, dict):
        raise ValueError(
            f"runtime reference {pointer!r} is not provable from the Capability output schema"
        )
    return current


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
        if column.executor.kind == "capability_sequence":
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
    return {"project": project, "workflow": workflow}


def _system_files_list(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    limit = int(args.get("limit") or ctx.store.policy.service_limits.default_file_list_size)
    paths = ctx.system_files.list_paths(
        str(args.get("path") or "."),
        str(args.get("pattern") or "**/*"),
        limit=limit,
    )
    return {"paths": paths, "count": len(paths)}


def _system_files_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    path = str(args["path"])
    resolved = ctx.system_files.resolve(path)
    return {
        "path": str(resolved),
        "content": ctx.system_files.read_text(
            path,
            int(args["max_chars"]) if args.get("max_chars") is not None else None,
        ),
    }


def _system_files_write(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    with ctx.effect_guard():
        return {"file": ctx.system_files.write_text(str(args["path"]), str(args["content"]))}


def _system_files_search(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return ctx.system_files.search_text(
        str(args.get("path") or "."),
        str(args["pattern"]),
        str(args.get("glob") or "**/*"),
        limit=int(args.get("limit") or ctx.store.policy.service_limits.default_search_results),
    )


def _system_command_run(args: dict[str, Any], ctx: CapabilityContext) -> ToolResult:
    output = ctx.system_files.run(
        [str(item) for item in args["argv"]],
        str(args.get("cwd") or "."),
        check=ctx.execution_control.check if ctx.execution_control else None,
        limits=ctx.store.policy.execution,
        guard=ctx.effect_guard,
    )
    accepted = {0}  # Exit truth belongs to the runtime, never to model arguments.
    exit_code = int(output["exit_code"])
    if exit_code in accepted and not output.get("timed_out") and not output.get("output_truncated"):
        return ToolResult(ok=True, capability="system.command.run", output=output)
    detail = str(output.get("stderr") or output.get("stdout") or "").strip()
    message = f"command exited with code {exit_code}"
    if detail:
        message = f"{message}: {detail}"
    return ToolResult(
        ok=False,
        capability="system.command.run",
        output=output,
        error={"type": "CommandFailed", "message": message, "exit_code": exit_code},
    )


def _files_list(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "**/*")
    limit = int(args.get("limit") or ctx.store.policy.service_limits.default_file_list_size)
    paths = ctx.files.list_paths(pattern, limit=limit)
    return {"paths": paths, "count": len(paths)}


def _files_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    path = str(args["path"])
    return {"path": path, "content": ctx.files.read_text(path, int(args["max_chars"]) if args.get("max_chars") is not None else None)}


def _files_measure(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return ctx.files.measure_text(str(args["path"]))


def _files_verify(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return ctx.files.verify_text(
        str(args["path"]),
        {key: value for key, value in args.items() if key != "path"},
    )


def _verify_expectations_preflight(args: dict[str, Any]) -> None:
    content = args.get("expected_content")
    if content is None:
        return
    text = str(content)
    data = text.encode("utf-8")
    actual = {
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "expected_size_bytes": len(data),
        "expected_utf8_characters": len(text),
        "expected_non_whitespace_characters": sum(
            1 for character in text if not character.isspace()
        ),
        "expected_line_count": len(text.splitlines()),
        "expected_ends_with_newline": text.endswith(("\n", "\r")),
    }
    contradictions = [
        key
        for key, derived in actual.items()
        if key in args and args[key] != derived
    ]
    minimum = args.get("minimum_non_whitespace_characters")
    if minimum is not None and actual["expected_non_whitespace_characters"] < int(minimum):
        contradictions.append("minimum_non_whitespace_characters")
    maximum = args.get("maximum_non_whitespace_characters")
    if maximum is not None and actual["expected_non_whitespace_characters"] > int(maximum):
        contradictions.append("maximum_non_whitespace_characters")
    if contradictions:
        facts = {
            key: {
                "supplied": args.get(key),
                "derived_from_expected_content": actual.get(key),
            }
            for key in contradictions
        }
        raise ValueError(
            "expected_content contradicts explicit verification expectations; "
            f"conflicting facts={facts}"
        )


def _files_write_preflight(args: dict[str, Any], ctx: CapabilityContext) -> None:
    requested_path = str(args["path"]).replace("\\", "/")
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
    info = ctx.files.write_text(str(args["path"]), str(args["content"]))
    artifact = ctx.store.register_artifact(
        ctx.project_id,
        ctx.task_id,
        ctx.column_run_id,
        str(args.get("kind") or "file"),
        info["path"],
        info["sha256"],
        info["size"],
        {"agent_run_id": ctx.agent_run_id},
    )
    return {"file": info, "artifact": artifact,
            **({'execution_key': ctx.execution_key} if ctx.execution_key else {})}


def _files_search(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    expression = re.compile(str(args["pattern"]))
    limit = int(args.get("limit") or ctx.store.policy.service_limits.default_search_results)
    matches: list[dict[str, Any]] = []
    for path in ctx.files.list_paths(str(args.get("glob") or "**/*"), limit=ctx.store.policy.service_limits.max_file_list_size):
        content = ctx.files.read_text(path)
        for number, line in enumerate(content.splitlines(), 1):
            if expression.search(line):
                matches.append({"path": path, "line": number, "text": line[:1000]})
                if len(matches) >= limit:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def _command_run(args: dict[str, Any], ctx: CapabilityContext) -> ToolResult:
    output = ctx.files.run(
        [str(item) for item in args["argv"]],
        str(args.get("cwd") or "."),
        check=ctx.execution_control.check if ctx.execution_control else None,
        limits=ctx.store.policy.execution,
        guard=ctx.effect_guard,
    )
    accepted = {0}  # Exit truth belongs to the runtime, never to model arguments.
    exit_code = int(output["exit_code"])
    if exit_code in accepted and not output.get("timed_out") and not output.get("output_truncated"):
        return ToolResult(ok=True, capability="project.command.run", output=output)
    detail = str(output.get("stderr") or output.get("stdout") or "").strip()
    message = f"command exited with code {exit_code}"
    if detail:
        message = f"{message}: {detail}"
    return ToolResult(
        ok=False,
        capability="project.command.run",
        output=output,
        error={
            "type": "CommandFailed",
            "message": message,
            "exit_code": exit_code,
        },
    )


def _workflow_inspect(_args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any] | None:
    try:
        return ctx.store.get_workflow(ctx.project_id)
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
    ctx.store.agents.assert_conversation(ctx)
    if not ctx.user_initiated or not ctx.start_task:
        raise PermissionError('A new delivery objective requires a user-initiated turn; continue the active Requirement for event-driven work')
    requirement = ctx.store.agents.requirement(ctx.project_id, **args, strict=True)
    return ctx.store.agents.select_requirement(ctx, requirement['id'])


def _requirement_close(args, ctx):
    _require_user_planning_turn(ctx, 'requirement.close')
    if ctx.requirement_id and ctx.requirement_id != args['requirement_id']:
        raise ValueError('Select the Requirement before closing it')
    return ctx.store.agents.close_requirement(ctx.project_id, **args)


def _inspect_conversation_turn(ctx):
    job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
    if not job:
        raise ValueError('A current Conversation Job is required')
    return {'source_message_id':job['user_message_id'], 'turn_contract':ctx.store.intents.contract(job),
            'work_intent':ctx.store.intents.context(job), 'requested_mode':job['requested_mode']}


def _loop_apply(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "loop.apply")
    result = ctx.store.apply_loop(
        ctx.project_id,
        str(args["loop_key"]),
        dict(args.get("bindings") or {}),
    )
    _bind_requirement(ctx, 'v1_workflow_plans', result['workflow_plan']['id'])
    scope = ctx.store.scopes.for_workflow(result['workflow']['id'])
    ctx.store.scopes.bind_workflow(result['workflow']['id'], scope['binding_id'], ctx.requirement_id, ctx.requirement_revision)
    return result


def _workflow_plan_save(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "workflow.plan.save")
    result = ctx.store.create_workflow_plan(ctx.project_id, WorkflowPlan.model_validate(args["plan"]))
    _bind_requirement(ctx, 'v1_workflow_plans', result['id'])
    return result


def _task_plan_save(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    _require_user_planning_turn(ctx, "task.plan.save")
    _check_workflow_scope(ctx, args['plan']['workflow_revision_id'])
    result = ctx.store.create_task_plan(ctx.project_id, TaskPlan.model_validate(args["plan"]))
    _bind_requirement(ctx, 'v1_task_plans', result['id'])
    return result


def _workflow_publish(args: dict[str, Any], ctx: CapabilityContext, registry: CapabilityRegistry) -> dict[str, Any]:
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
    fingerprint = hashlib.sha256(json.dumps({'workflow': plan['workflow_revision_id'], 'task': proposed}, sort_keys=True).encode()).hexdigest()
    existing = ctx.store.agents.caused_task(ctx.requirement_id, cause, args['proposed_task_ref'], fingerprint)
    if existing:
        return {**ctx.store.get_task(existing), 'materialization': {
            'created_task_ids': [], 'reused_task_ids': [existing]}, 'dispatch': _TASK_DISPATCH_RECEIPT}
    task = ctx.store.materialize_task_plan(
        ctx.project_id,
        task_plan_id=str(args["task_plan_id"]),
        proposed_task_ref=str(args["proposed_task_ref"]),
    )
    _bind_requirement(ctx, 'v1_tasks', task['id'])
    ctx.store.agents.record_caused_task(ctx.requirement_id, cause, args['proposed_task_ref'], fingerprint, task['id'])
    return {**ctx.store.get_task(task['id']), 'materialization': task['materialization'], 'dispatch': _TASK_DISPATCH_RECEIPT}


_TASK_DISPATCH_RECEIPT = {
    'owner': 'runtime',
    'mode': 'asynchronous',
    'next_action': 'Report the actual Task IDs with conversation.reply. Runtime schedules Column Workers automatically; '
                   'pending does not mean started. Do not start a Worker manually or implement the Task deliverables yourself.',
}


def _bind_requirement(ctx, table, identity):
    if not ctx.requirement_id:
        return
    assert table in {'v1_tasks', 'v1_task_plans', 'v1_workflow_plans'}
    with ctx.store.tx(immediate=True) as db:
        row = db.execute(f'SELECT * FROM {table} WHERE id=? AND project_id=?', (identity, ctx.project_id)).fetchone()
        if not row or row['requirement_id'] not in (None, ctx.requirement_id):
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
            raise ValueError('Workflow belongs to a different Requirement revision; plan against the Workflow returned by project.scope.revise')


def _agent_context_read(args, ctx):
    worker_id = args.get('worker_id') or ctx.agent_instance_id
    if not worker_id or (ctx.column_run_id and worker_id != ctx.agent_instance_id):
        raise PermissionError('Leaf Workers may read only their own context')
    return ctx.store.agents.context_page(ctx.project_id, worker_id, after=args.get('after_message_id', 0), limit=args.get('limit', 20))


def _task_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    return ctx.store.list_tasks(ctx.project_id, int(args.get("limit") or ctx.store.policy.service_limits.default_page_size))


def _task_inspect(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(str(args["task_id"]))
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    task_fields = (
        "id", "project_id", "workflow_revision_id", "task_plan_id", "proposed_task_ref",
        "logical_task_key",
        "title", "status", "control_state", "current_column", "attempt", "error",
        "created_at", "updated_at", "finished_at", "state_version", "terminal_artifact_id",
        "terminal_event_id", "notified_at", "observed_at", "supervision_action",
        "next_retry_at", "rerun_of_task_id", "resolved_by_task_id",
    )
    run_fields = (
        "id", "task_id", "column_key", "sequence", "status", "attempt", "error",
        "error_category", "failure_fingerprint", "started_at", "finished_at", "created_at",
    )
    attempt_fields = (
        "id", "task_id", "column_run_id", "attempt_no", "status", "error",
        "error_category", "failure_fingerprint", "agent_run_id", "started_at",
        "finished_at", "created_at",
    )
    return {
        "task": {key: task.get(key) for key in task_fields},
        "scheduling": ctx.store.task_scheduling(ctx.project_id, task["id"]),
        "runs": [
            {key: item.get(key) for key in run_fields}
            for item in ctx.store.runs(ctx.project_id, task["id"])
        ],
        "attempts": [
            {key: item.get(key) for key in attempt_fields}
            for item in ctx.store.attempts(ctx.project_id, task["id"])
        ],
        "artifacts": ctx.store.artifacts(ctx.project_id, task["id"]),
    }


def _task_retry(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(str(args["task_id"]))
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return ctx.store.retry_task(
        task["id"],
        str(args["column_key"]) if args.get("column_key") else None,
        clear_context=bool(args.get("clear_context", False)),
    )


def _task_rerun(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))
    return ctx.store.rerun_task(task["id"])


def _task_successor(args, ctx):
    _require_user_planning_turn(ctx, 'task.successor')
    task = ctx.store.get_project_task(ctx.project_id, args['task_id'])
    if task['task_plan_id'] == args['task_plan_id']:
        raise ValueError('task.successor requires a corrected TaskPlan; use task.rerun to repeat the original plan')
    _bind_requirement(ctx, 'v1_task_plans', args['task_plan_id'])
    plan = ctx.store.get_task_plan(ctx.project_id, args['task_plan_id'])['plan']
    _check_workflow_scope(ctx, plan['workflow_revision_id'])
    if not any(item['proposed_task_ref'] == task['proposed_task_ref'] for item in plan['tasks']):
        raise ValueError('Corrected TaskPlan must preserve the predecessor proposed_task_ref')
    return ctx.store.materialize_task_plan(ctx.project_id, task_plan_id=args['task_plan_id'],
        proposed_task_ref=task['proposed_task_ref'], rerun_of_task_id=task['id'])


def _turn_resolution_schema():
    from app.v1.conversation_intent import TurnResolution
    schema = TurnResolution.model_json_schema()
    schema['required'] = [key for key in schema['required'] if key not in {'source_message_id', 'based_on_intent_revision'}]
    for key in ('source_message_id', 'based_on_intent_revision', 'user_evidence'):
        schema['properties'][key]['description'] = 'Optional: host binds the current user turn when omitted. Explicit values remain strictly validated.'
    return schema


def _conversation_history(args, ctx):
    job = ctx.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
    before = min(int(args.get('before_message_id') or 2**63-1), job['user_message_id'] if job else 2**63-1)
    with ctx.store.connect() as db:
        rows = db.execute("""SELECT id,role,content FROM v1_conversations
            WHERE project_id=? AND id<? AND role IN ('user','assistant')
            AND COALESCE(json_extract(meta_json,'$.kind'),'')!='notification'
            ORDER BY id DESC LIMIT ?""", (ctx.project_id,before,int(args.get('limit') or 10))).fetchall()
    return {'messages':[dict(row) for row in reversed(rows)], 'next_before_message_id':rows[-1]['id'] if rows else None}


def _resolve_current_turn(args, ctx):
    job = ctx.store.agents.assert_conversation(ctx)
    arguments = dict(args)
    arguments.setdefault('source_message_id', job['user_message_id'])
    # Preserve the original revision for an idempotent replay after resolution.
    with ctx.store.connect() as db:
        prior = db.execute('SELECT resolution_json FROM v1_turn_resolutions WHERE job_id=?', (job['id'],)).fetchone()
    revision = json.loads(prior[0])['based_on_intent_revision'] if prior else ctx.store.intents.context(job)['revision']
    arguments.setdefault('based_on_intent_revision', revision)
    arguments.setdefault('user_evidence', [{'message_id':job['user_message_id']}])
    return ctx.store.intents.resolve(ctx, arguments)


def _task_pause(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))
    return ctx.store.pause_task(task["id"])


def _task_resume(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))
    if task.get("supervision_action") == "startup_hold" and not ctx.user_initiated:
        raise ValueError(
            "A startup-held Task can only be resumed from a user-initiated Conversation turn"
        )
    return ctx.store.resume_task(task["id"])


def _task_fail(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(str(args["task_id"]))
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return ctx.store.route_task_to_failed(task["id"], str(args["reason"]))


def _event_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    events = ctx.store.events(
        project_id=ctx.project_id,
        after=int(args.get("after") or 0),
        limit=int(args.get("limit") or ctx.store.policy.service_limits.detail_page_size),
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
    return ctx.store.update_conversation_instruction(ctx.project_id, str(args["instruction"]))


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
    evidence = {
        key: value
        for key, value in (reference or {}).items()
        if key in {
            "agent_run_id",
            "tool_call_id",
            "evidence_id",
            "capability",
            "entity_ids",
            "entity_ids_truncated",
            "entity_id_count",
            "entity_ids_sha256",
        }
        and value is not None
    }
    payload = result.model_dump(mode="json")
    if evidence:
        payload["evidence"] = evidence
    return json.dumps(payload, ensure_ascii=False)
