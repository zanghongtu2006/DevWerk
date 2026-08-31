from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

from app.core.debug_trace import trace_json
from app.v1.capabilities import CapabilityContext, CapabilityRegistry, tool_result_json
from app.v1.contracts import validate_contract
from app.v1.domain import AgentModelResponse, ToolResult
from app.v1.llm import complete as provider_complete
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicySnapshot, V1RuntimePolicy


ModelComplete = Callable[..., AgentModelResponse]
trace_log = logging.getLogger("devwerk.agent.trace")


@dataclass(frozen=True)
class AgentRunSpec:
    kind: Literal["conversation", "column"]
    project: dict[str, Any]
    instruction: str
    instruction_revision: int
    context: dict[str, Any]
    capability_ids: list[str]
    history: list[dict[str, Any]] = field(default_factory=list)
    task_id: str | None = None
    column_run_id: str | None = None
    column_attempt_id: str | None = None
    start_task: bool = True
    completion_outcomes: set[str] = field(default_factory=set)
    output_contract: dict[str, Any] = field(default_factory=dict)
    wait_config: dict[str, Any] = field(default_factory=dict)
    conversation_job_id: str | None = None
    completion_targets: dict[str, str] = field(default_factory=dict)
    agent_session_id: str | None = None
    writable_paths: tuple[str, ...] | None = None
    user_initiated: bool = False
    completion_tool_name: str = "column.complete"
    completion_requires_evidence: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    agent_run_id: str
    status: Literal["succeeded", "waiting", "failed"]
    text: str = ""
    completion: dict[str, Any] | None = None
    tool_calls: int = 0
    iterations: int = 0
    error: str | None = None
    error_category: str | None = None
    wait_request: dict[str, Any] | None = None
    error_code: str | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)


