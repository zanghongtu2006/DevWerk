from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.debug_trace import trace_json
from app.services.provider_errors import (
    is_recoverable_llm_error,
    is_recoverable_llm_error_code,
    llm_error_code,
)
from app.v1.agent import AgentCore, AgentRunSpec, _ledger_entry
from app.v1.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    resolve_references,
)
from app.v1.contracts import validate_contract
from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    ColumnDefinition,
    EventWaitPolicy,
    PollWaitPolicy,
    OrchestrationPlan,
    TimerWaitPolicy,
    ToolResult,
    WorkflowDefinition,
)
from app.v1.files import ProjectFiles
from app.v1.store import V1Store


log = logging.getLogger("devwerk.v1.runtime")
trace_log = logging.getLogger("devwerk.runtime.trace")


class WaitRequested(RuntimeError):
    def __init__(self, request: dict[str, Any]):
        self.request = request
        super().__init__("Column requested durable waiting")


class RuntimeExecutionError(RuntimeError):
    def __init__(self, message: str, category: str, *, error_code: str | None = None, checkpoint: dict[str, Any] | None = None, agent_run_id: str | None = None):
        self.category = category
        self.error_code = error_code
        self.checkpoint = checkpoint or {}
        self.agent_run_id = agent_run_id
        super().__init__(message)


