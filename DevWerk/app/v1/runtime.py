from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    resolve_references,
)
from app.v1.contracts import ContractError, validate_contract
from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    ColumnDefinition,
    WorkflowDefinition,
)
from app.v1.files import ProjectFiles
from app.v1.store import V1Store


log = logging.getLogger("devwerk.v1.runtime")


class WaitRequested(RuntimeError):
    def __init__(self, request: dict[str, Any]):
        self.request = request
        super().__init__("Column requested durable waiting")


class RuntimeExecutionError(RuntimeError):
    def __init__(self, message: str, category: str):
        self.category = category
        super().__init__(message)


class WorkflowRuntime:
    """Interprets declarative executor, contract, transition, retry and terminal data."""

    def __init__(self, store: V1Store, registry: CapabilityRegistry, worker_id: str, agent_core: AgentCore | None = None):
        self.store = store
        self.registry = registry
        self.worker_id = worker_id
        self.agent_core = agent_core or AgentCore(store, registry)

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
            visits = sum(
                1
                for item in self.store.runs(task["project_id"], task["id"])
                if item["column_key"] == column.key and item["status"] == "succeeded"
            )
            if visits >= column.max_visits:
                raise RuntimeExecutionError(f"column {column.key!r} exceeded max_visits={column.max_visits}", "max_visits_exceeded")
            input_data = self._input_for(task, workflow, column)
            validate_contract(input_data, column.input_contract, label=f"Column {column.key} input")
            run = self.store.begin_run(task, input_data)
            if column.terminal:
                terminal_error = None
                if column.terminal == "failed":
                    terminal_error = task.get("error") or task.get("context", {}).get("last_error")
                terminal_output = {"summary": f"Task reached {column.terminal}", "context": task["context"]}
                evidence = self.store.prepare_terminal_evidence(task, run["id"], column.terminal, terminal_output, terminal_error)
                self.store.finish_run(
                    task,
                    run["id"],
                    terminal_output,
                    column.terminal,
                    None,
                    terminal=column.terminal,
                    error=terminal_error,
                    terminal_artifact=evidence,
                )
                return

            output, outcome = self._execute(task, workflow, run, column, input_data)
            validate_contract(output, column.output_contract, label=f"Column {column.key} output")
            transition = next((item for item in column.transitions if item.outcome == outcome), None)
            if transition is None:
                raise ValueError(f"column {column.key!r} produced undeclared outcome {outcome!r}")
            context = dict(task["context"])
            context[column.key] = output
            target = workflow.column(transition.target)
            if target.terminal == "failed":
                context["last_error"] = _failure_message(output)
            persisted_output = {**output, "context": context}
            if target.terminal:
                terminal_error = context.get("last_error") if target.terminal == "failed" else None
                evidence = self.store.prepare_terminal_evidence(task, run["id"], target.terminal, persisted_output, terminal_error)
                self.store.finish_run(
                    task,
                    run["id"],
                    persisted_output,
                    outcome,
                    None,
                    terminal=target.terminal,
                    error=terminal_error,
                    terminal_artifact=evidence,
                )
            else:
                self.store.finish_run(task, run["id"], persisted_output, outcome, transition.target)
        except WaitRequested as requested:
            assert column is not None and run is not None
            request = requested.request
            poll_capability = str(request.get("poll_capability") or column.wait_policy.poll_capability or "")
            if not poll_capability or poll_capability not in (column.executor.capabilities if isinstance(column.executor, AgentExecutor) else [step.capability for step in column.executor.steps]):
                raise ValueError("durable wait requires a poll_capability selected by the Column executor")
            self.store.create_await_handle(
                task, run["id"], provider=str(request.get("provider") or "external"), token=request.get("token"),
                poll_capability=poll_capability, poll_arguments=dict(request.get("poll_arguments") or column.wait_policy.poll_arguments),
                next_check_seconds=int(request.get("next_check_seconds") or column.wait_policy.heartbeat_seconds),
                stale_seconds=column.wait_policy.stale_after_seconds, timeout_seconds=column.wait_policy.timeout_seconds,
                success_outcome=column.wait_policy.success_outcome, timeout_outcome=column.wait_policy.timeout_outcome,
                waiting_kind=column.wait_policy.waiting_kind, soft_deadline_seconds=column.wait_policy.soft_deadline_seconds,
                resume_condition=column.wait_policy.resume_condition, cancel_capability=column.wait_policy.cancel_capability,
                cancel_arguments=column.wait_policy.cancel_arguments, cleanup_capability=column.wait_policy.cleanup_capability,
                cleanup_arguments=column.wait_policy.cleanup_arguments, idempotency_key=column.wait_policy.idempotency_key,
            )
            return
        except Exception as exc:  # noqa: BLE001
            column_key = column.key if column else task["current_column"]
            log.exception("column failed task=%s column=%s", task_id, column_key)
            failed_before_run = run is None
            if run is None:
                run = self.store.begin_run(task, {"task": task, "column": column_key})
            error = f"{type(exc).__name__}: {exc}"[:4000]
            error_category = getattr(exc, "category", None) or _error_category(exc)
            failure_target = None
            if workflow and column:
                if error_category == "max_visits_exceeded":
                    runtime_outcome = column.runtime_outcomes.max_visits_exceeded
                elif isinstance(exc, ContractError) and failed_before_run:
                    runtime_outcome = column.runtime_outcomes.input_missing
                elif error_category in column.retry.retryable_errors:
                    runtime_outcome = column.runtime_outcomes.retry_exhausted
                else:
                    runtime_outcome = column.runtime_outcomes.execution_failed
                transition = next((item for item in column.transitions if item.outcome == runtime_outcome), None)
                failure_target = transition.target if transition else None
            if failure_target is None:
                evidence = self.store.prepare_terminal_evidence(task, run["id"], "failed", {"summary": "runtime definition unavailable"}, error)
                self.store.fail_unrecoverable_task(task, run["id"], error, evidence)
                return
            self.store.fail_attempt(
                task,
                run["id"],
                error,
                column.retry.max_attempts if column else 1,
                failure_target,
                failure_fingerprint=_fingerprint(error),
                repeated_failure_limit=column.retry.repeated_failure_limit if column else 1,
                backoff_seconds=column.retry.backoff_seconds if column else 0,
                error_category=error_category,
                retryable=bool(column and error_category in column.retry.retryable_errors),
            )
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
            files = ProjectFiles(project["base_dir"])
            remaining = column.context.max_chars
            artifacts: list[dict[str, str]] = []
            for pattern in column.context.artifact_globs:
                if remaining <= 0:
                    break
                for item in files.existing_texts(pattern, remaining):
                    artifacts.append(item)
                    remaining -= len(item["content"])
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
    ) -> tuple[dict[str, Any], str]:
        project = self.store.get_project(task["project_id"])
        capability_context = CapabilityContext(
            project_id=task["project_id"],
            project=project,
            store=self.store,
            task_id=task["id"],
            column_run_id=run["id"],
        )
        scope: dict[str, Any] = {"input": input_data, "steps": {}}
        results: list[dict[str, Any]] = []
        for index, step in enumerate(executor.steps):
            arguments = resolve_references(step.arguments, scope)
            step_context = CapabilityContext(**{**capability_context.__dict__, "execution_key": f"{run['id']}:step:{index}"})
            result = self.registry.dispatch(step.capability, arguments, step_context)
            value = result.model_dump(mode="json")
            key = step.save_as or str(index)
            scope["steps"][key] = value
            results.append({"step": index, "save_as": key, **value})
            if not result.ok and not step.continue_on_error:
                error = result.error or {"type": "CapabilityFailed", "message": f"{step.capability} failed"}
                category = "tool_transient" if "transient" in str(error.get("type") or "").lower() else "runtime_permanent"
                raise RuntimeExecutionError(f"{step.capability}: {error.get('message') or error}", category)
        outcome_value = executor.completed_outcome
        if executor.outcome_from:
            outcome_value = resolve_references({"$ref": executor.outcome_from}, scope)
        outcome = str(outcome_value or "")
        return {"summary": f"capability sequence completed with outcome {outcome}", "steps": results}, outcome

    def _execute_agent(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        run: dict[str, Any],
        column: ColumnDefinition,
        input_data: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        assert isinstance(column.executor, AgentExecutor)
        project = self.store.get_project(task["project_id"])
        workflow_row = self.store.get_workflow_revision(task["project_id"], task["workflow_revision_id"])
        outcomes = {item.outcome for item in column.transitions}
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
                },
                capability_ids=column.executor.capabilities,
                task_id=task["id"],
                column_run_id=run["id"],
                max_iterations=column.executor.max_iterations,
                max_tool_calls=column.executor.max_tool_calls,
                timeout_seconds=column.wait_policy.timeout_seconds,
                completion_outcomes=outcomes,
                output_contract=column.output_contract,
            )
        )
        if result.status != "succeeded" or not result.completion:
            if result.status == "waiting" and result.wait_request:
                raise WaitRequested(result.wait_request)
            raise RuntimeExecutionError(result.error or "Column Agent failed without a completion", result.error_category or "runtime_permanent")
        return dict(result.completion["output"]), str(result.completion["outcome"])

    def reconcile_await(self, handle: dict[str, Any]) -> None:
        task = self.store.get_task(handle["task_id"])
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        column = workflow.column(handle["column_key"])
        now = datetime.now(timezone.utc)
        if now >= datetime.fromisoformat(handle["hard_deadline_at"]):
            cancel = self._await_auxiliary(task, handle, "cancel")
            cleanup = self._await_auxiliary(task, handle, "cleanup")
            self.store.settle_await_handle(handle["id"], "timed_out", {"reason": "hard_deadline_exceeded", "cancel": cancel, "cleanup": cleanup})
            outcome = handle["timeout_outcome"]
            transition = next((item for item in column.transitions if item.outcome == outcome), None)
            if not transition:
                raise ValueError(f"await timeout outcome {outcome!r} is not declared")
            output = {"status": "timed_out", "summary": "hard deadline exceeded"}
            context = dict(task["context"]); context[column.key] = output; context["last_error"] = "AwaitTimeout: hard deadline exceeded"
            self.store.finish_run(task, handle["run_id"], {**output, "context": context}, outcome, transition.target, error=None)
            return
        if handle.get("soft_deadline_at") and now >= datetime.fromisoformat(handle["soft_deadline_at"]) and handle.get("health") == "healthy":
            self.store.mark_await_health(handle["id"], "soft_deadline", "await.soft_deadline")
        if now >= datetime.fromisoformat(handle["stale_at"]) and handle.get("health") != "stale":
            self.store.mark_await_health(handle["id"], "stale", "await.stale")
        project = self.store.get_project(task["project_id"])
        result = self.registry.dispatch(
            handle["poll_capability"], handle["poll_arguments"],
            CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:poll:{handle['next_check_at']}"),
        )
        if not result.ok:
            self.store.settle_await_handle(handle["id"], "pending", {"error": result.error}, next_check_seconds=column.wait_policy.heartbeat_seconds)
            return
        payload = result.output if isinstance(result.output, dict) else {"value": result.output}
        state = str(payload.get("status") or "succeeded").lower()
        successful = set((handle.get("resume_condition") or {}).get("status_in") or ["succeeded", "done", "complete"])
        if state in {"pending", "running", "queued", "processing"} or state not in successful | {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "pending", payload, next_check_seconds=column.wait_policy.heartbeat_seconds)
            return
        if state in {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "failed", payload)
            self.store.fail_attempt(
                task, handle["run_id"], f"AwaitFailed: {payload}", column.retry.max_attempts, workflow.terminal_key("failed"),
                failure_fingerprint=_fingerprint(json.dumps(payload, sort_keys=True, default=str)), repeated_failure_limit=column.retry.repeated_failure_limit,
                backoff_seconds=column.retry.backoff_seconds, error_category="external_permanent", retryable="external_permanent" in column.retry.retryable_errors,
            )
            return
        output = payload.get("output") if isinstance(payload.get("output"), dict) else payload
        validate_contract(output, column.output_contract, label=f"Column {column.key} awaited output")
        outcome = handle["success_outcome"]
        transition = next((item for item in column.transitions if item.outcome == outcome), None)
        if not transition:
            raise ValueError(f"await success outcome {outcome!r} is not declared by Column {column.key!r}")
        context = dict(task["context"])
        context[column.key] = output
        self._await_auxiliary(task, handle, "cleanup")
        self.store.settle_await_handle(handle["id"], "succeeded", payload)
        self.store.finish_run(task, handle["run_id"], {**output, "context": context}, outcome, transition.target)

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
    def __init__(self, store: V1Store, task_id: str, owner: str, *, interval: float = 30.0, lease_seconds: int = 120):
        self.store = store
        self.task_id = task_id
        self.owner = owner
        self.interval = interval
        self.lease_seconds = lease_seconds
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
            try:
                if not self.store.renew_lease(self.task_id, self.owner, self.lease_seconds):
                    return
            except Exception:  # noqa: BLE001
                log.exception("lease renewal failed task=%s", self.task_id)