class AgentCore:
    """Provider-independent tool loop shared by long-lived and ephemeral agents."""

    def __init__(self, store: Any, registry: CapabilityRegistry, model_complete: ModelComplete | None = None, *, policy: V1RuntimePolicy | None = None, platform_policy: PlatformPolicySnapshot | None = None):
        self.store = store
        self.registry = registry
        self.model_complete = model_complete or provider_complete
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.platform_policy = platform_policy

    def run(self, spec: AgentRunSpec) -> AgentRunResult:
        platform_policy = self.platform_policy or self.store.latest_platform_policy()
        capability_context = CapabilityContext(
            project_id=spec.project["id"],
            project=spec.project,
            store=self.store,
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            start_task=spec.start_task,
            writable_paths=spec.writable_paths,
            user_initiated=spec.user_initiated,
        )
        allowed = list(dict.fromkeys(spec.capability_ids))
        resolved_capabilities = self.registry.resolve(allowed, capability_context)
        effect_kinds = {item.id: item.side_effect_kind for item in resolved_capabilities}
        tools = [item.tool_schema() for item in resolved_capabilities]
        if spec.kind == "column":
            tools.append(
                _column_complete_schema(
                    spec.completion_outcomes,
                    spec.output_contract,
                    tool_name=spec.completion_tool_name,
                )
            )
            if spec.wait_config:
                tools.append(_column_await_schema(allowed, spec.wait_config))
        envelope = self._envelope(spec, platform_policy)
        run = self.store.begin_agent_run(
            project_id=spec.project["id"],
            kind=spec.kind,
            instruction_revision=spec.instruction_revision,
            instruction_snapshot=spec.instruction,
            context_snapshot=envelope,
            capabilities=allowed + (
                [spec.completion_tool_name]
                if spec.kind == "column"
                else []
            ),
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            platform_policy=platform_policy,
            runtime_policy=self.policy,
            conversation_job_id=spec.conversation_job_id,
            agent_session_id=spec.agent_session_id,
        )
        capability_context = CapabilityContext(
            project_id=spec.project["id"],
            project=spec.project,
            store=self.store,
            agent_run_id=run["id"],
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            start_task=spec.start_task,
            writable_paths=spec.writable_paths,
            user_initiated=spec.user_initiated,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": _stable_json(envelope)}]
        self.store.add_agent_message(run["id"], "system", messages[0]["content"], [])
        current_request = (
            spec.context.get("current_request")
            if isinstance(spec.context, dict)
            else None
        )
        if spec.agent_session_id:
            if spec.kind == "conversation":
                history = _replayable_session_messages(
                    self.store.conversation_session_messages(
                        spec.project["id"],
                        spec.agent_session_id,
                        before_message_id=(
                            current_request.get("message_id")
                            if isinstance(current_request, dict)
                            else None
                        ),
                    )
                )
                messages.extend(history)
            else:
                history = self.store.agent_session_messages(spec.project["id"], spec.agent_session_id)
            if spec.kind == "column" and history:
                item = {
                    "role": "user",
                    "content": _stable_json({
                        "logical_agent_session_history": history,
                        "instruction": "Resume the same logical assignment; current context and review feedback are authoritative.",
                    }),
                }
                messages.append(item)
                self.store.add_agent_message(run["id"], "user", item["content"], [], emit_progress=False)
        for message in spec.history:
            if message.get("role") in {"user", "assistant"}:
                item = {"role": message["role"], "content": str(message.get("content") or "")}
                messages.append(item)
                self.store.add_agent_message(
                    run["id"],
                    item["role"],
                    item["content"],
                    [],
                    emit_progress=False,
                )
        if isinstance(current_request, dict):
            item = {
                "role": "user",
                "content": _stable_json(
                    {
                        "authoritative_current_request": current_request,
                        "instruction": (
                            "This immutable request created the current Conversation Job. "
                            "It is authoritative over historical conversation instructions."
                        ),
                    }
                ),
            }
            messages.append(item)
            self.store.add_agent_message(run["id"], item["role"], item["content"], [])

        calls_used = 0
        direct_effect_calls = 0
        logical_ledger = [
            dict(item)
            for item in (spec.context.get("action_ledger") or [])
            if isinstance(item, dict)
        ]
        seen_tool_call_ids: set[str] = set()
        latest_text = ""
        current_iteration = 0
        try:
            iteration = 0
            while True:
                iteration += 1
                current_iteration = iteration
                if spec.kind == "conversation":
                    self.store.record_conversation_progress(
                        run["id"],
                        kind="provider_wait",
                        content=f"第 {iteration} 轮：已向 LLM Provider 提交完整上下文，正在等待响应。",
                        details={"iteration": iteration},
                    )
                trace_json(
                    trace_log,
                    "agent.model_input",
                    agent_run_id=run["id"],
                    conversation_job_id=spec.conversation_job_id,
                    project_id=spec.project["id"],
                    task_id=spec.task_id,
                    column_run_id=spec.column_run_id,
                    column_attempt_id=spec.column_attempt_id,
                    agent_kind=spec.kind,
                    iteration=iteration,
                    messages=messages,
                    tools=tools,
                )
                response = self.model_complete(
                    messages,
                    tools,
                    project_id=spec.project["id"],
                    task_id=spec.task_id,
                    agent="conversation" if spec.kind == "conversation" else "column",
                    require_tool=False,
                )
                trace_json(
                    trace_log,
                    "agent.model_output",
                    agent_run_id=run["id"],
                    conversation_job_id=spec.conversation_job_id,
                    project_id=spec.project["id"],
                    task_id=spec.task_id,
                    column_run_id=spec.column_run_id,
                    column_attempt_id=spec.column_attempt_id,
                    agent_kind=spec.kind,
                    iteration=iteration,
                    response=(response.model_dump(mode="json") if isinstance(response, AgentModelResponse) else response),
                )
                if not isinstance(response, AgentModelResponse):
                    response = AgentModelResponse.model_validate(response)
                if response.text.strip():
                    latest_text = response.text.strip()
                for index, call in enumerate(response.tool_calls):
                    provider_call_id = str(call.id or "").strip()
                    if not provider_call_id:
                        provider_call_id = f"{run['id']}-call-{iteration}-{index + 1}"
                    candidate = provider_call_id
                    duplicate = 1
                    while candidate in seen_tool_call_ids:
                        duplicate += 1
                        candidate = f"{provider_call_id}-{iteration}-{index + 1}-{duplicate}"
                    call.id = candidate
                    seen_tool_call_ids.add(candidate)
                assistant_message = _assistant_message(response)
                messages.append(assistant_message)
                self.store.add_agent_message(
                    run["id"],
                    "assistant",
                    response.text,
                    assistant_message.get("tool_calls") or [],
                    progress_details={"iteration": iteration},
                )

                if response.tool_calls:
                    calls_used += len(response.tool_calls)
                    completion: dict[str, Any] | None = None
                    wait_request: dict[str, Any] | None = None
                    completion_protocol_error = (
                        _column_completion_protocol_error(
                            response.tool_calls,
                            spec.completion_tool_name,
                        )
                        if spec.kind == "column"
                        else None
                    )
                    for call in response.tool_calls:
                        if completion_protocol_error is not None:
                            result = ToolResult(
                                ok=False,
                                capability=call.name,
                                error={
                                    "type": "ColumnCompletionProtocolError",
                                    "message": completion_protocol_error,
                                },
                                checkpoint={"failure_disposition": "rejected_before_effect"},
                            )
                        elif call.name == spec.completion_tool_name:
                            try:
                                result, accepted = self._complete_column(
                                    call.arguments,
                                    spec,
                                    logical_ledger,
                                )
                            except ValueError as exc:
                                result = ToolResult(
                                    ok=False,
                                    capability=spec.completion_tool_name,
                                    error={"type": type(exc).__name__, "message": str(exc)},
                                )
                                accepted = False
                            if accepted:
                                completion = call.arguments
                        elif call.name == "column.await":
                            if not spec.wait_config:
                                raise RuntimeError("Column has no declarative wait policy")
                            else:
                                result, accepted = self._await_column(call.arguments, allowed)
                            if accepted:
                                wait_request = call.arguments
                        elif call.name not in allowed:
                            result = ToolResult(
                                ok=False,
                                capability=call.name,
                                error={
                                    "type": "CapabilityUnavailable",
                                    "message": f"capability is not available in this Agent Run: {call.name}",
                                },
                                checkpoint={"failure_disposition": "rejected_before_effect"},
                            )
                        else:
                            result = self.registry.dispatch(call.name, call.arguments, replace(capability_context, execution_key=f"{run['id']}:{call.id}"))
                            if spec.kind == "column" and result.status == "awaiting":
                                if not spec.wait_config:
                                    raise RuntimeError("Capability returned awaiting but the Column has no wait policy")
                                else:
                                    wait_request = {
                                        **dict(result.await_handle_draft or {}),
                                        "checkpoint": {
                                            **dict(result.checkpoint or {}),
                                            "execution_key": f"{run['id']}:{call.id}",
                                            "capability_result": result.model_dump(mode="json"),
                                        },
                                        "source": "agent",
                                        "capability": call.name,
                                    }
                            if (
                                spec.kind == "conversation"
                                and result.ok
                                and effect_kinds.get(call.name) in {"write", "process"}
                            ):
                                direct_effect_calls += 1
                                self.store.record_governance_decision(
                                    spec.project["id"], "direct_execution", spec.task_id,
                                    "executed", {"agent_run_id": run["id"], "capability": call.name, "scope_index": direct_effect_calls},
                                )
                        self.store.record_tool_invocation(
                            agent_run_id=run["id"],
                            tool_call_id=call.id,
                            capability=call.name,
                            arguments=call.arguments,
                            result=result.model_dump(mode="json"),
                            ok=result.ok,
                        )
                        ledger_item = _ledger_entry(
                            run["id"],
                            call.id,
                            call.name,
                            effect_kinds.get(call.name, "control"),
                            result,
                            arguments=call.arguments,
                        )
                        logical_ledger.append(ledger_item)
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": tool_result_json(
                                result,
                                None,
                                reference={
                                    "agent_run_id": run["id"],
                                    "tool_call_id": call.id,
                                    "evidence_id": _evidence_id(run["id"], call.id),
                                    "capability": call.name,
                                    "entity_ids": ledger_item["entity_ids"],
                                    "entity_ids_truncated": ledger_item.get("entity_ids_truncated", False),
                                    "entity_id_count": ledger_item.get(
                                        "entity_id_count",
                                        len(ledger_item["entity_ids"]),
                                    ),
                                    "entity_ids_sha256": ledger_item.get("entity_ids_sha256"),
                                },
                            ),
                        }
                        messages.append(tool_message)
                        self.store.add_agent_message(
                            run["id"],
                            "tool",
                            tool_message["content"],
                            [],
                            call.id,
                            progress_details={"iteration": iteration, "capability": call.name},
                        )
                        if wait_request is not None:
                            break
                    if completion is not None:
                        completed_text = _stable_json({
                            "outcome": completion.get("outcome"),
                            "summary": completion.get("summary"),
                            "output": completion.get("output"),
                        })
                        self.store.finish_agent_run(run["id"], "succeeded", completed_text, None, iteration, calls_used)
                        return AgentRunResult(
                            run["id"],
                            "succeeded",
                            completed_text,
                            completion,
                            calls_used,
                            iteration,
                            checkpoint=self._checkpoint(
                                iteration,
                                calls_used,
                                direct_effect_calls,
                                completed_text,
                            ),
                        )
                    if wait_request is not None:
                        self.store.finish_agent_run(run["id"], "waiting", response.text, None, iteration, calls_used)
                        return AgentRunResult(run["id"], "waiting", response.text, None, calls_used, iteration, wait_request=wait_request)
                    continue

                if spec.kind == "column":
                    raise RuntimeError(
                        f"Column Agent ended without calling {spec.completion_tool_name}"
                    )
                text = response.text.strip()
                if not text:
                    raise RuntimeError("Conversation Agent returned neither tools nor final text")
                unsupported_claims = _unsupported_mutation_claims(
                    text,
                    effect_kinds,
                    logical_ledger,
                )
                if unsupported_claims:
                    correction = {
                        "unsupported_mutation_claims": unsupported_claims,
                        "instruction": (
                            "The response reports state-changing capabilities without successful execution receipts. "
                            "Call those capabilities now, or return a corrected concise reply that clearly says the "
                            "changes were not executed. Do not report an intended action as completed."
                        ),
                    }
                    correction_message = {
                        "role": "user",
                        "content": _stable_json(correction),
                    }
                    messages.append(correction_message)
                    self.store.add_agent_message(
                        run["id"],
                        "user",
                        correction_message["content"],
                        [],
                        emit_progress=False,
                    )
                    continue
                self.store.finish_agent_run(run["id"], "succeeded", text, None, iteration, calls_used)
                return AgentRunResult(run["id"], "succeeded", text, None, calls_used, iteration)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            error_code = getattr(exc, "error_code", None)
            checkpoint = self._checkpoint(current_iteration, calls_used, direct_effect_calls, latest_text)
            actual_iterations = int(checkpoint.get("iterations_completed") or 0)
            category = getattr(exc, "error_category", "runtime_permanent")
            self.store.finish_agent_run(
                run["id"],
                "failed",
                "",
                error,
                actual_iterations,
                calls_used,
                error_code=error_code,
                error_category=category,
                checkpoint=checkpoint,
            )
            raise

    @staticmethod
    def _envelope(spec: AgentRunSpec, platform_policy: PlatformPolicySnapshot) -> dict[str, Any]:
        return {
            "protocol_version": "devwerk.agent.v1",
            "agent": {
                "kind": spec.kind,
                "project_id": spec.project["id"],
                "instruction_revision": spec.instruction_revision,
                "task_id": spec.task_id,
                "column_run_id": spec.column_run_id,
                "column_attempt_id": spec.column_attempt_id,
                "agent_session_id": spec.agent_session_id,
            },
            "project": {
                "name": spec.project.get("name", ""),
                "description": spec.project.get("description", ""),
                "base_dir": spec.project.get("base_dir", ""),
            },
            "instruction": spec.instruction,
            "platform_policy": {
                "revision": platform_policy.revision,
                "content_hash": platform_policy.content_hash,
                **({"content": platform_policy.content} if spec.kind == "conversation" else {}),
            },
            "context": spec.context,
        }

    @staticmethod
    def _checkpoint(
        iterations: int,
        tool_calls: int,
        direct_effect_calls: int,
        latest_text: str,
    ) -> dict[str, Any]:
        return {
            "iterations_completed": iterations,
            "tool_calls_completed": tool_calls,
            "direct_effect_calls": direct_effect_calls,
            "latest_text": latest_text,
        }

    @staticmethod
    def _complete_column(
        arguments: dict[str, Any],
        spec: AgentRunSpec,
        logical_ledger: list[dict[str, Any]],
    ) -> tuple[ToolResult, bool]:
        outcome = str(arguments.get("outcome") or "")
        if outcome not in spec.completion_outcomes:
            raise ValueError(f"undeclared Column outcome: {outcome!r}")
        output = arguments.get("output")
        if not isinstance(output, dict):
            raise ValueError("column.complete output must be an object")
        validate_contract(output, spec.output_contract, label="Column output")
        evidence_ids = [str(item) for item in arguments.get("evidence_ids") or []]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("column.complete evidence references must be unique")
        by_evidence = {
            str(item.get("evidence_id")): item
            for item in logical_ledger
            if item.get("evidence_id")
        }
        target = spec.completion_targets.get(outcome)
        success_completion = (
            spec.completion_requires_evidence
            or outcome == "success"
            or target == "done"
        )
        if success_completion:
            if not evidence_ids:
                raise ValueError("successful Column completion requires capability evidence")
            referenced = []
            for evidence_id in evidence_ids:
                entry = by_evidence.get(evidence_id)
                if entry is None:
                    raise ValueError(f"unknown Column evidence: {evidence_id!r}")
                if not entry.get("ok") or entry.get("status") != "completed":
                    raise ValueError(f"successful Column completion referenced failed evidence: {evidence_id!r}")
                referenced.append(entry)
            referenced_ids = {str(item.get("evidence_id")) for item in referenced}
            required_actions = {
                str(item.get("evidence_id"))
                for item in logical_ledger
                if item.get("ok")
                and item.get("status") == "completed"
                and item.get("effect_kind") in {"write", "process", "control"}
                and item.get("capability") not in {
                    spec.completion_tool_name,
                    "column.await",
                }
            }
            if not required_actions.issubset(referenced_ids):
                raise ValueError("successful Column completion omitted successful action evidence")
            unresolved_failures: dict[str, dict[str, Any]] = {}
            for item in logical_ledger:
                if item.get("capability") in {
                    spec.completion_tool_name,
                    "column.await",
                }:
                    continue
                if item.get("effect_kind") not in {"write", "process", "control"}:
                    continue
                if (
                    (((item.get("facts") or {}).get("result") or {}).get("checkpoint") or {}).get(
                        "failure_disposition"
                    )
                    == "rejected_before_effect"
                ):
                    continue
                operation = str(item.get("operation_sha256") or "")
                if not operation:
                    continue
                if item.get("ok") and item.get("status") == "completed":
                    unresolved_failures.pop(operation, None)
                else:
                    unresolved_failures[operation] = item
            if unresolved_failures:
                failed = next(iter(unresolved_failures.values()))
                message = str(
                    ((failed.get("facts") or {}).get("result") or {}).get("error", {}).get("message")
                    or "a failed Column action has not been repaired"
                )
                raise ValueError(message)
        else:
            unresolved_failures: dict[str, dict[str, Any]] = {}
            for item in logical_ledger:
                if item.get("capability") in {
                    spec.completion_tool_name,
                    "column.await",
                }:
                    continue
                if item.get("effect_kind") not in {"write", "process", "control"}:
                    continue
                if (
                    (((item.get("facts") or {}).get("result") or {}).get("checkpoint") or {}).get(
                        "failure_disposition"
                    )
                    == "rejected_before_effect"
                ):
                    continue
                operation = str(item.get("operation_sha256") or "")
                if not operation:
                    continue
                if item.get("ok") and item.get("status") == "completed":
                    unresolved_failures.pop(operation, None)
                else:
                    unresolved_failures[operation] = item
            if unresolved_failures:
                failed = next(iter(unresolved_failures.values()))
                message = str(
                    ((failed.get("facts") or {}).get("result") or {}).get("error", {}).get("message")
                    or "a failed Column action has not been repaired"
                )
                raise RuntimeError(message)
        return ToolResult(
            ok=True,
            capability=spec.completion_tool_name,
            output={"accepted": True},
        ), True

    @staticmethod
    def _await_column(arguments: dict[str, Any], allowed: list[str]) -> tuple[ToolResult, bool]:
        capability = str(arguments.get("poll_capability") or "")
        if capability and capability not in allowed:
            raise PermissionError("poll_capability must be selected by the Column")
        return ToolResult(ok=True, capability="column.await", output={"accepted": True}), True


