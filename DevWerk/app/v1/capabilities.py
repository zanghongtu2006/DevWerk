from __future__ import annotations

import json
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

from app.v1.contracts import validate_contract
from app.v1.domain import ToolResult, WorkflowDefinition
from app.v1.files import ProjectFiles


CapabilityHandler = Callable[[dict[str, Any], "CapabilityContext"], Any]
AvailabilityCheck = Callable[["CapabilityContext"], bool]


@dataclass(frozen=True)
class CapabilityContext:
    project_id: str
    project: dict[str, Any]
    store: Any
    agent_run_id: str | None = None
    task_id: str | None = None
    column_run_id: str | None = None
    start_task: bool = True
    execution_key: str | None = None

    @property
    def files(self) -> ProjectFiles:
        return ProjectFiles(self.project["base_dir"])


@dataclass(frozen=True)
class CapabilityEntry:
    id: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: CapabilityHandler
    toolset: str = "core"
    availability_check: AvailabilityCheck | None = None
    side_effect_kind: Literal["none", "read", "write", "process", "control"] = "none"
    parallel_safe: bool = False
    default_timeout: int = 60
    delegable_to_column: bool = True

    def tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "additionalProperties": False},
            },
        }


class CapabilityRegistry:
    """Infrastructure-only capability registry used by every Agent and Column."""

    def __init__(self) -> None:
        self._entries: dict[str, CapabilityEntry] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="devwerk-capability")

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
        receipt = None
        try:
            entry = self.resolve([capability_id], context)[0]
            validate_contract(arguments, entry.input_schema, label=f"{capability_id} input")
            digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            execution_key = context.execution_key or f"adhoc:{capability_id}:{digest}"
            receipt = context.store.start_execution_receipt(context.project_id, execution_key, capability_id, arguments)
            if receipt["status"] == "completed":
                return ToolResult(ok=True, capability=capability_id, output=receipt["result"])
            if receipt["status"] == "started" and not receipt.get("claimed", False):
                return ToolResult(ok=False, capability=capability_id, error={"type": "ExecutionInProgress", "message": "an execution receipt already owns this side effect"})
            future = self._executor.submit(entry.handler, arguments, context)
            try:
                output = future.result(timeout=entry.default_timeout)
            except FutureTimeout as exc:
                future.cancel()
                raise TimeoutError(f"capability {capability_id} exceeded {entry.default_timeout}s") from exc
            validate_contract(output, entry.output_schema, label=f"{capability_id} output")
            context.store.finish_execution_receipt(context.project_id, execution_key, True, output, None)
            return ToolResult(ok=True, capability=capability_id, output=output)
        except Exception as exc:  # noqa: BLE001
            if receipt and receipt.get("claimed"):
                context.store.finish_execution_receipt(context.project_id, receipt["execution_key"], False, None, f"{type(exc).__name__}: {exc}")
            return ToolResult(
                ok=False,
                capability=capability_id,
                error={"type": type(exc).__name__, "message": str(exc)[:4000]},
            )

    def all_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)

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
                "default_timeout": entry.default_timeout,
            }
            for entry in self.resolve(self.column_ids(), context)
        ]


OBJECT = {"type": "object"}


