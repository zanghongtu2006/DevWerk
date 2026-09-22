from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import sqlite3
from app.v1.execution_control import ExecutionControl, ExecutionOwnershipLost, ExecutionBudgetExceeded, ExecutionReplayUncertain
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Any

from app.core.debug_trace import trace_json
from app.services.provider_errors import (
    LLMProviderError,
    is_recoverable_llm_error,
    is_recoverable_llm_error_code,
    llm_error_code,
)
from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    resolve_references,
)
from app.v1.contracts import validate_contract
from app.v1.completion_protocol import (
    CompletionContract,
    CompletionOutcomeRule,
)
from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    ColumnDefinition,
    ContextSelection,
    EventWaitPolicy,
    PollWaitPolicy,
    TaskPlan,
    TimerWaitPolicy,
    ToolResult,
    WorkflowDefinition,
    WorkflowPlan,
)
from app.v1.files import ProjectFiles
from app.v1.execution_ledger import ledger_entry
from app.v1.services.task_graph_admission import validate_task_scope
from app.v1.store import V1Store


log = logging.getLogger("devwerk.v1.runtime")
trace_log = logging.getLogger("devwerk.runtime.trace")

_CONTEXT_CONSUMPTION_CONTRACT = (
    "Runtime context is partitioned by provenance. accepted_artifacts are immutable facts "
    "from transitive done Task dependencies. working_artifacts belong to the current Task "
    "and are not accepted facts. reference_artifacts are unverified Project workspace "
    "content. current_goal is desired future state, never historical fact. Use embedded "
    "project.loop.assets and preloaded content directly; do not reread a manifest path "
    "unless it is missing, known to have changed, or independent verification is required. "
    "Loop asset paths are not Project filesystem paths. On a Session resume, use the logical "
    "checkpoint and directed Handoffs, then fetch only specific missing or changed files."
)


class WaitRequested(RuntimeError):
    def __init__(self, request: dict[str, Any]):
        self.request = request
        super().__init__("Column requested durable waiting")