def _column_complete_schema(
    outcomes: set[str],
    output_contract: dict[str, Any],
    *,
    tool_name: str = "column.complete",
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Finish this Column Run with one declared outcome and contract-valid structured output.",
            "parameters": {
                "type": "object",
                "required": ["outcome", "output", "summary", "evidence_ids"],
                "properties": {
                    "outcome": {"type": "string", "enum": sorted(outcomes)},
                    "output": output_contract or {"type": "object"},
                    "summary": {"type": "string", "maxLength": 4000},
                    "evidence_ids": {
                        "type": "array",
                        "maxItems": 500,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Canonical evidence_id from a successful business capability result. "
                                "Include every successful write, process, and control action from this Run; "
                                "do not include rejected completion-tool calls."
                            ),
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def _ledger_entry(
    agent_run_id: str,
    tool_call_id: str,
    capability: str,
    effect_kind: str,
    result: ToolResult,
    *,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    operation_json = json.dumps(
        {"capability": capability, "arguments": arguments or {}},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    entity_ids = sorted(_entity_ids(payload))
    entity_digest = hashlib.sha256(
        json.dumps(entity_ids, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    entry: dict[str, Any] = {
        "agent_run_id": agent_run_id,
        "tool_call_id": tool_call_id,
        "evidence_id": _evidence_id(agent_run_id, tool_call_id),
        "capability": capability,
        "effect_kind": effect_kind,
        "ok": result.ok,
        "status": result.status,
        "operation_sha256": hashlib.sha256(operation_json.encode("utf-8")).hexdigest(),
        "entity_ids": entity_ids,
        "result_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    }
    facts = {"arguments": arguments or {}, "result": payload}
    entry["entity_id_count"] = len(entity_ids)
    entry["entity_ids_sha256"] = entity_digest
    entry["facts"] = facts
    return entry


def _unsupported_mutation_claims(
    text: str,
    effect_kinds: dict[str, str],
    logical_ledger: list[dict[str, Any]],
) -> list[str]:
    successful = {
        str(item.get("capability") or "")
        for item in logical_ledger
        if item.get("ok") and item.get("status") == "completed"
    }
    return sorted(
        capability
        for capability, effect_kind in effect_kinds.items()
        if effect_kind in {"write", "process", "control"}
        and capability in text
        and capability not in successful
    )


def _evidence_id(agent_run_id: str, tool_call_id: str) -> str:
    return f"{agent_run_id}:{tool_call_id}"


def _entity_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == "id" or key.endswith("_id")) and item:
                found.add(item)
            found.update(_entity_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_entity_ids(item))
    return found


def _column_await_schema(allowed: list[str], wait_config: dict[str, Any]) -> dict[str, Any]:
    kind = str(wait_config.get("kind") or "poll")
    required = ["provider"]
    properties: dict[str, Any] = {
        "provider": {"type": "string", "minLength": 1, "maxLength": 200},
        "token": {"type": ["string", "null"], "maxLength": 4000},
        "checkpoint": {"type": "object"},
    }
    if kind == "poll":
        required.extend(["poll_capability", "poll_arguments"])
        properties.update({
            "poll_capability": {"type": "string", "enum": sorted(allowed)},
            "poll_arguments": {"type": "object"},
            "next_check_seconds": {"type": "integer", "minimum": 1},
        })
    return {
        "type": "function",
        "function": {
            "name": "column.await",
            "description": f"Suspend this Column Run using its declared {kind} wait policy instead of keeping an Agent alive.",
            "parameters": {
                "type": "object",
                "required": required,
                "properties": properties,
                "additionalProperties": False,
            },
        },
    }


def _column_completion_protocol_error(
    tool_calls: list[Any],
    completion_tool_name: str = "column.complete",
) -> str | None:
    complete_indices = [
        index
        for index, call in enumerate(tool_calls)
        if call.name == completion_tool_name
    ]
    if not complete_indices:
        return None
    if len(complete_indices) > 1:
        return (
            f"A model response may contain only one {completion_tool_name} call. "
            f"Combine outcome, output, summary, and evidence_ids into one final {completion_tool_name} call. "
            "No tool call from this response was executed."
        )
    if complete_indices[0] != len(tool_calls) - 1:
        return (
            f"{completion_tool_name} must be the final tool call in its model response. "
            f"Move all required tool calls before one final {completion_tool_name} call. "
            "No tool call from this response was executed."
        )
    if any(call.name == "column.await" for call in tool_calls):
        return (
            f"{completion_tool_name} and column.await are mutually exclusive in one model response. "
            f"Return either one final {completion_tool_name} call or one column.await call. "
            "No tool call from this response was executed."
        )
    return None


def _replayable_session_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay complete Turns while removing an interrupted tool-call tail."""
    replay: list[dict[str, Any]] = []
    index = 0
    while index < len(history):
        item = history[index]
        role = str(item.get("role") or "")
        if role != "assistant" or not item.get("tool_calls"):
            if role in {"user", "assistant", "tool"}:
                replay.append({
                    key: item[key]
                    for key in ("role", "content", "tool_calls", "tool_call_id")
                    if key in item and item[key] not in (None, [])
                })
            index += 1
            continue

        calls = list(item.get("tool_calls") or [])
        expected_ids = {str(call.get("id") or "") for call in calls}
        tool_messages: list[dict[str, Any]] = []
        cursor = index + 1
        while cursor < len(history) and history[cursor].get("role") == "tool":
            tool_messages.append(history[cursor])
            cursor += 1
        returned_ids = {str(tool.get("tool_call_id") or "") for tool in tool_messages}
        if expected_ids and expected_ids.issubset(returned_ids):
            replay.append({
                "role": "assistant",
                "content": str(item.get("content") or ""),
                "tool_calls": calls,
            })
            replay.extend({
                "role": "tool",
                "content": str(tool.get("content") or ""),
                "tool_call_id": str(tool.get("tool_call_id") or ""),
            } for tool in tool_messages)
        elif str(item.get("content") or "").strip():
            replay.append({
                "role": "assistant",
                "content": str(item.get("content") or ""),
            })
        index = cursor
    return replay


def _assistant_message(response: AgentModelResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.text or ""}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": _stable_json(call.arguments)},
            }
            for call in response.tool_calls
        ]
    return message


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
