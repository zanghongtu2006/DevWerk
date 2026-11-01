from __future__ import annotations

from typing import Any
from dataclasses import replace
import time
import queue
import threading
import json
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.execution.execution_recovery import adopt_legacy_intents
from app.v1.execution.execution_control import (
    ConversationTurnPreempted, ExecutionControl, ExecutionBudgetExceeded,
    ExecutionReplayUncertain,
)

from app.v1.agents.agent_models import AgentRunResult, AgentRunSpec, ModelComplete
from app.v1.protocols.agent.agent_context_frames import CompletionResultFrame, WorkerMessagesFrame
from app.v1.agents.agent_prompt import assistant_message, run_checkpoint, stable_json
from app.v1.agents.agent_provider import ProviderTurnRequester
from app.v1.agents.agent_run_preparation import AgentRunPreparer
from app.v1.agents.agent_tool_execution import AgentToolBatchExecutor, queued_task_entry
from app.v1.protocols.agent.resolved_tool_operation import ResolvedToolOperation
from app.v1.protocols.agent.agent_runtime_receipts import ExecutionBudgetView
from app.v1.protocols.conversation.conversation_notification_contracts import ConversationUpdateMeta
from app.v1.tooling.tool_call_resolution import resolve_tool_call, schema_digest
from app.v1.capabilities import CapabilityRegistry
from app.v1.policy import PlatformPolicySnapshot, V1RuntimePolicy
from app.v1.protocols.workflow.runtime_json_contracts import WorkResultNotice