class RuntimeExecutionError(RuntimeError):
    def __init__(self, message: str, category: str, *, error_code: str | None = None, checkpoint: dict[str, Any] | None = None, agent_run_id: str | None = None):
        self.category = category
        self.error_category = category
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
            workflow_revision = self.store.get_workflow_revision(
                task["project_id"], task["workflow_revision_id"]
            )
            workflow_plan = WorkflowPlan.model_validate(
                self.store.get_workflow_plan(
                    task["project_id"], str(workflow_revision["workflow_plan_id"])
                )["plan"]
            )
            loop_binding = self.store.get_project_loop_binding(task["project_id"], task['workflow_revision_id'])
            validate_task_scope(
                str(task.get("proposed_task_ref") or task["id"]),
                dict(task.get("input") or {}),
                workflow_plan.task_contract.admission_constraints,
                loop_bindings=(
                    dict(loop_binding.get("bindings") or {}) if loop_binding else {}
                ),
            )
            column = workflow.column(task["current_column"])
            input_data = self._input_for(task, workflow, column)
            validate_contract(input_data, column.input_contract, label=f"Column {column.key} input")
            run = self.store.begin_run(task, input_data)
            with self.store.connect() as db:
                visits = db.execute("SELECT COUNT(*) FROM v1_column_runs WHERE task_id=? AND column_key=?",
                                    (task_id, column.key)).fetchone()[0]
            if visits > self.policy.execution.max_column_visits:
                raise ExecutionBudgetExceeded("Column visit ceiling reached; revise or split the Task")
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
            if isinstance(exc, ExecutionOwnershipLost):
                log.warning("discarding stale execution task=%s: %s", task_id, exc)
                return
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
                else "provider_transient" if is_recoverable_llm_error(exc)
                else "provider_permanent" if isinstance(exc, LLMProviderError)
                else "infrastructure_transient" if isinstance(exc, sqlite3.OperationalError)
                else getattr(exc, "error_category", "runtime_permanent")
            )
            if is_recoverable_llm_error(exc) or is_recoverable_llm_error_code(error_code) or error_category in {"tool_transient", "infrastructure_transient", "external_transient"}:
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
            # Unhandled execution errors stop here. Only a declared Workflow
            # outcome above (or explicit cancel) may assert business failure.
            self.store.recovery_manager.block_runtime_failure(
                task, run["id"], error, error_code=error_code,
                error_category=error_category, checkpoint=checkpoint,
            )
            raise
        finally:
            keeper.stop()

    def _input_for(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        column: ColumnDefinition,
        *,
        context_selection: ContextSelection | None = None,
    ) -> dict[str, Any]:
        project = self.store.get_project(task["project_id"])
        selection = context_selection or column.context
        data: dict[str, Any] = {"column": {"key": column.key, "name": column.name}}
        preloaded_loop_assets: list[dict[str, Any]] = []
        preloaded_artifacts: list[dict[str, Any]] = []
        revision = self.store.get_workflow_revision(task["project_id"], task["workflow_revision_id"])
        workflow_plan_row = self.store.get_workflow_plan(task["project_id"], revision["workflow_plan_id"])
        task_plan_row = self.store.get_task_plan(task["project_id"], task["task_plan_id"])
        workflow_plan = dict(workflow_plan_row["plan"])
        task_plan = dict(task_plan_row["plan"])
        column_plan = next(item for item in workflow_plan["columns"] if item["key"] == column.key)
        planned_task = next(item for item in task_plan["tasks"] if item["proposed_task_ref"] == task["proposed_task_ref"])
        data["planning"] = {
            "workflow_plan_id": workflow_plan_row["id"],
            "workflow_plan_hash": workflow_plan_row["plan_hash"],
            "task_plan_id": task_plan_row["id"],
            "task_plan_hash": task_plan_row["plan_hash"],
            "column": column_plan,
        }
        if selection.include_current_goal:
            data["current_goal"] = planned_task
        data["dependencies"] = self.store.task_dependency_context(
            task["project_id"],
            task["id"],
        )
        if selection.include_project:
            data["project"] = {
                "id": project["id"],
                "name": project["name"],
                "description": project["description"],
                "base_dir": project["base_dir"],
            }
        if selection.include_loop_bindings or selection.include_loop_assets:
            loop_binding = self.store.get_project_loop_binding(task["project_id"], task['workflow_revision_id'])
            if loop_binding:
                loop_context = {
                    "key": loop_binding["loop_key"],
                    "version": loop_binding["loop_version"],
                    "digest": loop_binding["loop_digest"],
                }
                if selection.include_loop_bindings:
                    loop_context["bindings"] = loop_binding["bindings"]
                if selection.include_loop_assets:
                    loop_assets = self.store.get_project_loop_assets(task["project_id"], task['workflow_revision_id'])
                    preloaded_loop_assets = [
                        {
                            "path": item["path"],
                            "utf8_characters": len(item["content"]),
                            "sha256": hashlib.sha256(
                                item["content"].encode("utf-8")
                            ).hexdigest(),
                        }
                        for item in loop_assets
                    ]
                    loop_context["assets"] = loop_assets
                    loop_context["asset_manifest"] = preloaded_loop_assets
                data.setdefault("project", {})["loop"] = loop_context
        if selection.include_task:
            data["task"] = {
                "id": task["id"],
                "input": task["input"],
            }
            if selection.include_task_description:
                data["task"]["title"] = task["title"]
                data["task"]["brief"] = task["brief"]
            if selection.include_task_context:
                data["task"]["context"] = task["context"]
        if selection.upstream_outputs:
            selected = set(selection.upstream_outputs)
            data["upstream_outputs"] = {
                item["column_key"]: item["output"]
                for item in self.store.runs(task["project_id"], task["id"])
                if item["column_key"] in selected and item["status"] == "succeeded"
            }
        artifact_channels: dict[str, list[dict[str, Any]]] = {
            "accepted_artifacts": [],
            "working_artifacts": [],
            "reference_artifacts": [],
        }
        excluded_artifacts: list[dict[str, str]] = []
        files = ProjectFiles(project["base_dir"], self.policy)
        seen_paths: set[str] = set()
        remaining_chars = self.policy.context.artifact_context_max_characters
        remaining_files = self.policy.context.artifact_context_max_files

        def preload_registered(
            rows: list[dict[str, Any]],
            patterns: list[str],
            channel: str,
            provenance: str,
        ) -> None:
            nonlocal remaining_chars, remaining_files
            for pattern in patterns:
                for row in rows:
                    path = str(row.get("path") or "")
                    if not path or path in seen_paths or not PurePosixPath(path).match(pattern):
                        continue
                    if remaining_chars <= 0 or remaining_files <= 0:
                        return
                    try:
                        snapshot = self.store.artifact_repository.snapshot_text(row)
                        if snapshot is not None:
                            measured = {"path": path, "sha256": hashlib.sha256(snapshot.encode('utf-8')).hexdigest(),
                                        "size_bytes": len(snapshot.encode('utf-8')), "utf8_characters": len(snapshot),
                                        "non_whitespace_characters": sum(not char.isspace() for char in snapshot),
                                        "line_count": len(snapshot.splitlines())}
                        else:
                            measured = files.measure_text(path)
                        expected_sha256 = str(row.get("sha256") or "")
                        if expected_sha256 and measured["sha256"] != expected_sha256:
                            excluded_artifacts.append({
                                "path": path,
                                "provenance": provenance,
                                "reason": "registered_hash_mismatch",
                            })
                            continue
                        content = snapshot if snapshot is not None else files.read_text(path)
                    except (OSError, UnicodeDecodeError, ValueError) as exc:
                        excluded_artifacts.append({
                            "path": path,
                            "provenance": provenance,
                            "reason": f"unreadable:{type(exc).__name__}",
                        })
                        continue
                    if len(content) > remaining_chars:
                        excluded_artifacts.append({
                            "path": path,
                            "provenance": provenance,
                            "reason": "context_character_limit",
                        })
                        continue
                    artifact_channels[channel].append({
                        **measured,
                        "content": content,
                        "artifact_id": row.get("id"),
                        "source_task_id": row.get("task_id"),
                        "source_run_id": row.get("run_id"),
                        "provenance": provenance,
                    })
                    seen_paths.add(path)
                    remaining_chars -= len(content)
                    remaining_files -= 1

        if selection.working_artifact_globs:
            preload_registered(
                self.store.current_task_artifacts(task["project_id"], task["id"]),
                selection.working_artifact_globs,
                "working_artifacts",
                "current_task",
            )
        if selection.accepted_artifact_globs:
            preload_registered(
                self.store.accepted_dependency_artifacts(task["project_id"], task["id"]),
                selection.accepted_artifact_globs,
                "accepted_artifacts",
                "accepted_dependency",
            )
        if selection.artifact_globs:
            for pattern in selection.artifact_globs:
                selected = files.existing_texts(
                    pattern,
                    remaining_chars,
                    limit=remaining_files,
                    exclude_paths=seen_paths,
                )
                for item in selected:
                    artifact_channels["reference_artifacts"].append({
                        **item,
                        "provenance": "project_workspace",
                    })
                    seen_paths.add(item["path"])
                    remaining_chars -= len(item["content"])
                    remaining_files -= 1
                if remaining_chars <= 0 or remaining_files <= 0:
                    break
        for channel, artifacts in artifact_channels.items():
            if artifacts:
                data[channel] = artifacts
        preloaded_artifacts = [
            {
                key: item.get(key)
                for key in (
                    "path",
                    "size_bytes",
                    "utf8_characters",
                    "non_whitespace_characters",
                    "line_count",
                    "sha256",
                    "artifact_id",
                    "source_task_id",
                    "source_run_id",
                    "provenance",
                )
                if item.get(key) is not None
            }
            for artifacts in artifact_channels.values()
            for item in artifacts
        ]
        if selection.memory:
            data["memory"] = self.store.memory.build_context(
                project,
                selectors=selection.memory,
                task_id=task["id"],
                include_core=selection.include_project,
            )
        data["context_manifest"] = {
            "preloaded_project_artifacts": preloaded_artifacts,
            "preloaded_loop_assets": preloaded_loop_assets,
            "artifact_channels": {
                "accepted_artifacts": "transitive done Task dependency facts",
                "working_artifacts": "mutable current Task work",
                "reference_artifacts": "unverified Project workspace reference",
            },
            "excluded_artifacts": excluded_artifacts,
            "accepted_content_is_authoritative": True,
            "working_and_reference_content_is_authoritative": False,
            "read_preloaded_path_only_when_missing_or_changed": True,
            "write_receipt_contains_text_metrics": True,
            "consumption_contract": _CONTEXT_CONSUMPTION_CONTRACT,
            "projection": "full_activation",
        }
        return data

    @staticmethod
    def _resume_input(input_data: dict[str, Any]) -> dict[str, Any]:
        """Project a bounded state delta for a persistent logical Agent Session."""
        projected = dict(input_data)
        project = dict(projected.get("project") or {})
        loop = dict(project.get("loop") or {})
        if loop:
            loop.pop("assets", None)
            project["loop"] = loop
            projected["project"] = project
        projected.pop("artifacts", None)
        projected.pop("accepted_artifacts", None)
        projected.pop("working_artifacts", None)
        projected.pop("reference_artifacts", None)
        manifest = dict(projected.get("context_manifest") or {})
        manifest["projection"] = "session_resume_delta"
        projected["context_manifest"] = manifest
        return projected

    @staticmethod
    def _agent_instruction(*parts: str) -> str:
        return "\n\n".join(
            item for item in (*parts, _CONTEXT_CONSUMPTION_CONTRACT) if item.strip()
        )

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
            execution_control=self._control(task),
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
                category = "tool_transient" if "transient" in str(error.get("type") or "").lower() else "capability_permanent"
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
        workflow = self.store.workflow_by_id(task['project_id'],task['workflow_revision_id'])
        column = workflow.column(task['current_column'])
        transition = next((edge for edge in column.transitions if edge.outcome == outcome),None)
        if transition and not transition.allows_unresolved_failures:
            for check in column.acceptance_checks:
                execution_key = f"{run['id']}:acceptance:{check.key}:{time.monotonic_ns()}"
                ctx = CapabilityContext(**{**capability_context.__dict__, 'execution_key':execution_key})
                result = self.registry.dispatch(check.capability, check.arguments, ctx)
                spec = AgentRunSpec(kind='column', project=project, instruction='',instruction_revision=0,
                    context={},capability_ids=[],task_id=task['id'],column_run_id=run['id'],column_attempt_id=run['attempt_id'])
                self.store.feedback.record_check(spec,check.key,execution_key,result,task_owner=task)
                if not result.ok or result.status != 'completed':
                    raise RuntimeExecutionError(f'Required acceptance check failed: {check.key}', 'runtime_permanent')
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
        assignment = self.store.agents.assign(task, run, column)
        session = {"id": assignment["session_id"]}
        agent_input = input_data
        if self.store.agents.session_history(task["project_id"], session["id"], assignment_id=assignment['id']):
            agent_input = self._resume_input(input_data)
        writable_path_values = resolve_references(
            column.metadata.get("writable_paths", []),
            {"input": agent_input},
        ) if "writable_paths" in column.metadata else None
        if writable_path_values is not None and not isinstance(writable_path_values, list):
            raise ValueError("Column metadata writable_paths must resolve to a list")
        result = self.agent_core.run(
            AgentRunSpec(
                kind="column",
                project=project,
                instruction=self._agent_instruction(column.instruction),
                instruction_revision=int(workflow_row["revision"]),
                context={
                    "workflow": {"id": workflow_row["id"], "name": workflow.name, "description": workflow.description},
                    "column": column.model_dump(mode="json"),
                    "input": agent_input,
                    "task_feedback": self.store.feedback.list(task['project_id'], task['id']),
                    **({'feedback_contract':{
                        'responsible_columns':[c.key for c in workflow.columns],
                        'checks':[{'column':c.key,'check_key':check.key,'purpose':check.purpose[:500]}
                                  for c in workflow.columns for check in c.acceptance_checks],
                        'instruction':'Before reporting a defect/rework outcome, persist task.feedback.record with a responsible Column and frozen check references. Read existing feedback to avoid duplicates. Runtime handles the declared handoff; do not send instructions through Mailbox or run other Workers.'}}
                       if 'task.feedback.record' in column.executor.capabilities else {}),
                    "action_ledger": self._column_action_ledger(task["project_id"], run["id"]) + (prior_action_ledger or []),
                },
                capability_ids=column.executor.capabilities,
                task_id=task["id"],
                column_run_id=run["id"],
                column_attempt_id=run["attempt_id"],
                execution_control=ExecutionControl(
                    time.monotonic() + self.policy.execution.agent_wall_seconds,
                    validate_owner=lambda: self.store.agents.assert_owner(assignment)),
                completion_contract=_column_completion_contract(workflow, column),
                wait_config=column.wait_policy.model_dump(mode="json") if column.wait_policy else {},
                agent_session_id=session["id"] if session else None,
                assignment=assignment,
                agent_instance_id=assignment["agent_instance_id"],
                requirement_id=assignment["requirement_id"],
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
        return dict(result.completion["output"]), str(result.completion["outcome"])

    def _control(self, task):
        return ExecutionControl(time.monotonic() + self.policy.execution.agent_wall_seconds,
                                validate_owner=lambda: self.store.assert_execution_owner(task))

    def _column_action_ledger(self, project_id, run_id):
        """Restore execution facts across provider retry and Await continuation."""
        with self.store.connect() as db:
            runs = db.execute("SELECT id FROM v1_agent_runs WHERE project_id=? AND column_run_id=? ORDER BY created_at,rowid",
                              (project_id, run_id)).fetchall()
        ledger = []
        seen_operations = set()
        for row in runs:
            after = 0
            invocations = []
            while True:
                page = self.store.tool_invocations(project_id, row[0], limit=self.policy.service_limits.max_page_size,
                                                   after_sequence=after, hydrate_payloads=True)
                if not page:
                    break
                invocations.extend(page)
                after = page[-1]["sequence"]
            for item in invocations:
                if item["capability"] in {"column.complete", "column.await"}:
                    continue
                result = ToolResult.model_validate(item["result"])
                execution_key = (result.checkpoint or {}).get("execution_key")
                if not execution_key and result.status == 'awaiting':
                    with self.store.connect() as db:
                        checkpoints = [json.loads(r[0]) for r in db.execute('SELECT checkpoint_json FROM v1_await_handles WHERE project_id=? AND run_id=?', (project_id, run_id))]
                    matches = [cp for cp in checkpoints if cp.get('capability_result') == result.model_dump(mode='json')]
                    if len(matches) == 1:
                        execution_key = matches[0].get('execution_key')
                if execution_key:
                    if execution_key in seen_operations:
                        continue
                    seen_operations.add(execution_key)
                    with self.store.connect() as db:
                        receipt = db.execute("SELECT * FROM v1_execution_receipts WHERE project_id=? AND execution_key=?", (project_id, execution_key)).fetchone()
                    if receipt and receipt["status"] in {"completed", "failed"}:
                        result = ToolResult(ok=receipt["status"] == "completed", capability=item["capability"],
                                            output=json.loads(receipt["result_json"]) if receipt["result_json"] else None,
                                            error={"message": receipt["error"]} if receipt["error"] else None,
                                            checkpoint=dict(result.checkpoint or {}))
                if item["capability"] in {"project.command.run", "system.command.run"} and isinstance(result.output, dict) and result.output.get("exit_code") not in (None, 0):
                    result = ToolResult(ok=False, capability=item["capability"], output=result.output,
                                        error={"type": "CommandFailed", "message": "Recorded command exit code is nonzero"})
                ledger.append(ledger_entry(row[0], item["tool_call_id"], item["capability"],
                                           self.registry.side_effect_kind(item["capability"]), result,
                                           arguments=item["arguments"]))
        return ledger

    def reconcile_await(self, handle: dict[str, Any]) -> None:
        from app.v1.storage_support import new_id
        owner = f"{self.worker_id}:{new_id('awaitowner')}"
        task = self.store.claim_await(handle["id"], owner)
        if task is None:
            return
        keeper = LeaseKeeper(self.store, task["id"], owner)
        keeper.start()
        try:
            self._reconcile_claimed_await(self.store.await_handle(handle["id"]), task)
        except ExecutionOwnershipLost:
            log.warning("discarding stale Await execution handle=%s", handle["id"])
        except Exception as exc:
            code = getattr(exc, "error_code", None) or llm_error_code(exc, default=type(exc).__name__)
            category = getattr(exc, "error_category", "runtime_permanent")
            if is_recoverable_llm_error(exc) or category in {"provider_transient", "tool_transient", "infrastructure_transient"}:
                self.store.recovery_manager.retry_await_operation(
                    task, handle["id"], f"{type(exc).__name__}: {exc}",
                    error_code=code, error_category="provider_transient" if is_recoverable_llm_error(exc) else category,
                )
                return
            self.store.recovery_manager.block_runtime_failure(
                task, handle["run_id"], f"{type(exc).__name__}: {exc}",
                error_code=code, error_category=category,
            )
            raise
        finally:
            keeper.stop()
            self.store.release_await_claim(task)

    def _reconcile_claimed_await(self, handle, task):
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        column = workflow.column(handle["column_key"])
        now = datetime.now(timezone.utc)
        if "resolved_external" in handle["checkpoint"]:
            payload = handle["checkpoint"]["resolved_external"]
        elif handle["waiting_kind"] == "timer":
            payload = {"status": "succeeded", "output": {"resumed_at": now.isoformat(timespec="milliseconds")}}
        elif handle["waiting_kind"] == "event":
            event = self.store.correlated_event(
                task["project_id"], str(handle["event_type"]), str(handle["correlation_key"]), handle["created_at"]
            )
            if event is None:
                if not isinstance(column.wait_policy, EventWaitPolicy):
                    raise ValueError("await handle is not backed by an event wait policy")
                self.store.settle_await_handle(handle["id"], "pending", {"status": "waiting_for_event"}, next_check_seconds=column.wait_policy.check_interval_seconds, expected_task=task)
                return
            event_data = event["data"]
            payload = {"status": "succeeded", "output": event_data.get("output") or {}, "event_id": event["id"]}
        elif handle["waiting_kind"] == "poll":
            project = self.store.get_project(task["project_id"])
            try:
                result = self.registry.dispatch(
                    handle["poll_capability"], handle["poll_arguments"],
                    CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:poll:{handle['next_check_at']}", execution_control=self._control(task)),
                )
            except (ExecutionOwnershipLost, ExecutionBudgetExceeded, ExecutionReplayUncertain):
                raise
            except Exception as exc:  # noqa: BLE001
                error_code = llm_error_code(exc, default=type(exc).__name__)
                recoverable = is_recoverable_llm_error(exc) or is_recoverable_llm_error_code(error_code)
                if not recoverable:
                    raise
                self.store.recovery_manager.retry_await_operation(
                    task, handle["id"], f"{type(exc).__name__}: {exc}",
                    error_code=error_code, error_category="provider_transient",
                )
                return
            if result.status == "failed":
                error = result.error or {"type": "PollFailed"}
                code = str(error.get("code") or error.get("type") or "PollFailed")
                transient = is_recoverable_llm_error_code(code) or "transient" in code.casefold()
                raise RuntimeExecutionError(str(error), "tool_transient" if transient else "capability_permanent", error_code=code)
            elif result.status != "completed":
                self.store.settle_await_handle(handle["id"], "pending", {"error": result.error or {"type": "PollNotCompleted"}}, next_check_seconds=self._poll_interval(column), expected_task=task)
                return
            else:
                payload = result.output if isinstance(result.output, dict) else {"value": result.output}
        else:
            raise ValueError(f"unsupported V1 wait kind: {handle['waiting_kind']!r}")
        state = str(payload.get("status") or "succeeded").lower()
        successful = set((handle.get("resume_condition") or {}).get("status_in") or ["succeeded", "done", "complete"])
        if state in {"pending", "running", "queued", "processing"} or state not in successful | {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "pending", payload, next_check_seconds=self._poll_interval(column), expected_task=task)
            return
        if state in {"failed", "error", "cancelled"}:
            error_value = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_code = str(
                error_value.get("code")
                or error_value.get("error_code")
                or error_value.get("type")
                or "AWAIT_FAILED"
            )
            error_category = str(
                payload.get("error_category")
                or error_value.get("category")
                or "external_permanent"
            )
            recoverable = bool(payload.get("recoverable")) or error_category in {
                "provider_transient",
                "tool_transient",
                "external_transient",
            } or is_recoverable_llm_error_code(error_code) or "transient" in error_code.casefold()
            self.store.resolve_await_failure(
                handle["id"],
                payload,
                expected_task=task,
                recoverable=recoverable,
                error_code=error_code,
                error_category=error_category if recoverable else "external_permanent",
            )
            return
        awaited_output = payload.get("output") if isinstance(payload.get("output"), dict) else payload
        self.store.checkpoint_await(task, handle["id"], "resolved_external", payload)
        try:
            resumed = handle["checkpoint"].get("resumed_completion")
            if resumed:
                output, outcome = resumed["output"], resumed["outcome"]
            else:
                output, outcome = self._resume_awaited_execution(task, workflow, column, handle, awaited_output)
                self.store.checkpoint_await(task, handle["id"], "resumed_completion", {"output": output, "outcome": outcome})
        except WaitRequested as requested:
            with self.store.tx(immediate=True):
                self.store.assert_execution_owner(task)
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
        with self.store.tx(immediate=True):
            self.store.assert_execution_owner(task)
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
            with self.store.tx(immediate=True) as db:
                self.store.assert_execution_owner(task, db=db)
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
                ledger_entry(
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
        plan = TaskPlan.model_validate(
            self.store.get_task_plan(
                task["project_id"],
                task["task_plan_id"],
            )["plan"]
        )
        proposed = next(
            item
            for item in plan.tasks
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
            CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:{kind}", execution_control=self._control(task)),
        )
        if not result.ok or result.status != "completed":
            raise RuntimeExecutionError(f"Await {kind} did not complete: {result.error}", "capability_permanent")
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
        self.generation = store.get_task(task_id)["state_version"]

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
            except Exception:
                self.store._revoked_owners.add((self.task_id, self.owner, self.generation))
                log.exception("lease renewal failed task=%s; execution revoked", self.task_id)
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
        self.workers = max(1, workers or store.policy.scheduling.runtime_workers)
        self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="devwerk-v1")
        self.worker_id = f"runtime-{id(self):x}"
        self.runtime = WorkflowRuntime(store, registry, self.worker_id, agent_core)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: set[str] = set()
        self._active_await: set[str] = set()
        self._lock = threading.Lock()
        self.last_successful_tick: str | None = None
        self.last_exception: str | None = None

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
                self._tick()
                self.last_successful_tick = datetime.now(timezone.utc).isoformat()
                self.last_exception = None
            except Exception as exc:
                self.last_exception = f"{type(exc).__name__}: {exc}"
                log.exception("supervisor tick failed; retrying next tick")
            self._wake.wait(self.interval)
            self._wake.clear()

    def _tick(self) -> None:
        for handle in self.store.due_await_handles():
            with self._lock:
                if len(self._active) + len(self._active_await) >= self.workers:
                    break
                if handle["id"] in self._active_await:
                    continue
                self._active_await.add(handle["id"])
            try:
                future = self.executor.submit(self.runtime.reconcile_await, handle)
            except Exception:
                with self._lock:
                    self._active_await.discard(handle["id"])
                raise
            future.add_done_callback(lambda item, value=handle["id"]: self._await_done(value, item))
        for task_id in self.store.runnable_task_ids():
            with self._lock:
                if len(self._active) + len(self._active_await) >= self.workers:
                    break
                if task_id in self._active:
                    continue
                self._active.add(task_id)
            try:
                future = self.executor.submit(self.runtime.step, task_id)
            except Exception:
                with self._lock:
                    self._active.discard(task_id)
                raise
            future.add_done_callback(lambda item, value=task_id: self._done(value, item))

    def health(self) -> dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive()),
                "last_successful_tick": self.last_successful_tick, "last_exception": self.last_exception}

    def _done(self, task_id: str, future: Any) -> None:
        with self._lock:
            self._active.discard(task_id)
        self.wake()
        try:
            future.result()
        except Exception:
            log.exception("runtime worker stopped task=%s", task_id)

    def _await_done(self, handle_id: str, future: Any) -> None:
        with self._lock:
            self._active_await.discard(handle_id)
        self.wake()
        try:
            future.result()
        except Exception:
            log.exception("Await worker stopped handle=%s", handle_id)


def _failure_message(output: dict[str, Any]) -> str:
    for step in reversed(output.get("steps") or []):
        error = step.get("error") if isinstance(step, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return f"{error.get('type')}: {error['message']}"
    for key in ("summary", "reason", "failure_reason", "error", "message"):
        detail = output.get(key)
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return "Column transitioned to failed"


def _column_completion_contract(
    workflow: WorkflowDefinition,
    column: ColumnDefinition,
) -> CompletionContract:
    rules: dict[str, CompletionOutcomeRule] = {}
    for transition in column.transitions:
        terminal_failure = workflow.terminal_kind(transition.target) == "failed"
        rules[transition.outcome] = CompletionOutcomeRule(
            target=transition.target,
            evidence_requirement=(
                transition.evidence_requirement
                or "optional"
            ),
            allows_unresolved_failures=(
                transition.allows_unresolved_failures or terminal_failure
            ),
        )
    return CompletionContract(
        tool_name="column.complete",
        outcomes=rules,
        output_schema=column.output_contract,
        evidence_collection="runtime",
        acceptance_checks=tuple(check.model_dump() for check in column.acceptance_checks),
    )