class RuntimeSupervisor:
    def __init__(
        self,
        store: V1Store,
        registry: CapabilityRegistry,
        *,
        interval: float = 0.5,
        workers: int = 4,
        agent_core: AgentCore | None = None,
    ):
        self.store = store
        self.registry = registry
        self.interval = max(interval, 0.1)
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="devwerk-v1")
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
            try:
                self.store.recover_expired()
                self.store.expire_nonterminal_deadlines()
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
            except Exception:  # noqa: BLE001
                log.exception("supervisor iteration failed")
            self._wake.wait(self.interval)
            self._wake.clear()

    def _done(self, task_id: str, future: Any) -> None:
        try:
            future.result()
        except Exception:  # noqa: BLE001
            log.exception("unhandled task step failure task=%s", task_id)
        finally:
            with self._lock:
                self._active.discard(task_id)

    def _await_done(self, handle_id: str, future: Any) -> None:
        try:
            future.result()
        except Exception:  # noqa: BLE001
            log.exception("await reconciliation failed handle=%s", handle_id)
        finally:
            with self._lock:
                self._active_await.discard(handle_id)


def _fingerprint(error: str) -> str:
    normalized = " ".join(error.lower().split())[:2000]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _failure_message(output: dict[str, Any]) -> str:
    for step in reversed(output.get("steps") or []):
        error = step.get("error") if isinstance(step, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return f"{error.get('type')}: {error['message']}"[:4000]
    return str(output.get("summary") or "Column transitioned to failed")[:4000]


def _error_category(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "provider_transient"
    if isinstance(exc, (ValueError, TypeError, PermissionError)):
        return "contract_permanent"
    return "runtime_permanent"
