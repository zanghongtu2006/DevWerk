from __future__ import annotations

from typing import Any
from dataclasses import replace
import time
import queue
import threading
import json
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.execution_recovery import adopt_legacy_intents
from app.v1.execution_control import ExecutionControl, ExecutionBudgetExceeded, ExecutionReplayUncertain

from app.v1.agent_models import AgentRunResult, AgentRunSpec, ModelComplete
from app.v1.agent_prompt import assistant_message, run_checkpoint, stable_json
from app.v1.agent_provider import ProviderTurnRequester
from app.v1.agent_run_preparation import AgentRunPreparer
from app.v1.agent_tool_execution import AgentToolBatchExecutor
from app.v1.capabilities import CapabilityRegistry
from app.v1.policy import PlatformPolicySnapshot, V1RuntimePolicy


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
        )
        try:
            iteration = 0
            report_rejections = 0
            scope_id = spec.column_run_id or spec.conversation_job_id or run["id"]
            adopt_legacy_intents(self.store, self.registry, spec, run['id'])
            pending = self.store.agents.pending_operations(spec.project["id"], scope_id)
            if spec.assignment:
                self.store.agents.charge(spec.assignment, resumes=1)
            while True:
                control.check()
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
                if iteration >= limits.agent_max_iterations or calls_used >= limits.agent_max_tool_calls:
                    raise ExecutionBudgetExceeded("Agent iteration/tool-call ceiling reached")
                iteration += 1
                current_iteration = iteration
                if spec.assignment and not pending:
                    payload = self.store.agents.consume_messages(spec.assignment, run["id"])
                    if payload:
                        messages.append({"role": "user", "content": stable_json({"worker_messages": payload})})
                    self.store.agents.charge(spec.assignment, models=1)
                operation_ids = None
                if pending:
                    source = (pending[0]['source_run_id'], pending[0]['source_sequence'])
                    replay = [op for op in pending if (op['source_run_id'], op['source_sequence']) == source]
                    pending = pending[len(replay):]
                    operation_ids = [op['id'] for op in replay]
                    response = AgentModelResponse(tool_calls=[AgentToolCall(id=op['tool_call_id'], name=op['capability'], arguments=json.loads(op['arguments_json'])) for op in replay])
                else:
                    response = self._bounded_request(control, provider.request,
                        messages, prepared.tools, iteration=iteration,
                        require_tool=False, required_tool_name=None,
                    )
                if response.text.strip():
                    latest_text = response.text.strip()
                self._normalize_tool_call_ids(
                    response.tool_calls,
                    run_id=run["id"],
                    iteration=iteration,
                    seen=seen_tool_call_ids,
                )
                provider_message = assistant_message(response)
                messages.append(provider_message)
                with self.store.tx(immediate=True):
                    control.check()
                    if spec.assignment and operation_ids is None:
                        self.store.agents.charge(spec.assignment, tools=len(response.tool_calls))
                    source_message = self.store.add_agent_message(
                        run["id"], "assistant", response.text,
                        provider_message.get("tool_calls") or [],
                        progress_details={"iteration": iteration},
                    )
                    if operation_ids is None:
                        operation_ids = self.store.agents.prepare_operations(spec.project["id"], scope_id, run["id"], source_message['sequence'], response.tool_calls)

                if response.tool_calls:
                    control.check()
                    if calls_used + len(response.tool_calls) > limits.agent_max_tool_calls:
                        raise ExecutionBudgetExceeded("Agent tool-call ceiling reached")
                    calls_used += len(response.tool_calls)
                    batch = tool_batch_executor.execute(
                        response.tool_calls,
                        messages,
                        iteration=iteration,
                        operation_ids=operation_ids,
                    )
                    direct_effect_calls += batch.direct_effect_calls
                    if batch.completion is not None:
                        return self._finish_completion(
                            run["id"], batch.completion, iteration, calls_used,
                            direct_effect_calls,
                        )
                    if batch.wait_request is not None:
                        return self._finish_wait(
                            run["id"], response.text, batch.wait_request,
                            iteration, calls_used,
                        )
                    continue

                if spec.kind == "column":
                    raise RuntimeError(
                        f"Column Agent ended without calling {completion_tool_name}"
                    )
                text = response.text.strip()
                if not text:
                    raise RuntimeError(
                        "Conversation Agent returned neither tools nor final text"
                    )
                if spec.require_conversation_report:
                    from app.v1.conversation_report import render_report, REPORT_INSTRUCTION
                    try:
                        text, evidence = render_report(self.store, spec.project['id'], run['id'], text)
                    except (ValueError, KeyError) as exc:
                        report_rejections += 1
                        if report_rejections >= 3:
                            raise ValueError('Conversation reply has no valid fact report after three attempts') from exc
                        correction = 'Reply validation failed: '+str(exc)+'. '+REPORT_INSTRUCTION
                        messages.append({'role': 'user', 'content': correction})
                        self.store.add_agent_message(run['id'], 'user', correction, [], emit_progress=False)
                        continue
                    result = self._finish_text(run['id'], text, iteration, calls_used)
                    return replace(result, completion={'conversation_report': evidence})
                return self._finish_text(run["id"], text, iteration, calls_used)
        except Exception as exc:  # noqa: BLE001
            checkpoint = run_checkpoint(
                current_iteration,
                calls_used,
                direct_effect_calls,
                latest_text,
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
            try:
                ok, value = result.get(timeout=min(0.2, max(0.001, control.deadline-time.monotonic())))
            except queue.Empty:
                continue
            control.check()
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
        completed_text = stable_json({
            "outcome": completion.get("outcome"),
            "summary": completion.get("summary"),
            "output": completion.get("output"),
        })
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