class AgentExecutionRunner:
    """Orchestrate one Agent Run without owning Workflow transitions."""

    def __init__(
        self,
        *,
        store: Any,
        registry: CapabilityRegistry,
        model_complete: ModelComplete,
        runtime_policy: V1RuntimePolicy,
        platform_policy: PlatformPolicySnapshot | None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.model_complete = model_complete
        self.runtime_policy = runtime_policy
        self.platform_policy = platform_policy

    def run(self, spec: AgentRunSpec) -> AgentRunResult:
        limits = self.runtime_policy.execution
        control = spec.execution_control or ExecutionControl(time.monotonic() + limits.agent_wall_seconds)
        spec = replace(spec, execution_control=control)
        prepared = AgentRunPreparer(
            store=self.store,
            registry=self.registry,
            runtime_policy=self.runtime_policy,
            platform_policy=self.platform_policy,
        ).prepare(spec)
        run = prepared.run
        completion_tool_name = prepared.completion_tool_name
        messages = prepared.messages
        calls_used = 0
        direct_effect_calls = 0
        seen_tool_call_ids: set[str] = set()
        latest_text = ""
        current_iteration = 0
        tool_batch_executor = AgentToolBatchExecutor(
            store=self.store,
            registry=self.registry,
            spec=spec,
            run_id=run["id"],
            allowed=prepared.allowed,
            effect_kinds=prepared.effect_kinds,
            capability_context=prepared.capability_context,
            logical_ledger=prepared.logical_ledger,
            completion_contract=prepared.completion_contract,
        )
        provider = ProviderTurnRequester(
            store=self.store,
            model_complete=self.model_complete,
            spec=spec,
            run_id=run["id"],
            registry=self.registry,
            capability_context=prepared.capability_context,
        )
        try:
            iteration = 0
            report_rejections = 0
            empty_responses = 0
            column_text_responses = 0
            force_reply_tool = spec.kind == 'conversation' and (
                any(queued_task_entry(entry)
                    for entry in prepared.logical_ledger)
                or self._active_worker_handoff(spec, prepared.logical_ledger)
            )
            scope_id = spec.column_run_id or spec.conversation_job_id or run["id"]
            adopt_legacy_intents(self.store, self.registry, spec, run['id'])
            pending = self.store.agents.pending_operations(spec.project["id"], scope_id)
            if spec.assignment:
                self.store.agents.charge(spec.assignment, resumes=1)
            while True:
                control.check()
                if spec.kind == 'conversation':
                    control.check_preempt()
                if spec.column_run_id:
                    # Runtime checks are deliberately rerun to validate current
                    # output, but an earlier unsettled check is not a safe retry.
                    with self.store.connect() as db:
                        uncertain = db.execute(
                            "SELECT e.execution_key FROM v1_execution_receipts e "
                            "JOIN v1_agent_runs r ON r.project_id=e.project_id "
                            "AND e.execution_key GLOB (r.id || ':acceptance-*') "
                            "WHERE r.project_id=? AND r.column_run_id=? "
                            "AND e.status IN ('started','awaiting') LIMIT 1",
                            (spec.project['id'], spec.column_run_id),
                        ).fetchone()
                    if uncertain:
                        raise ExecutionReplayUncertain('Runtime acceptance effect requires reconciliation: '+uncertain['execution_key'])
                max_iterations = min(limits.agent_max_iterations, 20) if spec.supervision_turn else limits.agent_max_iterations
                max_tool_calls = min(limits.agent_max_tool_calls, 50) if spec.supervision_turn else limits.agent_max_tool_calls
                reserve_enabled = (spec.kind == 'conversation' and spec.user_initiated
                                   and not spec.supervision_turn and spec.require_conversation_report)
                reserve_iterations = min(limits.conversation_finalize_reserve_iterations,
                                         max_iterations - 1) if reserve_enabled else 0
                reserve_tools = min(limits.conversation_finalize_reserve_tool_calls,
                                    max_tool_calls - 1) if reserve_enabled else 0
                if not pending and (iteration >= max_iterations or calls_used >= max_tool_calls):
                    raise ExecutionBudgetExceeded("Agent iteration/tool-call ceiling reached")
                if not pending:
                    iteration += 1
                current_iteration = iteration
                finalizing = bool(reserve_enabled and not pending and
                                  iteration > max_iterations - reserve_iterations)
                if finalizing:
                    force_reply_tool = True
                if spec.assignment and not pending:
                    consumed = self.store.agents.consume_messages(spec.assignment, run["id"])
                    if consumed:
                        messages.append({"role": "user",
                            "content": stable_json(WorkerMessagesFrame(
                                worker_messages=consumed['payload']).model_dump(mode='json')),
                            '_source_message_id':consumed['source_message_id']})
                    self.store.agents.charge(spec.assignment, models=1)
                operation_ids = None
                if pending:
                    source = (pending[0]['source_run_id'], pending[0]['source_sequence'])
                    replay = [op for op in pending if (op['source_run_id'], op['source_sequence']) == source]
                    pending = pending[len(replay):]
                    operation_ids = [op['id'] for op in replay]
                    resolutions = [ResolvedToolOperation.model_validate_json(op['resolution_json'])
                        if op.get('resolution_json') else ResolvedToolOperation(
                            provider_name=op['capability'], provider_arguments=json.loads(op['arguments_json']),
                            capability=op['capability'], arguments=json.loads(op['arguments_json']))
                        for op in replay]
                    response = AgentModelResponse(tool_calls=[AgentToolCall(
                        id=op['tool_call_id'], name=resolution.provider_name,
                        arguments=resolution.provider_arguments)
                        for op, resolution in zip(replay, resolutions)])
                else:
                    response = self._bounded_request(control, provider.request,
                        messages, prepared.tools, iteration=iteration,
                        require_tool=force_reply_tool,
                        required_tool_name='conversation.reply' if force_reply_tool else None,
                        budget_view=(ExecutionBudgetView(
                            iterations_completed=iteration - 1,
                            iterations_remaining=max(0, max_iterations - iteration + 1),
                            tool_calls_completed=calls_used,
                            tool_calls_remaining=max(0, max_tool_calls - calls_used),
                            phase='finalize' if finalizing else (
                                'advisory' if iteration >= max_iterations *
                                (1 - limits.conversation_advisory_fraction) else 'work'),
                        ) if reserve_enabled else None),
                    )
                    resolutions = [resolve_tool_call(
                        call.name, call.arguments,
                        presented_schemas=provider.last_presented_schemas,
                        allowed=prepared.allowed, registry=self.registry,
                        context=prepared.capability_context).model_copy(update={
                            'presentation_digest': schema_digest(provider.last_presented_schemas)})
                        for call in response.tool_calls]
                if spec.kind == 'conversation':
                    control.check_preempt()
                if finalizing and response.tool_calls and not (
                        len(response.tool_calls) == 1 and
                        resolutions[0].capability == 'conversation.reply'):
                    raise ExecutionBudgetExceeded(
                        'Final reserved model turn may only call conversation.reply')
                if response.text.strip():
                    latest_text = response.text.strip()
                self._normalize_tool_call_ids(
                    response.tool_calls,
                    run_id=run["id"],
                    iteration=iteration,
                    seen=seen_tool_call_ids,
                )
                effective_calls = [AgentToolCall(
                    id=call.id, name=resolution.capability,
                    arguments=resolution.arguments)
                    for call, resolution in zip(response.tool_calls, resolutions)]
                provider_message = assistant_message(response)
                messages.append(provider_message)
                replay_batch = operation_ids is not None
                if response.tool_calls and not replay_batch:
                    reply_batch = (len(response.tool_calls) == 1 and
                                   effective_calls[0].name == 'conversation.reply')
                    available = max_tool_calls if reply_batch else max_tool_calls - reserve_tools
                    if calls_used + len(response.tool_calls) > available:
                        self.store.add_agent_message(run['id'], 'assistant', response.text,
                            provider_message.get('tool_calls') or [],
                            progress_details={'iteration': iteration, 'budget_rejected': True},
                            provider_content=provider_message.get('provider_content'))
                        raise ExecutionBudgetExceeded('Tool batch exceeds remaining work budget before admission')
                with self.store.tx(immediate=True):
                    control.check()
                    if spec.assignment and operation_ids is None:
                        self.store.agents.charge(spec.assignment, tools=len(response.tool_calls))
                    source_message = self.store.add_agent_message(
                        run["id"], "assistant", response.text,
                        provider_message.get("tool_calls") or [],
                        progress_details={"iteration": iteration},
                        provider_content=provider_message.get("provider_content"),
                    )
                    provider_message['_source_message_id'] = source_message['id']
                    if operation_ids is None:
                        operation_ids = self.store.agents.prepare_operations(spec.project["id"], scope_id,
                            run["id"], source_message['sequence'], effective_calls, resolutions)
                        if (spec.kind == "conversation" and spec.user_initiated
                                and spec.conversation_job_id and response.text.strip()
                                and effective_calls and all(call.name != "conversation.reply" for call in effective_calls)):
                            update = ConversationUpdateMeta(job_id=spec.conversation_job_id,
                                agent_run_id=run["id"], source_message_id=source_message["id"])
                            self.store.add_message(project_id=spec.project["id"], role="assistant",
                                content=response.text.strip(), meta=update.model_dump(mode="json"))

                if response.tool_calls:
                    column_text_responses = 0
                    control.check()
                    if not replay_batch:
                        calls_used += len(response.tool_calls)
                    batch = tool_batch_executor.execute(
                        effective_calls,
                        messages,
                        iteration=iteration,
                        operation_ids=operation_ids,
                        resolutions=resolutions,
                    )
                    direct_effect_calls += batch.direct_effect_calls
                    if spec.kind == 'conversation':
                        control.check_preempt()
                    if batch.completion is not None:
                        if spec.kind == 'conversation':
                            result = self._finish_text(run['id'], batch.completion['rendered'], iteration, calls_used)
                            return replace(result, completion={'conversation_report':batch.completion['conversation_report']})
                        return self._finish_completion(
                            run["id"], batch.completion, iteration, calls_used,
                            direct_effect_calls,
                        )
                    if batch.wait_request is not None:
                        return self._finish_wait(
                            run["id"], response.text, batch.wait_request,
                            iteration, calls_used,
                        )
                    if spec.kind == 'conversation' and any(
                        queued_task_entry(entry)
                        for entry in batch.ledger_entries
                    ):
                        force_reply_tool = True
                    if spec.kind == 'conversation' and self._active_worker_handoff(spec, batch.ledger_entries):
                        force_reply_tool = True
                    if spec.supervision_turn:
                        recovery = next((entry for entry in batch.ledger_entries
                            if entry['ok'] and entry['capability'] in {'task.retry','task.reopen','task.successor'}), None)
                        if recovery:
                            from app.v1.conversations.conversation_report import render_report
                            task_id = (recovery['facts']['result'].get('output') or {}).get('id') if recovery['capability'] == 'task.successor' else spec.context['turn_contract']['supervised_task_id']
                            report_text = WorkResultNotice(
                                task_ids=[task_id], tool_call_ids=[recovery['tool_call_id']]
                            ).model_dump_json()
                            rendered, evidence = render_report(self.store, spec.project['id'],
                                run['id'], report_text, execution_control=control)
                            result = self._finish_text(run['id'], rendered, iteration, calls_used)
                            return replace(result, completion={'conversation_report':evidence})
                    continue

                if spec.kind == "column":
                    column_text_responses += 1
                    if column_text_responses >= 3:
                        raise RuntimeError(
                            f"Column Agent ended without calling {completion_tool_name}"
                        )
                    correction = (
                        f"This Column has no plain-text reply channel. Your last message did not complete it. "
                        f"Continue any missing work with tools, then call {completion_tool_name} with a declared "
                        "outcome and real evidence. A diagnostic summary alone cannot advance the Workflow."
                    )
                    messages.append({'role': 'user', 'content': correction})
                    self.store.add_agent_message(run['id'], 'user', correction, [], emit_progress=False)
                    continue
                text = response.text.strip()
                if not text:
                    empty_responses += 1
                    if not spec.require_conversation_report or empty_responses >= 3:
                        raise RuntimeError("Conversation Agent returned neither tools nor final text")
                    job = self.store.intents.job_for_run(run['id'], spec.project['id'])
                    force_reply_tool = ('conversation.reply' in prepared.allowed and
                        (not job or self.store.intents.contract(job)['phase'] != 'unresolved'))
                    correction = ('Your response was empty. The current turn is not finished. ' +
                        ('Call conversation.reply alone using actual evidence IDs or a discussion message.'
                         if force_reply_tool else 'Answer the user naturally, inspect with read-only tools, or bind the current user request with conversation.execution.authorize before a business mutation.'))
                    messages.append({'role':'user','content':correction})
                    self.store.add_agent_message(run['id'],'user',correction,[],emit_progress=False)
                    continue
                if spec.require_conversation_report:
                    from app.v1.conversations.conversation_report import render_report, REPORT_INSTRUCTION
                    try:
                        text, evidence = render_report(self.store, spec.project['id'], run['id'], text, execution_control=control)
                    except (ValueError, KeyError) as exc:
                        report_rejections += 1
                        if report_rejections >= 3:
                            raise ValueError('Conversation reply has no valid fact report after three attempts') from exc
                        job = self.store.intents.job_for_run(run['id'], spec.project['id'])
                        boundary_hint = ''
                        if job:
                            state = self.store.intents.context(job)
                            phase = self.store.intents.contract(job)['phase']
                            boundary_hint = (f' Current durable phase={phase}; source_message_id={job["user_message_id"]}; '
                                             f'based_on_intent_revision={state["revision"]}. Rejected calls changed nothing. '
                                             'Do not copy a question or decision ledger into turn_resolution. ')
                        force_reply_tool = ('conversation.reply' in prepared.allowed and
                            (not job or self.store.intents.contract(job)['phase'] != 'unresolved'))
                        correction = ('Reply validation failed: '+str(exc)+'. '+boundary_hint+
                            ('Call conversation.reply ALONE now with mode and actual task_ids/tool_call_ids. Do not reformat plain JSON or include turn_resolution/draft_update.'
                             if force_reply_tool else REPORT_INSTRUCTION))
                        messages.append({'role': 'user', 'content': correction})
                        self.store.add_agent_message(run['id'], 'user', correction, [], emit_progress=False)
                        continue
                    result = self._finish_text(run['id'], text, iteration, calls_used)
                    return replace(result, completion={'conversation_report': evidence})
                return self._finish_text(run["id"], text, iteration, calls_used)
        except ConversationTurnPreempted:
            # Completed Tool receipts remain durable. The next queued user turn
            # observes that state; a late Provider answer cannot execute tools.
            result = self._finish_text(
                run['id'],
                '已收到新的用户消息，本轮在安全边界结束；已提交的操作保留，后续消息将基于当前状态处理。',
                current_iteration, calls_used,
            )
            return replace(result, completion={'turn_interrupted': True})
        except Exception as exc:  # noqa: BLE001
            work = (self.store.work_progress.current_view(spec.project['id'], spec.conversation_job_id)
                    if spec.conversation_job_id else None)
            checkpoint = run_checkpoint(
                current_iteration,
                calls_used,
                direct_effect_calls,
                latest_text,
                stop_reason=type(exc).__name__,
                work_revision=work.revision if work else None,
            )
            self.store.finish_agent_run(
                run["id"],
                "failed",
                "",
                f"{type(exc).__name__}: {exc}",
                int(checkpoint.get("iterations_completed") or 0),
                calls_used,
                error_code=getattr(exc, "error_code", None),
                error_category=getattr(exc, "error_category", "runtime_permanent"),
                checkpoint=checkpoint,
            )
            raise

    def _active_worker_handoff(self, spec: AgentRunSpec, entries: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> bool:
        if not spec.user_initiated or spec.supervision_turn:
            return False
        for entry in entries:
            if not entry.get('ok') or entry.get('capability') != 'agent.worker.send':
                continue
            worker_id = ((entry.get('facts') or {}).get('arguments') or {}).get('worker_id')
            if not worker_id:
                continue
            with self.store.connect() as db:
                row = db.execute('''SELECT task.status FROM v1_agent_assignments assignment
                    JOIN v1_tasks task ON task.id=assignment.task_id
                    WHERE assignment.project_id=? AND assignment.agent_instance_id=?
                    ORDER BY assignment.rowid DESC LIMIT 1''',
                    (spec.project['id'], worker_id)).fetchone()
            if row and row['status'] in {'pending', 'running', 'waiting'}:
                return True
        return False

    @staticmethod
    def _bounded_request(control, request, *args, **kwargs):
        # Provider calls cannot be forcibly interrupted in Python. A daemon holds
        # only the request; the runtime worker exits and never executes a late tool.
        result = queue.Queue(maxsize=1)
        def invoke():
            try:
                result.put((True, request(*args, **kwargs)))
            except BaseException as exc:
                result.put((False, exc))
        threading.Thread(target=invoke, name="bounded-provider", daemon=True).start()
        while True:
            control.check()
            control.check_preempt()
            try:
                ok, value = result.get(timeout=min(0.2, max(0.001, control.deadline-time.monotonic())))
            except queue.Empty:
                continue
            control.check()
            control.check_preempt()
            if not ok:
                raise value
            return value

    @staticmethod
    def _normalize_tool_call_ids(
        tool_calls: list[Any],
        *,
        run_id: str,
        iteration: int,
        seen: set[str],
    ) -> None:
        for index, call in enumerate(tool_calls):
            provider_call_id = str(call.id or "").strip()
            if not provider_call_id:
                provider_call_id = f"{run_id}-call-{iteration}-{index + 1}"
            candidate = provider_call_id
            duplicate = 1
            while candidate in seen:
                duplicate += 1
                candidate = f"{provider_call_id}-{iteration}-{index + 1}-{duplicate}"
            call.id = candidate
            seen.add(candidate)

    def _finish_completion(
        self,
        run_id: str,
        completion: dict[str, Any],
        iteration: int,
        calls_used: int,
        direct_effect_calls: int,
    ) -> AgentRunResult:
        completed_text = stable_json(CompletionResultFrame(
            outcome=completion.get("outcome"), summary=completion.get("summary"),
            output=completion.get("output")).model_dump(mode='json'))
        checkpoint = run_checkpoint(
            iteration,
            calls_used,
            direct_effect_calls,
            completed_text,
        )
        self.store.finish_agent_run(
            run_id, "succeeded", completed_text, None, iteration, calls_used
        )
        return AgentRunResult(
            run_id,
            "succeeded",
            completed_text,
            completion,
            calls_used,
            iteration,
            checkpoint=checkpoint,
        )

    def _finish_wait(
        self,
        run_id: str,
        text: str,
        wait_request: dict[str, Any],
        iteration: int,
        calls_used: int,
    ) -> AgentRunResult:
        self.store.finish_agent_run(
            run_id, "waiting", text, None, iteration, calls_used
        )
        return AgentRunResult(
            run_id,
            "waiting",
            text,
            None,
            calls_used,
            iteration,
            wait_request=wait_request,
        )

    def _finish_text(
        self,
        run_id: str,
        text: str,
        iteration: int,
        calls_used: int,
    ) -> AgentRunResult:
        self.store.finish_agent_run(
            run_id, "succeeded", text, None, iteration, calls_used
        )
        return AgentRunResult(run_id, "succeeded", text, None, calls_used, iteration)
