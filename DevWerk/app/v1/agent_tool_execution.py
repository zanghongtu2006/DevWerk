from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import json

from app.v1.agent_models import AgentRunSpec
from app.v1.agent_protocol import ConversationProtocolStalled
from app.v1.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    tool_result_json,
)
from app.v1.completion_protocol import (
    CompletionAttemptGuard,
    CompletionContract,
    parse_completion_submission,
)
from app.v1.domain import ToolResult
from app.v1.execution_ledger import evidence_id, ledger_entry, repeats_failed_operation, operation_sha256
from app.v1.services.completion_admission import CompletionAdmissionService
from app.v1.tool_protocol import column_tool_batch_error, parse_await_submission


@dataclass(frozen=True)
class ToolBatchResult:
    completion: dict[str, Any] | None
    wait_request: dict[str, Any] | None
    direct_effect_calls: int
    ledger_entries: tuple[dict[str, Any], ...]


class AgentToolBatchExecutor:
    """Validate, execute, and persist one Provider tool-call batch."""

    def __init__(
        self,
        *,
        store: Any,
        registry: CapabilityRegistry,
        spec: AgentRunSpec,
        run_id: str,
        allowed: list[str],
        effect_kinds: dict[str, str],
        capability_context: CapabilityContext,
        logical_ledger: list[dict[str, Any]],
        completion_contract: CompletionContract | None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.spec = spec
        self.run_id = run_id
        self.allowed = allowed
        self.effect_kinds = effect_kinds
        self.capability_context = capability_context
        self.logical_ledger = logical_ledger
        self.completion_contract = completion_contract
        self.completion_admission = CompletionAdmissionService()
        self.completion_guard = CompletionAttemptGuard()
        self.effect_occurrences: dict[str, int] = {}

    def execute(
        self,
        tool_calls: list[Any],
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        operation_ids: list[str] | None = None,
    ) -> ToolBatchResult:
        completion: dict[str, Any] | None = None
        wait_request: dict[str, Any] | None = None
        direct_effect_calls = 0
        ledger_start = len(self.logical_ledger)
        completion_tool_name = (
            self.completion_contract.tool_name
            if self.completion_contract is not None
            else "column.complete"
        )
        protocol_error = (
            column_tool_batch_error(tool_calls, completion_tool_name)
            if self.spec.kind == "column"
            else None
        )
        if self.spec.kind == 'conversation' and len(tool_calls) > 1 and any(
            call.name in {'conversation.turn.resolve','conversation.reply'} for call in tool_calls
        ):
            protocol_error = 'Submit conversation.turn.resolve or conversation.reply alone; read other receipts before choosing the next action. This batch had no effects.'
        if protocol_error is not None:
            self.completion_guard.observe_rejection(
                {
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in tool_calls
                    ]
                },
                ValueError(protocol_error),
                self.logical_ledger,
                completion_tool_name=completion_tool_name,
            )

        if self.spec.kind == 'conversation' and not protocol_error:
            for call in tool_calls:
                if repeats_failed_operation(call.name, call.arguments, self.logical_ledger):
                    raise ConversationProtocolStalled(
                        f'Conversation Agent repeated an identical failed tool operation without changing its arguments: {call.name}')

        for index, call in enumerate(tool_calls):
            operation_id = operation_ids[index] if operation_ids else None
            operation = self.store.agents.operation(operation_id) if operation_id else None
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if operation and operation['result_json']:
                result = ToolResult.model_validate_json(operation['result_json'])
                completion = json.loads(operation['completion_json']) if operation['completion_json'] else None
                wait_request = json.loads(operation['wait_json']) if operation['wait_json'] else None
                if completion is not None and self.spec.kind == 'column':
                    # A prior admission may precede an interrupted Task commit.
                    # Validate the frozen checks against the current workspace.
                    result, completion = self._complete(call.arguments)
                elif completion is not None and call.name == 'conversation.reply':
                    from app.v1.conversation_report import render_report
                    rendered, evidence = render_report(
                        self.store, self.spec.project['id'], operation['source_run_id'],
                        json.dumps(call.arguments, ensure_ascii=False), execution_control=self.spec.execution_control)
                    completion = {'rendered': rendered, 'conversation_report': evidence}
                    result = result.model_copy(update={'output': completion})
            elif protocol_error is not None:
                result = ToolResult(
                    ok=False,
                    capability=call.name,
                    error={
                        "type": "ColumnCompletionProtocolError" if self.spec.kind == 'column' else 'ConversationTurnProtocolError',
                        "message": protocol_error,
                    },
                    checkpoint={"failure_disposition": "rejected_before_effect"},
                )
            elif call.name == completion_tool_name:
                result, completion = self._complete(call.arguments)
            elif call.name == "column.await":
                result, wait_request = self._await(call.arguments)
            elif call.name not in self.allowed:
                result = ToolResult(
                    ok=False,
                    capability=call.name,
                    error={
                        "type": "CapabilityUnavailable",
                        "message": (
                            "capability is not available in this Agent Run: " + call.name
                        ),
                    },
                    checkpoint={"failure_disposition": "rejected_before_effect"},
                )
            elif (
                self.spec.kind == "conversation"
                and (boundary_error := self.store.intents.denial(
                    self.capability_context, call.name, call.arguments,
                    self.effect_kinds.get(call.name, 'control')))
            ):
                result = ToolResult(
                    ok=False,
                    capability=call.name,
                    error={
                        "type": boundary_error.split(':')[0],
                        "message": boundary_error,
                    },
                    checkpoint={"failure_disposition": "rejected_before_effect"},
                )
            elif (
                self.spec.kind == "conversation"
                and repeats_failed_operation(
                    call.name,
                    call.arguments,
                    self.logical_ledger,
                )
            ):
                raise ConversationProtocolStalled(
                    "Conversation Agent repeated an identical failed tool operation "
                    f"without changing its arguments: {call.name}"
                )
            else:
                execution_key = operation_id or f"{self.run_id}:{call.id}"
                result = self.registry.dispatch(
                    call.name,
                    call.arguments,
                    replace(
                        self.capability_context,
                        execution_key=execution_key,
                    ),
                )
                result = result.model_copy(update={"checkpoint": {**(result.checkpoint or {}), "execution_key": execution_key}})
                if self.spec.kind == "column" and result.status == "awaiting":
                    if not self.spec.wait_config:
                        raise RuntimeError(
                            "Capability returned awaiting but the Column has no wait policy"
                        )
                    wait_request = {
                        **dict(result.await_handle_draft or {}),
                        "checkpoint": {
                            **dict(result.checkpoint or {}),
                            "execution_key": execution_key,
                            "capability_result": result.model_dump(mode="json"),
                        },
                        "source": "agent",
                        "capability": call.name,
                    }
                if (
                    self.spec.kind == "conversation"
                    and result.ok
                    and self.effect_kinds.get(call.name) in {"write", "process"}
                ):
                    direct_effect_calls += 1
                    self.store.record_governance_decision(
                        self.spec.project["id"],
                        "direct_execution",
                        self.spec.task_id,
                        "executed",
                        {
                            "agent_run_id": self.run_id,
                            "capability": call.name,
                            "scope_index": direct_effect_calls,
                        },
                    )

            if self.spec.kind == 'conversation' and call.name == 'conversation.reply' and result.ok:
                completion = result.output
            if operation_id:
                if wait_request:
                    wait_request['checkpoint'] = {**(wait_request.get('checkpoint') or {}), 'operation_id': operation_id}
                    if self.spec.assignment:
                        self.store.agents.note_wait(self.spec.assignment, operation_id, self.logical_ledger)
                self.store.agents.answer_operation(operation_id, result.model_dump(mode="json"), completion, wait_request)
            self.store.record_tool_invocation(
                agent_run_id=self.run_id,
                tool_call_id=call.id,
                capability=call.name,
                arguments=call.arguments,
                result=result.model_dump(mode="json"),
                ok=result.ok,
            )
            item = ledger_entry(
                self.run_id,
                call.id,
                call.name,
                self.effect_kinds.get(call.name, "control"),
                result,
                arguments=call.arguments,
            )
            self.logical_ledger.append(item)
            tool_message = {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": tool_result_json(
                    result,
                    None,
                    reference={
                        "agent_run_id": self.run_id,
                        "tool_call_id": call.id,
                        "evidence_id": evidence_id(self.run_id, call.id),
                        "capability": call.name,
                        "entity_ids": item["entity_ids"],
                        "entity_ids_truncated": item.get("entity_ids_truncated", False),
                        "entity_id_count": item.get(
                            "entity_id_count", len(item["entity_ids"])
                        ),
                        "entity_ids_sha256": item.get("entity_ids_sha256"),
                    },
                ),
            }
            messages.append(tool_message)
            self.store.add_agent_message(
                self.run_id,
                "tool",
                tool_message["content"],
                [],
                call.id,
                progress_details={"iteration": iteration, "capability": call.name},
            )
            if operation_id:
                self.store.agents.deliver_operation(operation_id)
            if wait_request is not None:
                break

        return ToolBatchResult(
            completion=completion,
            wait_request=wait_request,
            direct_effect_calls=direct_effect_calls,
            ledger_entries=tuple(self.logical_ledger[ledger_start:]),
        )

    def _complete(
        self,
        arguments: dict[str, Any],
    ) -> tuple[ToolResult, dict[str, Any] | None]:
        if self.completion_contract is None:
            raise RuntimeError("Conversation Agent cannot submit a Column completion")
        tool_name = self.completion_contract.tool_name
        try:
            submission = parse_completion_submission(
                arguments,
                self.completion_contract,
                self.logical_ledger,
            )
            if not self.completion_contract.rule(submission.outcome).allows_unresolved_failures:
                self._run_acceptance_checks()
                submission = parse_completion_submission(arguments, self.completion_contract, self.logical_ledger)
            decision = self.completion_admission.evaluate(
                submission,
                self.completion_contract,
                self.logical_ledger,
            )
            if not decision.accepted:
                assert decision.rejection is not None
                raise ValueError(decision.rejection.message)
            assert decision.submission is not None
            self.completion_guard.observe_progress()
            return (
                ToolResult(
                    ok=True,
                    capability=tool_name,
                    output={"accepted": True},
                ),
                decision.submission.model_dump(),
            )
        except ValueError as exc:
            self.completion_guard.observe_rejection(
                arguments,
                exc,
                self.logical_ledger,
                completion_tool_name=tool_name,
            )
            return (
                ToolResult(
                    ok=False,
                    capability=tool_name,
                    error={"type": type(exc).__name__, "message": str(exc)},
                    checkpoint={"failure_disposition": "rejected_before_effect"},
                ),
                None,
            )

    def _run_acceptance_checks(self):
        for check in self.completion_contract.acceptance_checks:
            if self.spec.assignment:
                self.store.agents.charge(self.spec.assignment, tools=1)
            call_id = f"acceptance-{len(self.logical_ledger)}-{check['key']}"
            result = self.registry.dispatch(check['capability'], check['arguments'],
                                            replace(self.capability_context, execution_key=f'{self.run_id}:{call_id}'))
            if result.status == 'awaiting':
                raise ValueError('Acceptance checks must complete synchronously')
            self.store.record_tool_invocation(agent_run_id=self.run_id, tool_call_id=call_id,
                                             capability=check['capability'], arguments=check['arguments'],
                                             result=result.model_dump(mode='json'), ok=result.ok)
            self.store.feedback.record_check(self.spec, check['key'], f'{self.run_id}:{call_id}', result, agent_run_id=self.run_id)
            item = ledger_entry(self.run_id, call_id, check['capability'], self.registry.side_effect_kind(check['capability']), result, arguments=check['arguments'])
            item['acceptance_check'] = check['key']
            self.logical_ledger.append(item)

    def _await(
        self,
        arguments: dict[str, Any],
    ) -> tuple[ToolResult, dict[str, Any] | None]:
        if not self.spec.wait_config:
            raise RuntimeError("Column has no declarative wait policy")
        try:
            normalized = parse_await_submission(
                arguments,
                self.allowed,
                self.spec.wait_config,
            )

            self.completion_guard.observe_progress()
            return (
                ToolResult(
                    ok=True,
                    capability="column.await",
                    output={"accepted": True},
                ),
                normalized,
            )
        except ValueError as exc:
            self.completion_guard.observe_rejection(
                arguments,
                exc,
                self.logical_ledger,
                completion_tool_name="column.await",
            )
            return (
                ToolResult(
                    ok=False,
                    capability="column.await",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    checkpoint={"failure_disposition": "rejected_before_effect"},
                ),
                None,
            )