def build_core_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    workflow_schema = WorkflowDefinition.model_json_schema()
    workflow_defs = workflow_schema.pop("$defs", {})

    def add(
        capability_id: str,
        description: str,
        input_schema: dict[str, Any],
        handler: CapabilityHandler,
        *,
        output_schema: dict[str, Any] | None = None,
        side_effect_kind: Literal["none", "read", "write", "process", "control"] = "none",
        delegable_to_column: bool = True,
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
            )
        )

    add("system.noop", "Complete a deterministic no-operation step.", {"type": "object", "additionalProperties": False}, lambda _a, _c: {"completed": True})
    add("project.inspect", "Read the current Project metadata and active workflow summary.", {"type": "object", "additionalProperties": False}, _project_inspect)
    add(
        "project.files.list",
        "List text-capable files inside the Project base directory using a relative glob.",
        {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
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
            "properties": {"path": {"type": "string", "minLength": 1}, "max_chars": {"type": "integer", "minimum": 1, "maximum": 500000}},
            "additionalProperties": False,
        },
        _files_read,
        side_effect_kind="read",
    )
    add(
        "project.files.write",
        "Atomically write a complete UTF-8 file inside the Project base directory and register it as an artifact.",
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        _files_search,
        side_effect_kind="read",
    )
    add(
        "project.command.run",
        "Run an argv command without a shell inside the Project base directory.",
        {
            "type": "object",
            "required": ["argv"],
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
            },
            "additionalProperties": False,
        },
        _command_run,
        side_effect_kind="process",
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
        "Publish a complete declarative Workflow revision. Use only capability IDs allowed by the schema, explicitly select every capability each Agent Column needs, and reach exactly one done and one failed terminal.",
        {
            "type": "object",
            "required": ["workflow"],
            "properties": {"workflow": workflow_schema},
            "additionalProperties": False,
            "$defs": workflow_defs,
        },
        lambda args, ctx: _workflow_publish(args, ctx, registry),
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.create",
        "Create a formal Task on the active Workflow after a complete readiness decision concludes dispatch.",
        {
            "type": "object",
            "required": ["title", "brief", "readiness"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "brief": {"type": "string", "maxLength": 30000},
                "input": {"type": "object"},
                "pending_timeout_seconds": {"type": "integer", "minimum": 60, "maximum": 2592000},
                "readiness": {
                    "type": "object",
                    "required": ["decision", "objective", "deliverables", "acceptance_criteria", "dependencies_checked", "reason_summary"],
                    "properties": {
                        "decision": {"type": "string", "enum": ["dispatch"]},
                        "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "scope": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                        "non_scope": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                        "deliverables": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
                        "dependencies_checked": {"type": "boolean"},
                        "resource_conflicts": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                        "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                        "reason_summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "next_review_at": {"type": ["string", "null"]}
                    },
                    "additionalProperties": False
                },
            },
            "additionalProperties": False,
        },
        _task_create,
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
        "Persist Task admission, priority, WIP group, dependencies, and resource constraints used by the dispatcher.",
        {"type": "object", "required": ["task_id", "state"], "properties": {"task_id": {"type": "string"}, "state": {"type": "string", "enum": ["admitted", "queued", "hold", "cancelled"]}, "priority": {"type": "integer", "minimum": -1000, "maximum": 1000}, "wip_group": {"type": "string", "minLength": 1, "maxLength": 200}, "wip_limit": {"type": "integer", "minimum": 1, "maximum": 100}, "dependencies": {"type": "array", "items": {"type": "string"}, "maxItems": 200}, "resources": {"type": "array", "items": {"type": "string"}, "maxItems": 200}}, "additionalProperties": False},
        lambda args, ctx: ctx.store.schedule_task(ctx.project_id, str(args["task_id"]), str(args["state"]), int(args.get("priority") or 0), str(args.get("wip_group") or "default"), int(args.get("wip_limit") or 4), list(args.get("dependencies") or []), list(args.get("resources") or [])),
        side_effect_kind="control", delegable_to_column=False,
    )
    add("task.list", "List bounded Tasks for the current Project.", {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False}, _task_list, side_effect_kind="read")
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
        "task.rerun",
        "Create a successor Task for an immutable done/failed Task.",
        {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
                "pending_timeout_seconds": {"type": "integer", "minimum": 60, "maximum": 2592000},
            },
            "additionalProperties": False,
        },
        _task_rerun,
        side_effect_kind="control",
        delegable_to_column=False,
    )
    add(
        "task.pause",
        "Pause a non-terminal Task until it is resumed or its hard pause deadline fails it explicitly.",
        {
            "type": "object",
            "required": ["task_id", "pause_timeout_seconds"],
            "properties": {
                "task_id": {"type": "string"},
                "pause_timeout_seconds": {"type": "integer", "minimum": 60, "maximum": 2592000},
            },
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
        "Route a Task through the explicit failed terminal with a recorded reason.",
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
        "Cancel a non-terminal Task through the explicit failed terminal path.",
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
        "Read bounded Project events after an event cursor.",
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
    _bind_workflow_capability_catalog(workflow_defs, registry.column_ids())
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


def validate_workflow_capabilities(workflow: WorkflowDefinition, registry: CapabilityRegistry) -> None:
    known = set(registry.column_ids())
    for column in workflow.columns:
        if column.terminal or column.executor is None:
            continue
        if column.executor.kind == "agent":
            requested = set(column.executor.capabilities)
        else:
            requested = {step.capability for step in column.executor.steps}
        requested.update(item for item in (column.wait_policy.poll_capability, column.wait_policy.cancel_capability, column.wait_policy.cleanup_capability) if item)
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"column {column.key!r} references unknown or non-delegable capabilities: {unknown}")


def _project_inspect(_args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    project = {key: ctx.project[key] for key in ("id", "name", "description", "base_dir", "created_at", "updated_at") if key in ctx.project}
    try:
        workflow = ctx.store.get_workflow(ctx.project_id)
    except KeyError:
        workflow = None
    return {"project": project, "workflow": workflow}


def _files_list(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "**/*")
    limit = int(args.get("limit") or 200)
    paths = ctx.files.list_paths(pattern, limit=limit)
    return {"paths": paths, "count": len(paths)}


def _files_read(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    path = str(args["path"])
    return {"path": path, "content": ctx.files.read_text(path, int(args.get("max_chars") or 100_000))}


def _files_write(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
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
    return {"file": info, "artifact": artifact}


def _files_search(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    expression = re.compile(str(args["pattern"]))
    limit = int(args.get("limit") or 100)
    matches: list[dict[str, Any]] = []
    for path in ctx.files.list_paths(str(args.get("glob") or "**/*"), limit=1000):
        try:
            content = ctx.files.read_text(path, 500_000)
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if expression.search(line):
                matches.append({"path": path, "line": number, "text": line[:1000]})
                if len(matches) >= limit:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def _command_run(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    return ctx.files.run(
        [str(item) for item in args["argv"]],
        str(args.get("cwd") or "."),
        int(args.get("timeout_seconds") or 600),
    )


def _workflow_inspect(_args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any] | None:
    try:
        return ctx.store.get_workflow(ctx.project_id)
    except KeyError:
        return None


def _workflow_publish(args: dict[str, Any], ctx: CapabilityContext, registry: CapabilityRegistry) -> dict[str, Any]:
    if not ctx.start_task:
        raise PermissionError("workflow.publish is disabled for this conversation turn")
    workflow = WorkflowDefinition.model_validate(args["workflow"])
    validate_workflow_capabilities(workflow, registry)
    return ctx.store.publish_workflow(ctx.project_id, workflow)


def _task_create(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    if not ctx.start_task:
        raise PermissionError("task.create is disabled for this conversation turn")
    return ctx.store.create_task(
        ctx.project_id,
        str(args["title"]),
        str(args.get("brief") or ""),
        dict(args.get("input") or {}),
        dict(args["readiness"]),
        pending_timeout_seconds=int(args.get("pending_timeout_seconds") or 86_400),
    )


def _task_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    return ctx.store.list_tasks(ctx.project_id, int(args.get("limit") or 100))


def _task_inspect(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(str(args["task_id"]))
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return {"task": task, "runs": ctx.store.runs(ctx.project_id, task["id"]), "artifacts": ctx.store.artifacts(ctx.project_id, task["id"])}


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
    return ctx.store.rerun_task(task["id"], pending_timeout_seconds=int(args.get("pending_timeout_seconds") or 86_400))


def _task_pause(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))
    return ctx.store.pause_task(task["id"], int(args["pause_timeout_seconds"]))


def _task_resume(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_project_task(ctx.project_id, str(args["task_id"]))
    return ctx.store.resume_task(task["id"])


def _task_fail(args: dict[str, Any], ctx: CapabilityContext) -> dict[str, Any]:
    task = ctx.store.get_task(str(args["task_id"]))
    if task["project_id"] != ctx.project_id:
        raise PermissionError("task is outside the current Project")
    return ctx.store.route_task_to_failed(task["id"], str(args["reason"]))


def _event_list(args: dict[str, Any], ctx: CapabilityContext) -> list[dict[str, Any]]:
    return ctx.store.events(project_id=ctx.project_id, after=int(args.get("after") or 0), limit=int(args.get("limit") or 200))


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


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("$ref must be an absolute JSON Pointer")
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def tool_result_json(result: ToolResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
