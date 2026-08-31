from __future__ import annotations

import hashlib
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
    ContextSelection,
    EventWaitPolicy,
    PollWaitPolicy,
    TaskPlan,
    TimerWaitPolicy,
    ToolResult,
    WorkcellAgentParticipant,
    WorkcellCapabilityParticipant,
    WorkcellExecutor,
    WorkflowDefinition,
)
from app.v1.files import ProjectFiles
from app.v1.store import V1Store


log = logging.getLogger("devwerk.v1.runtime")
trace_log = logging.getLogger("devwerk.runtime.trace")

_CONTEXT_CONSUMPTION_CONTRACT = (
    "Runtime context is authoritative for this activation. Use embedded "
    "project.loop.assets and artifacts content directly; do not list or read a path "
    "already named in context_manifest unless it is absent, known to have changed, "
    "or independent verification is explicitly required. Loop asset paths are not "
    "Project filesystem paths. On a Session resume, use the logical checkpoint and "
    "directed Handoffs, then fetch only the specific missing or changed Project files."
)


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
                if run is not None:
                    self.store.mark_workcell_recovering(
                        task["project_id"],
                        run["id"],
                        error,
                    )
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
            "task": planned_task,
        }
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
            loop_binding = self.store.get_project_loop_binding(task["project_id"])
            if loop_binding:
                loop_assets = self.store.get_project_loop_assets(task["project_id"])
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
                data["project"]["loop"] = {
                    "key": loop_binding["loop_key"],
                    "version": loop_binding["loop_version"],
                    "digest": loop_binding["loop_digest"],
                    "bindings": loop_binding["bindings"],
                    "assets": loop_assets,
                    "asset_manifest": preloaded_loop_assets,
                }
        if selection.include_task:
            data["task"] = {
                "id": task["id"],
                "title": task["title"],
                "brief": task["brief"],
                "input": task["input"],
                "context": task["context"],
            }
        if selection.upstream_outputs:
            selected = set(selection.upstream_outputs)
            data["upstream_outputs"] = {
                item["column_key"]: item["output"]
                for item in self.store.runs(task["project_id"], task["id"])
                if item["column_key"] in selected and item["status"] == "succeeded"
            }
        if selection.artifact_globs:
            files = ProjectFiles(project["base_dir"], self.policy)
            artifacts: list[dict[str, str]] = []
            seen_paths: set[str] = set()
            remaining_chars = self.policy.context.artifact_context_max_characters
            remaining_files = self.policy.context.artifact_context_max_files
            for pattern in selection.artifact_globs:
                selected = files.existing_texts(
                    pattern,
                    remaining_chars,
                    limit=remaining_files,
                    exclude_paths=seen_paths,
                )
                for item in selected:
                    artifacts.append(item)
                    seen_paths.add(item["path"])
                    remaining_chars -= len(item["content"])
                    remaining_files -= 1
                if remaining_chars <= 0 or remaining_files <= 0:
                    break
            data["artifacts"] = artifacts
            preloaded_artifacts = [
                {
                    key: item[key]
                    for key in (
                        "path",
                        "size_bytes",
                        "utf8_characters",
                        "non_whitespace_characters",
                        "line_count",
                        "sha256",
                    )
                }
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
            "preloaded_content_is_authoritative": True,
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
        if isinstance(column.executor, WorkcellExecutor):
            return self._execute_workcell(task, workflow, run, column, input_data)
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

    def _execute_workcell(
        self,
        task: dict[str, Any],
        workflow: WorkflowDefinition,
        run: dict[str, Any],
        column: ColumnDefinition,
        input_data: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        executor = column.executor
        assert isinstance(executor, WorkcellExecutor)
        project = self.store.get_project(task["project_id"])
        workflow_row = self.store.get_workflow_revision(
            task["project_id"], task["workflow_revision_id"]
        )
        workcell = self.store.get_or_create_workcell(
            task["project_id"],
            task["id"],
            run["id"],
            executor,
            input_data,
        )
        participants = {
            item["participant_key"]: item
            for item in self.store.workcell_participants(task["project_id"], workcell["id"])
        }
        while workcell["status"] == "active":
            state = executor.state(str(workcell["current_state"]))
            participant = executor.participant(state.participant)
            handoffs = self.store.workcell_handoffs(
                task["project_id"],
                workcell["id"],
                receiver=participant.key,
            )
            participant_session_id = (
                participants[participant.key].get("agent_session_id")
                if isinstance(participant, WorkcellAgentParticipant)
                and participant.lifecycle != "invocation"
                else None
            )
            participant_input = input_data
            if isinstance(participant, WorkcellAgentParticipant):
                participant_input = self._input_for(
                    task,
                    workflow,
                    column,
                    context_selection=participant.context,
                )
                if participant_session_id and self.store.agent_session_messages(
                    task["project_id"], participant_session_id
                ):
                    participant_input = self._resume_input(participant_input)
            activation = {
                **participant_input,
                "workcell": {
                    "id": workcell["id"],
                    "current_state": state.key,
                    "participant": participant.key,
                },
                "handoffs": handoffs,
            }
            validate_contract(
                activation,
                state.input_contract,
                label=f"Workcell state {state.key} input",
            )
            self.store.activate_workcell_participant(
                task["project_id"],
                workcell["id"],
                participant.key,
                state.key,
            )
            if isinstance(participant, WorkcellAgentParticipant):
                memory = self.store.memory.build_context(
                    project,
                    selectors=participant.context.memory,
                    task_id=task["id"],
                    workcell_id=workcell["id"],
                    participant_key=participant.key,
                    include_core=participant.context.include_project,
                )
                result = self.agent_core.run(
                    AgentRunSpec(
                        kind="column",
                        project=project,
                        instruction=self._agent_instruction(
                            participant.instruction,
                            state.instruction,
                        ),
                        instruction_revision=int(workflow_row["revision"]),
                        context={
                            "workflow": {
                                "id": workflow_row["id"],
                                "name": workflow.name,
                                "description": workflow.description,
                            },
                            "column": {"key": column.key, "name": column.name},
                            "workcell": activation["workcell"],
                            "input": participant_input,
                            "handoffs": handoffs,
                            "memory": memory,
                        },
                        capability_ids=participant.capabilities,
                        task_id=task["id"],
                        column_run_id=run["id"],
                        column_attempt_id=run["attempt_id"],
                        completion_outcomes={item.signal for item in state.transitions},
                        completion_targets={
                            item.signal: item.target for item in state.transitions
                        },
                        output_contract=state.output_contract,
                        agent_session_id=participant_session_id,
                        completion_tool_name="workcell.signal",
                        completion_requires_evidence=state.require_evidence,
                    )
                )
                if result.status != "succeeded" or not result.completion:
                    raise RuntimeExecutionError(
                        result.error or "Workcell participant failed without a signal",
                        result.error_category or "runtime_permanent",
                        error_code=result.error_code,
                        checkpoint={
                            **result.checkpoint,
                            "workcell_id": workcell["id"],
                            "workcell_state": state.key,
                            "participant": participant.key,
                        },
                        agent_run_id=result.agent_run_id,
                    )
                signal = str(result.completion["outcome"])
                payload = {
                    "output": dict(result.completion["output"]),
                    "summary": str(result.completion.get("summary") or result.text),
                    "evidence_ids": list(result.completion.get("evidence_ids") or []),
                    "agent_run_id": result.agent_run_id,
                }
            elif isinstance(participant, WorkcellCapabilityParticipant):
                sequence = CapabilitySequenceExecutor(
                    steps=participant.steps,
                    completed_outcome=participant.completed_signal,
                    outcome_from=participant.signal_from,
                )
                output, signal = self._execute_sequence(
                    task,
                    run,
                    sequence,
                    activation,
                )
                payload = {"output": output, "summary": output.get("summary", "")}
            else:
                raise TypeError(f"unsupported Workcell participant: {type(participant).__name__}")
            transition = next(
                (item for item in state.transitions if item.signal == signal),
                None,
            )
            if transition is None:
                raise ValueError(
                    f"Workcell state {state.key!r} produced undeclared signal {signal!r}"
                )
            terminal_outcome = executor.terminal_outcome(transition.target)
            workcell = self.store.advance_workcell(
                task["project_id"],
                workcell["id"],
                sender_key=participant.key,
                signal=signal,
                payload=payload,
                receivers=transition.receivers,
                target=transition.target,
                terminal_outcome=terminal_outcome,
            )
            self.store.snapshot_workcell(task["project_id"], workcell["id"])
            if terminal_outcome is not None:
                for item in participants.values():
                    session_id = item.get("agent_session_id")
                    if session_id and item.get("lifecycle") == "column_visit":
                        self.store.suspend_agent_session(
                            task["project_id"], session_id, task["id"]
                        )
                final_output = payload.get("output")
                if not isinstance(final_output, dict):
                    final_output = {"value": final_output}
                return final_output, terminal_outcome
        terminal_outcome = executor.terminal_outcome(str(workcell["current_state"]))
        if terminal_outcome is None:
            raise RuntimeError("Workcell stopped outside a declared terminal")
        output = workcell.get("output") or {}
        final_output = output.get("output") if isinstance(output, dict) else output
        return (
            final_output if isinstance(final_output, dict) else {"value": final_output},
            terminal_outcome,
        )

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
        agent_input = input_data
        if session and self.store.agent_session_messages(task["project_id"], session["id"]):
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
            try:
                result = self.registry.dispatch(
                    handle["poll_capability"], handle["poll_arguments"],
                    CapabilityContext(project_id=task["project_id"], project=project, store=self.store, task_id=task["id"], column_run_id=handle["run_id"], execution_key=f"{handle['id']}:poll:{handle['next_check_at']}"),
                )
            except Exception as exc:  # noqa: BLE001
                error_code = llm_error_code(exc, default=type(exc).__name__)
                recoverable = is_recoverable_llm_error(exc) or is_recoverable_llm_error_code(error_code)
                self.store.resolve_await_failure(
                    handle["id"],
                    {
                        "status": "failed",
                        "error": {"type": type(exc).__name__, "message": str(exc), "code": error_code},
                    },
                    recoverable=recoverable,
                    error_code=error_code,
                    error_category="provider_transient" if recoverable else "external_permanent",
                )
                return
            if result.status == "failed":
                payload = {"status": "failed", "error": result.error or {"type": "PollFailed"}}
            elif result.status != "completed":
                self.store.settle_await_handle(handle["id"], "pending", {"error": result.error or {"type": "PollNotCompleted"}}, next_check_seconds=self._poll_interval(column))
                return
            else:
                payload = result.output if isinstance(result.output, dict) else {"value": result.output}
        else:
            raise ValueError(f"unsupported V1 wait kind: {handle['waiting_kind']!r}")
        state = str(payload.get("status") or "succeeded").lower()
        successful = set((handle.get("resume_condition") or {}).get("status_in") or ["succeeded", "done", "complete"])
        if state in {"pending", "running", "queued", "processing"} or state not in successful | {"failed", "error", "cancelled"}:
            self.store.settle_await_handle(handle["id"], "pending", payload, next_check_seconds=self._poll_interval(column))
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
                recoverable=recoverable,
                error_code=error_code,
                error_category=error_category if recoverable else "external_permanent",
            )
            return
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