class WorkflowRuntime:
    """Interprets declarative executor, contract, transition and terminal data."""

    def __init__(self, store: V1Store, registry: CapabilityRegistry, worker_id: str, agent_core: AgentCore | None = None):
        self.store = store
        self.registry = registry
        self.worker_id = worker_id
        self.policy = store.policy
        self.agent_core = agent_core or AgentCore(store, registry, policy=self.policy)

    def step(self, task_id: str) -> None:
        task = self.store.claim_task(task_id, self.worker_id)
        if task is None:
            return
        workflow: WorkflowDefinition | None = None
        column: ColumnDefinition | None = None
        run: dict[str, Any] | None = None
        keeper = LeaseKeeper(self.store, task_id, self.worker_id)
        keeper.start()
        try:
            workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
            column = workflow.column(task["current_column"])
            input_data = self._input_for(task, workflow, column)
            validate_contract(input_data, column.input_contract, label=f"Column {column.key} input")
            run = self.store.begin_run(task, input_data)
            trace_json(
                trace_log,
                "runtime.column_input",
                project_id=task["project_id"],
                task_id=task["id"],
                workflow_revision_id=task["workflow_revision_id"],
                column=column.model_dump(mode="json"),
                column_run_id=run["id"],
                input=input_data,
            )
            output, outcome = self._execute(task, workflow, run, column, input_data)
            validate_contract(output, column.output_contract, label=f"Column {column.key} output")
            transition = next((item for item in column.transitions if item.outcome == outcome), None)
            if transition is None:
                raise ValueError(f"column {column.key!r} produced undeclared outcome {outcome!r}")
            trace_json(
                trace_log,
                "runtime.column_output",
                project_id=task["project_id"],
                task_id=task["id"],
                workflow_revision_id=task["workflow_revision_id"],
                column_key=column.key,
                column_run_id=run["id"],
                output=output,
                outcome=outcome,
                transition_target=transition.target,
            )
            context = dict(task["context"])
            context[column.key] = output
            terminal = workflow.terminal_kind(transition.target)
            if terminal == "failed":
                context["last_error"] = _failure_message(output)
            persisted_output = {**output, "context": context}
            if terminal:
                self._validate_task_agent_terminal(task, terminal)
                terminal_error = context.get("last_error") if terminal == "failed" else None
                evidence = self.store.prepare_terminal_evidence(task, run["id"], terminal, persisted_output, terminal_error)
                self.store.finish_run(
                    task,
                    run["id"],
                    persisted_output,
                    outcome,
                    transition.target,
                    terminal=terminal,
                    error=terminal_error,
                    terminal_artifact=evidence,
                )
            else:
                self.store.finish_run(task, run["id"], persisted_output, outcome, transition.target)
        except WaitRequested as requested:
            assert column is not None and run is not None
            self._persist_wait(task, run, column, requested.request)
            return
        except Exception as exc:  # noqa: BLE001
            column_key = column.key if column else task["current_column"]
            trace_json(
                trace_log,
                "runtime.column_error",
                project_id=task["project_id"],
                task_id=task["id"],
                workflow_revision_id=task.get("workflow_revision_id"),
                column_key=column_key,
                column_run_id=run.get("id") if run else None,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            log.exception("column failed task=%s column=%s", task_id, column_key)
            if run is None:
                run = self.store.begin_run(task, {"task": task, "column": column_key})
            error = f"{type(exc).__name__}: {exc}"
            checkpoint = exc.checkpoint if isinstance(exc, RuntimeExecutionError) else {}
            error_code = (
                exc.error_code
                if isinstance(exc, RuntimeExecutionError)
                else llm_error_code(exc, default=type(exc).__name__)
            )
            error_category = (
                exc.category
                if isinstance(exc, RuntimeExecutionError)
                else "provider_transient" if is_recoverable_llm_error(exc) else "runtime_permanent"
            )
            if is_recoverable_llm_error(exc) or is_recoverable_llm_error_code(error_code):
                self.store.recover_task_from_exception(
                    task,
                    run["id"],
                    error,
                    error_code=error_code,
                    error_category=error_category,
                    checkpoint=checkpoint,
                    agent_run_id=exc.agent_run_id if isinstance(exc, RuntimeExecutionError) else None,
                )
                return
            evidence = self.store.prepare_terminal_evidence(
                task,
                run["id"],
                "failed",
                {"summary": error, "exception_type": type(exc).__name__, "checkpoint": checkpoint},
                error,
            )
            self.store.fail_task_from_exception(task, run["id"], error, evidence, checkpoint=checkpoint)
            raise
        finally:
            keeper.stop()

    def _input_for(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        column: ColumnDefinition,
    ) -> dict[str, Any]:
        project = self.store.get_project(task["project_id"])
        data: dict[str, Any] = {"column": {"key": column.key, "name": column.name}}
        plan_row = self.store.get_orchestration_plan(task["project_id"], task["orchestration_plan_id"])
        plan = dict(plan_row["plan"])
        column_plan = next(item for item in plan["columns"] if item["key"] == column.key)
        task_plan = next(item for item in plan["task_portfolio"] if item["proposed_task_ref"] == task["proposed_task_ref"])
        data["orchestration"] = {
            "plan_id": plan_row["id"],
            "plan_hash": plan_row["plan_hash"],
            "column": column_plan,
            "task": task_plan,
        }
        data["dependencies"] = self.store.task_dependency_context(
            task["project_id"],
            task["id"],
        )
        if column.context.include_project:
            data["project"] = {
                "id": project["id"],
                "name": project["name"],
                "description": project["description"],
                "base_dir": project["base_dir"],
            }
        if column.context.include_task:
            data["task"] = {
                "id": task["id"],
                "title": task["title"],
                "brief": task["brief"],
                "input": task["input"],
                "context": task["context"],
            }
        if column.context.upstream_outputs:
            selected = set(column.context.upstream_outputs)
            data["upstream_outputs"] = {
                item["column_key"]: item["output"]
                for item in self.store.runs(task["project_id"], task["id"])
                if item["column_key"] in selected and item["status"] == "succeeded"
            }
        if column.context.artifact_globs:
            files = ProjectFiles(project["base_dir"], self.policy)
            artifacts: list[dict[str, str]] = []
            for pattern in column.context.artifact_globs:
                for item in files.existing_texts(pattern, None):
                    artifacts.append(item)
            data["artifacts"] = artifacts
        return data

    def _execute(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        run: dict[str, Any],
        column: ColumnDefinition,
        input_data: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if isinstance(column.executor, CapabilitySequenceExecutor):
            return self._execute_sequence(task, run, column.executor, input_data)
        if isinstance(column.executor, AgentExecutor):
            return self._execute_agent(task, workflow, run, column, input_data)
        raise ValueError(f"column {column.key!r} has no supported declarative executor")

    def _execute_sequence(
        self,
        task: dict[str, Any],
        run: dict[str, Any],
        executor: CapabilitySequenceExecutor,
        input_data: dict[str, Any],
        resume_checkpoint: dict[str, Any] | None = None,
        awaited_output: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        project = self.store.get_project(task["project_id"])
        capability_context = CapabilityContext(
            project_id=task["project_id"],
            project=project,
            store=self.store,
            task_id=task["id"],
            column_run_id=run["id"],
        )
        scope: dict[str, Any] = dict((resume_checkpoint or {}).get("scope") or {"input": input_data, "steps": {}})
        results: list[dict[str, Any]] = list((resume_checkpoint or {}).get("results") or [])
        start_index = int((resume_checkpoint or {}).get("next_step_index") or 0)
        if resume_checkpoint and awaited_output is not None:
            key = str(resume_checkpoint["awaiting_save_as"])
            resumed = ToolResult(ok=True, capability=str(resume_checkpoint["awaiting_capability"]), output=awaited_output).model_dump(mode="json")
            scope.setdefault("steps", {})[key] = resumed
            results.append({"step": start_index - 1, "save_as": key, **resumed})
        for index in range(start_index, len(executor.steps)):
            step = executor.steps[index]
            try:
                arguments = resolve_references(step.arguments, scope)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise RuntimeExecutionError(
                    f"capability step {index} cannot resolve its declared runtime reference: {exc}",
                    "input_missing",
                    error_code="reference_unresolved",
                    checkpoint={
                        "executor_kind": "capability_sequence",
                        "input": input_data,
                        "scope": scope,
                        "results": results,
                        "failed_step_index": index,
                    },
                ) from exc
            step_context = CapabilityContext(**{**capability_context.__dict__, "execution_key": f"{run['id']}:step:{index}"})
            result = self.registry.dispatch(step.capability, arguments, step_context)
            if result.status == "awaiting":
                key = step.save_as or str(index)
                raise WaitRequested({
                    **dict(result.await_handle_draft or {}),
                    "source": "sequence",
                    "checkpoint": {
                        **dict(result.checkpoint or {}),
                        "executor_kind": "capability_sequence",
                        "input": input_data,
                        "scope": scope,
                        "results": results,
                        "next_step_index": index + 1,
                        "awaiting_save_as": key,
                        "awaiting_capability": step.capability,
                        "execution_key": f"{run['id']}:step:{index}",
                        "capability_result": result.model_dump(mode="json"),
                    },
                })
            value = result.model_dump(mode="json")
            key = step.save_as or str(index)
            scope["steps"][key] = value
            results.append({"step": index, "save_as": key, **value})
            if not result.ok:
                error = result.error or {"type": "CapabilityFailed", "message": f"{step.capability} failed"}
                category = "tool_transient" if "transient" in str(error.get("type") or "").lower() else "runtime_permanent"
                raise RuntimeExecutionError(
                    f"{step.capability}: {error.get('message') or error}",
                    category,
                    checkpoint={
                        "executor_kind": "capability_sequence",
                        "input": input_data,
                        "scope": scope,
                        "results": results,
                        "failed_step_index": index,
                        "failed_result": value,
                    },
                )
        outcome_value = executor.completed_outcome
        if executor.outcome_from:
            try:
                outcome_value = resolve_references(
                    {"$ref": executor.outcome_from},
                    scope,
                )
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise RuntimeExecutionError(
                    f"sequence outcome_from {executor.outcome_from!r} cannot be resolved: {exc}",
                    "input_missing",
                    error_code="reference_unresolved",
                    checkpoint={
                        "executor_kind": "capability_sequence",
                        "input": input_data,
                        "scope": scope,
                        "results": results,
                    },
                ) from exc
        outcome = str(outcome_value or "")
        return {"summary": f"capability sequence completed with outcome {outcome}", "steps": results}, outcome

    def _persist_wait(self, task: dict[str, Any], run: dict[str, Any], column: ColumnDefinition, request: dict[str, Any]) -> None:
        policy = column.wait_policy
        if policy is None:
            raise ValueError("durable wait requires a declarative Column wait policy")
        allowed = column.executor.capabilities if isinstance(column.executor, AgentExecutor) else [step.capability for step in column.executor.steps]
        common: dict[str, Any] = {
            "provider": str(request.get("provider") or "external"),
            "token": request.get("token"),
            "success_outcome": policy.success_outcome,
            "waiting_kind": policy.kind,
            "checkpoint": {**dict(request.get("checkpoint") or {}), "source": str(request.get("source") or "agent")},
        }
        if isinstance(policy, PollWaitPolicy):
            poll_capability = str(request.get("poll_capability") or policy.poll_capability or "")
            if not poll_capability or poll_capability not in allowed:
                raise ValueError("durable poll requires a poll_capability selected by the Column executor")
            common.update(
                poll_capability=poll_capability,
                poll_arguments=dict(request.get("poll_arguments") or policy.poll_arguments),
                next_check_seconds=int(request.get("next_check_seconds") or policy.poll_interval_seconds),
                resume_condition=policy.resume_condition,
                cancel_capability=policy.cancel_capability,
                cancel_arguments=policy.cancel_arguments,
                cleanup_capability=policy.cleanup_capability,
                cleanup_arguments=policy.cleanup_arguments,
                idempotency_key=policy.idempotency_key,
            )
        elif isinstance(policy, EventWaitPolicy):
            common.update(
                poll_capability=None,
                poll_arguments={},
                next_check_seconds=policy.check_interval_seconds,
                event_type=policy.event_type,
                correlation_key=policy.correlation_key,
            )
        elif isinstance(policy, TimerWaitPolicy):
            resume_at = policy.resume_at
            if resume_at is None:
                resume_at = (datetime.now(timezone.utc) + timedelta(seconds=int(policy.delay_seconds or 1))).isoformat(timespec="milliseconds")
            datetime.fromisoformat(resume_at)
            common.update(
                poll_capability=None,
                poll_arguments={},
                next_check_seconds=max(1, int(policy.delay_seconds or 1)),
                resume_at=resume_at,
            )
        self.store.create_await_handle(task, run["id"], **common)

    def _execute_agent(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        run: dict[str, Any],
        column: ColumnDefinition,
        input_data: dict[str, Any],
        prior_action_ledger: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str]:
        assert isinstance(column.executor, AgentExecutor)
        project = self.store.get_project(task["project_id"])
        workflow_row = self.store.get_workflow_revision(task["project_id"], task["workflow_revision_id"])
        outcomes = {item.outcome for item in column.transitions}
        session_key = str(column.metadata.get("agent_session_key") or "").strip()
        session = (
            self.store.get_or_create_agent_session(task["project_id"], task["id"], session_key)
            if session_key else None
        )
        writable_path_values = resolve_references(
            column.metadata.get("writable_paths", []),
            {"input": input_data},
        ) if "writable_paths" in column.metadata else None
        if writable_path_values is not None and not isinstance(writable_path_values, list):
            raise ValueError("Column metadata writable_paths must resolve to a list")
        result = self.agent_core.run(
            AgentRunSpec(
                kind="column",
                project=project,
                instruction=column.instruction,
                instruction_revision=int(workflow_row["revision"]),
                context={
                    "workflow": {"id": workflow_row["id"], "name": workflow.name, "description": workflow.description},
                    "column": column.model_dump(mode="json"),
                    "input": input_data,
                    "action_ledger": prior_action_ledger or [],
                },
                capability_ids=column.executor.capabilities,
                task_id=task["id"],
                column_run_id=run["id"],
                column_attempt_id=run["attempt_id"],
                completion_outcomes=outcomes,
                completion_targets={
                    transition.outcome: transition.target
                    for transition in column.transitions
                },
                output_contract=column.output_contract,
                wait_config=column.wait_policy.model_dump(mode="json") if column.wait_policy else {},
                agent_session_id=session["id"] if session else None,
                writable_paths=(
                    tuple(str(path) for path in writable_path_values)
                    if writable_path_values is not None
                    else None
                ),
            )
        )
        if result.status != "succeeded" or not result.completion:
            if result.status == "waiting" and result.wait_request:
                raise WaitRequested(result.wait_request)
            raise RuntimeExecutionError(
                result.error or "Column Agent failed without a completion",
                result.error_category or "runtime_permanent",
                error_code=result.error_code,
                checkpoint=result.checkpoint,
                agent_run_id=result.agent_run_id,
            )
        if session:
            self.store.suspend_agent_session(task["project_id"], session["id"], task["id"])
        return dict(result.completion["output"]), str(result.completion["outcome"])

    def reconcile_await(self, handle: dict[str, Any]) -> None:
        task = self.store.get_task(handle["task_id"])
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        column = workflow.column(handle["column_key"])
        now = datetime.now(timezone.utc)
        if handle["waiting_kind"] == "timer":
            payload = {"status": "succeeded", "output": {"resumed_at": now.isoformat(timespec="milliseconds")}}
        elif handle["waiting_kind"] == "event":
            event = self.store.correlated_event(
                task["project_id"], str(handle["event_type"]), str(handle["correlation_key"]), handle["created_at"]
            )
            if event is None:
                if not isinstance(column.wait_policy, EventWaitPolicy):
                    raise ValueError("await handle is not backed by an event wait policy")
                self.store.settle_await_handle(handle["id"], "pending", {"status": "waiting_for_event"}, next_check_seconds=column.wait_policy.check_interval_seconds)
                return
            event_data = event["data"]
            payload = {"status": "succeeded", "output": event_data.get("output") or {}, "event_id": event["id"]}
        elif handle["waiting_kind"] == "poll":
            project = self.store.get_project(task["project_id"])
            result = self.registry.dispatch(
                handle["poll_capability"], handle["poll_arguments"],
                CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:poll:{handle['next_check_at']}"),
            )
            if result.status != "completed":
                self.store.settle_await_handle(handle["id"], "pending", {"error": result.error or {"type": "PollNotCompleted"}}, next_check_seconds=self._poll_interval(column))
                return
            payload = result.output if isinstance(result.output, dict) else {"value": result.output}
        else:
            raise ValueError(f"unsupported V1 wait kind: {handle['waiting_kind']!r}")
        state = str(payload.get("status") or "succeeded").lower()
        successful = set((handle.get("resume_condition") or {}).get("status_in") or ["succeeded", "done", "complete"])
        if state in {"pending", "running", "queued", "processing"} or state not in successful | {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "pending", payload, next_check_seconds=self._poll_interval(column))
            return
        if state in {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "failed", payload)
            raise RuntimeExecutionError(f"AwaitFailed: {payload}", "external_permanent")
        awaited_output = payload.get("output") if isinstance(payload.get("output"), dict) else payload
        try:
            output, outcome = self._resume_awaited_execution(task, workflow, column, handle, awaited_output)
        except WaitRequested as requested:
            self._persist_wait(task, {"id": handle["run_id"], "attempt_id": handle["column_attempt_id"]}, column, requested.request)
            self.store.settle_await_handle(handle["id"], "succeeded", payload)
            return
        validate_contract(output, column.output_contract, label=f"Column {column.key} awaited output")
        transition = next((item for item in column.transitions if item.outcome == outcome), None)
        if not transition:
            raise ValueError(f"await success outcome {outcome!r} is not declared by Column {column.key!r}")
        context = dict(task["context"])
        context[column.key] = output
        self._await_auxiliary(task, handle, "cleanup")
        self.store.settle_await_handle(handle["id"], "succeeded", payload)
        self._finish_await_transition(task, handle["run_id"], workflow, transition.target, {**output, "context": context}, outcome)

    def _resume_awaited_execution(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        column: ColumnDefinition,
        handle: dict[str, Any],
        awaited_output: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        checkpoint = dict(handle.get("checkpoint") or {})
        if checkpoint.get("execution_key"):
            self.store.complete_awaiting_receipt(
                task["project_id"], str(checkpoint["execution_key"]), awaited_output
            )
        source = str(checkpoint.get("source") or "agent")
        run = {"id": handle["run_id"], "attempt_id": handle["column_attempt_id"]}
        if source == "sequence":
            assert isinstance(column.executor, CapabilitySequenceExecutor)
            return self._execute_sequence(
                task,
                run,
                column.executor,
                dict(checkpoint.get("input") or self._input_for(task, workflow, column)),
                resume_checkpoint=checkpoint,
                awaited_output=awaited_output,
            )
        if source == "agent":
            assert isinstance(column.executor, AgentExecutor)
            input_data = self._input_for(task, workflow, column)
            input_data["resume"] = {"checkpoint": checkpoint, "external_result": awaited_output}
            resume_capability = str(
                handle.get("poll_capability")
                or checkpoint.get("capability")
                or "column.await.resume"
            )
            resume_result = ToolResult(
                ok=True,
                capability=resume_capability,
                output={
                    "await_handle_id": handle["id"],
                    "external_result": awaited_output,
                },
            )
            resume_ledger = [
                _ledger_entry(
                    str(handle["run_id"]),
                    f"await-resume-{handle['id']}",
                    resume_capability,
                    "read",
                    resume_result,
                    arguments=dict(handle.get("poll_arguments") or {}),
                )
            ]
            return self._execute_agent(
                task,
                workflow,
                run,
                column,
                input_data,
                prior_action_ledger=resume_ledger,
            )
        return awaited_output, str(handle["success_outcome"])

    @staticmethod
    def _poll_interval(column: ColumnDefinition) -> int:
        if not isinstance(column.wait_policy, PollWaitPolicy):
            raise ValueError("await handle is not backed by a poll wait policy")
        return column.wait_policy.poll_interval_seconds

    def _finish_await_transition(self, task: dict[str, Any], run_id: str, workflow: WorkflowDefinition, target: str, output: dict[str, Any], outcome: str) -> None:
        terminal = workflow.terminal_kind(target)
        if terminal:
            self._validate_task_agent_terminal(task, terminal)
            error = _failure_message(output) if terminal == "failed" else None
            evidence = self.store.prepare_terminal_evidence(task, run_id, terminal, output, error)
            self.store.finish_run(task, run_id, output, outcome, target, terminal=terminal, error=error, terminal_artifact=evidence)
        else:
            workflow.column(target)
            self.store.finish_run(task, run_id, output, outcome, target)

    def _validate_task_agent_terminal(self, task: dict[str, Any], terminal: str) -> None:
        if terminal != "done":
            return
        plan = OrchestrationPlan.model_validate(
            self.store.get_orchestration_plan(
                task["project_id"],
                task["orchestration_plan_id"],
            )["plan"]
        )
        proposed = next(
            item
            for item in plan.task_portfolio
            if item.proposed_task_ref == task["proposed_task_ref"]
        )
        agent_run_count = len(
            self.store.agent_runs(project_id=task["project_id"], task_id=task["id"])
        )
        if proposed.agent_execution == "forbidden" and agent_run_count:
            raise ValueError(
                f"Task Agent execution is forbidden but {agent_run_count} Task Agent Run(s) exist"
            )
        if proposed.agent_execution == "required" and not agent_run_count:
            raise ValueError(
                "Task Agent execution is required before the Task can reach done"
            )

    def _await_auxiliary(self, task: dict[str, Any], handle: dict[str, Any], kind: str) -> dict[str, Any] | None:
        capability = handle.get(f"{kind}_capability")
        if not capability:
            return None
        project = self.store.get_project(task["project_id"])
        result = self.registry.dispatch(
            capability, handle.get(f"{kind}_arguments") or {},
            CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:{kind}"),
        )
        return result.model_dump(mode="json")


class LeaseKeeper:
    def __init__(self, store: V1Store, task_id: str, owner: str, *, interval: float | None = None, lease_seconds: int | None = None):
        self.store = store
        self.task_id = task_id
        self.owner = owner
        self.interval = interval or store.policy.scheduling.task_lease_renew_seconds
        self.lease_seconds = lease_seconds or store.policy.scheduling.task_lease_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"lease-{self.task_id[-8:]}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            if not self.store.renew_lease(self.task_id, self.owner, self.lease_seconds):
                return


class RuntimeSupervisor:
    def __init__(
        self,
        store: V1Store,
        registry: CapabilityRegistry,
        *,
        interval: float | None = None,
        workers: int | None = None,
        agent_core: AgentCore | None = None,
    ):
        self.store = store
        self.registry = registry
        self.interval = interval or store.policy.scheduling.supervisor_interval_seconds
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers or store.policy.scheduling.runtime_workers), thread_name_prefix="devwerk-v1")
        self.worker_id = f"runtime-{id(self):x}"
        self.runtime = WorkflowRuntime(store, registry, self.worker_id, agent_core)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: set[str] = set()
        self._active_await: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="devwerk-v1-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=False)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            for handle in self.store.due_await_handles():
                with self._lock:
                    if handle["id"] in self._active_await:
                        continue
                    self._active_await.add(handle["id"])
                future = self.executor.submit(self.runtime.reconcile_await, handle)
                future.add_done_callback(lambda item, value=handle["id"]: self._await_done(value, item))
            for task_id in self.store.runnable_task_ids():
                with self._lock:
                    if task_id in self._active:
                        continue
                    self._active.add(task_id)
                future = self.executor.submit(self.runtime.step, task_id)
                future.add_done_callback(lambda item, value=task_id: self._done(value, item))
            self._wake.wait(self.interval)
            self._wake.clear()

    def _done(self, task_id: str, future: Any) -> None:
        with self._lock:
            self._active.discard(task_id)
        self.wake()
        future.result()

    def _await_done(self, handle_id: str, future: Any) -> None:
        with self._lock:
            self._active_await.discard(handle_id)
        self.wake()
        future.result()


def _failure_message(output: dict[str, Any]) -> str:
    for step in reversed(output.get("steps") or []):
        error = step.get("error") if isinstance(step, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return f"{error.get('type')}: {error['message']}"
    return str(output.get("summary") or "Column transitioned to failed")
