from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

from app.v1.capabilities import CapabilityContext, CapabilityRegistry, tool_result_json
from app.v1.contracts import validate_contract
from app.v1.domain import AgentModelResponse, ToolResult
from app.v1.llm import complete as provider_complete
from app.services.provider_errors import is_retryable_llm_error, llm_error_code


ModelComplete = Callable[..., AgentModelResponse]


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
    start_task: bool = True
    max_iterations: int = 12
    max_tool_calls: int = 40
    timeout_seconds: int = 900
    completion_outcomes: set[str] = field(default_factory=set)
    output_contract: dict[str, Any] = field(default_factory=dict)
    direct_effect_limit: int = 3
    provider_max_attempts: int = 3
    wait_config: dict[str, Any] = field(default_factory=dict)


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


class AgentCore:
    """Provider-independent tool loop shared by long-lived and ephemeral agents."""

    def __init__(self, store: Any, registry: CapabilityRegistry, model_complete: ModelComplete | None = None):
        self.store = store
        self.registry = registry
        self.model_complete = model_complete or provider_complete

    def run(self, spec: AgentRunSpec) -> AgentRunResult:
        capability_context = CapabilityContext(
            project_id=spec.project["id"],
            project=spec.project,
            store=self.store,
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            start_task=spec.start_task,
        )
        allowed = list(dict.fromkeys(spec.capability_ids))
        resolved_capabilities = self.registry.resolve(allowed, capability_context)
        effect_kinds = {item.id: item.side_effect_kind for item in resolved_capabilities}
        tools = [item.tool_schema() for item in resolved_capabilities]
        if spec.kind == "column":
            tools.append(_column_complete_schema(spec.completion_outcomes, spec.output_contract))
            tools.append(_column_await_schema(allowed))

        envelope = self._envelope(spec)
        run = self.store.begin_agent_run(
            project_id=spec.project["id"],
            kind=spec.kind,
            instruction_revision=spec.instruction_revision,
            instruction_snapshot=spec.instruction,
            context_snapshot=envelope,
            capabilities=allowed + (["column.complete"] if spec.kind == "column" else []),
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
        )
        capability_context = CapabilityContext(
            project_id=spec.project["id"],
            project=spec.project,
            store=self.store,
            agent_run_id=run["id"],
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            start_task=spec.start_task,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": _stable_json(envelope)}]
        self.store.add_agent_message(run["id"], "system", messages[0]["content"], [])
        for message in spec.history:
            if message.get("role") in {"user", "assistant"}:
                item = {"role": message["role"], "content": str(message.get("content") or "")}
                messages.append(item)
                self.store.add_agent_message(run["id"], item["role"], item["content"], [])

        calls_used = 0
        direct_effect_calls = 0
        delegated = False
        started = time.monotonic()
        try:
            for iteration in range(1, spec.max_iterations + 1):
                remaining = spec.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError(f"Agent Run exceeded {spec.timeout_seconds} seconds")
                response = None
                for provider_attempt in range(1, max(1, spec.provider_max_attempts) + 1):
                    try:
                        response = self.model_complete(
                            messages,
                            tools,
                            project_id=spec.project["id"],
                            task_id=spec.task_id,
                            agent="conversation" if spec.kind == "conversation" else "column",
                            timeout_seconds=remaining,
                        )
                        break
                    except Exception as provider_error:  # noqa: BLE001
                        if not is_retryable_llm_error(provider_error) or provider_attempt >= spec.provider_max_attempts:
                            raise
                        time.sleep(min(2 ** (provider_attempt - 1), 8))
                assert response is not None
                if not isinstance(response, AgentModelResponse):
                    response = AgentModelResponse.model_validate(response)
                for index, call in enumerate(response.tool_calls):
                    if not call.id:
                        call.id = f"call-{iteration}-{index + 1}"
                assistant_message = _assistant_message(response)
                messages.append(assistant_message)
                self.store.add_agent_message(run["id"], "assistant", response.text, assistant_message.get("tool_calls") or [])

                if response.tool_calls:
                    calls_used += len(response.tool_calls)
                    if calls_used > spec.max_tool_calls:
                        raise RuntimeError(f"agent tool-call budget exceeded ({spec.max_tool_calls})")
                    completion: dict[str, Any] | None = None
                    wait_request: dict[str, Any] | None = None
                    for call in response.tool_calls:
                        if time.monotonic() - started >= spec.timeout_seconds:
                            raise TimeoutError(f"Agent Run exceeded {spec.timeout_seconds} seconds")
                        if call.name == "column.complete":
                            result, accepted = self._complete_column(call.arguments, spec)
                            if accepted:
                                completion = call.arguments
                        elif call.name == "column.await":
                            result, accepted = self._await_column(call.arguments, allowed)
                            if accepted:
                                wait_request = call.arguments
                        elif call.name not in allowed:
                            result = ToolResult(
                                ok=False,
                                capability=call.name,
                                error={"type": "CapabilityDenied", "message": "capability is not available in this Agent Run"},
                            )
                        elif spec.kind == "conversation" and effect_kinds.get(call.name) in {"write", "process"} and delegated:
                            result = ToolResult(
                                ok=False,
                                capability=call.name,
                                error={
                                    "type": "DelegationBoundary",
                                    "message": "A formal Task was created in this turn; direct write/process execution is disabled. Continue with supervision or return the tracked Task status.",
                                },
                            )
                        elif (
                            spec.kind == "conversation"
                            and effect_kinds.get(call.name) in {"write", "process"}
                            and direct_effect_calls >= spec.direct_effect_limit
                        ):
                            result = ToolResult(
                                ok=False,
                                capability=call.name,
                                error={
                                    "type": "DelegationRequired",
                                    "message": "The bounded direct-execution budget is exhausted. Publish a declarative Workflow and create a formal Task instead of continuing project writes or commands.",
                                },
                            )
                        else:
                            result = self.registry.dispatch(call.name, call.arguments, replace(capability_context, execution_key=f"{run['id']}:{call.id}"))
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
                            if spec.kind == "conversation" and result.ok and call.name == "task.create":
                                delegated = True
                        self.store.record_tool_invocation(
                            agent_run_id=run["id"],
                            tool_call_id=call.id,
                            capability=call.name,
                            arguments=call.arguments,
                            result=result.model_dump(mode="json"),
                            ok=result.ok,
                        )
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": tool_result_json(result),
                        }
                        messages.append(tool_message)
                        self.store.add_agent_message(run["id"], "tool", tool_message["content"], [], call.id)
                    if completion is not None:
                        self.store.finish_agent_run(run["id"], "succeeded", response.text, None, iteration, calls_used)
                        return AgentRunResult(run["id"], "succeeded", response.text, completion, calls_used, iteration)
                    if wait_request is not None:
                        self.store.finish_agent_run(run["id"], "waiting", response.text, None, iteration, calls_used)
                        return AgentRunResult(run["id"], "waiting", response.text, None, calls_used, iteration, wait_request=wait_request)
                    continue

                if spec.kind == "column":
                    raise RuntimeError("Column Agent ended without calling column.complete")
                text = response.text.strip()
                if not text:
                    raise RuntimeError("Conversation Agent returned neither tools nor final text")
                self.store.finish_agent_run(run["id"], "succeeded", text, None, iteration, calls_used)
                return AgentRunResult(run["id"], "succeeded", text, None, calls_used, iteration)
            raise RuntimeError(f"agent iteration budget exceeded ({spec.max_iterations})")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:4000]
            self.store.finish_agent_run(run["id"], "failed", "", error, spec.max_iterations, calls_used)
            category = "provider_transient" if is_retryable_llm_error(exc) else ("provider_permanent" if llm_error_code(exc, "") else "runtime_permanent")
            return AgentRunResult(run["id"], "failed", tool_calls=calls_used, iterations=spec.max_iterations, error=error, error_category=category)

    @staticmethod
    def _envelope(spec: AgentRunSpec) -> dict[str, Any]:
        return {
            "protocol_version": "devwerk.agent.v1",
            "agent": {
                "kind": spec.kind,
                "project_id": spec.project["id"],
                "instruction_revision": spec.instruction_revision,
                "task_id": spec.task_id,
                "column_run_id": spec.column_run_id,
            },
            "project": {
                "name": spec.project.get("name", ""),
                "description": spec.project.get("description", ""),
                "base_dir": spec.project.get("base_dir", ""),
            },
            "instruction": spec.instruction,
            "context": spec.context,
            "constraints": {
                "kanban_user_access": "read_only",
                "task_terminal_states": ["done", "failed"],
                "default_execution": "delegate",
                "direct_execution_scopes": ["small_task", "diagnostic", "recovery", "emergency"],
                "responsibilities": [
                    "general_purpose_agent",
                    "project_manager",
                    "agile_coach",
                    "kanban_designer",
                    "task_supervisor",
                    "diagnostics_and_recovery",
                ],
                "workflow_source": "conversation_generated_project_data",
                "business_templates_in_runtime": False,
                "governance_protocol": {
                    "dispatch_requires_readiness_fact": True,
                    "terminal_mailbox_requires_observation": True,
                    "terminal_follow_up_requires_explicit_intervention_fact": True,
                    "scheduled_reviews_are_durable": True,
                },
            },
        }

    @staticmethod
    def _complete_column(arguments: dict[str, Any], spec: AgentRunSpec) -> tuple[ToolResult, bool]:
        try:
            outcome = str(arguments.get("outcome") or "")
            if outcome not in spec.completion_outcomes:
                raise ValueError(f"undeclared Column outcome: {outcome!r}")
            output = arguments.get("output")
            if not isinstance(output, dict):
                raise ValueError("column.complete output must be an object")
            validate_contract(output, spec.output_contract, label="Column output")
            return ToolResult(ok=True, capability="column.complete", output={"accepted": True}), True
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                capability="column.complete",
                error={"type": type(exc).__name__, "message": str(exc)[:4000]},
            ), False

    @staticmethod
    def _await_column(arguments: dict[str, Any], allowed: list[str]) -> tuple[ToolResult, bool]:
        capability = str(arguments.get("poll_capability") or "")
        if capability not in allowed:
            return ToolResult(ok=False, capability="column.await", error={"type": "CapabilityDenied", "message": "poll_capability must be selected by the Column"}), False
        return ToolResult(ok=True, capability="column.await", output={"accepted": True}), True


def _column_complete_schema(outcomes: set[str], output_contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "column.complete",
            "description": "Finish this Column Run with one declared outcome and contract-valid structured output.",
            "parameters": {
                "type": "object",
                "required": ["outcome", "output", "summary"],
                "properties": {
                    "outcome": {"type": "string", "enum": sorted(outcomes)},
                    "output": output_contract or {"type": "object"},
                    "summary": {"type": "string", "maxLength": 4000},
                },
                "additionalProperties": False,
            },
        },
    }


def _column_await_schema(allowed: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "column.await",
            "description": "Suspend this Column Run for durable asynchronous polling instead of keeping an Agent alive.",
            "parameters": {
                "type": "object",
                "required": ["provider", "poll_capability", "poll_arguments"],
                "properties": {
                    "provider": {"type": "string", "minLength": 1, "maxLength": 200},
                    "token": {"type": ["string", "null"], "maxLength": 4000},
                    "poll_capability": {"type": "string", "enum": sorted(allowed)},
                    "poll_arguments": {"type": "object"},
                    "next_check_seconds": {"type": "integer", "minimum": 5, "maximum": 3600},
                },
                "additionalProperties": False,
            },
        },
    }


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
