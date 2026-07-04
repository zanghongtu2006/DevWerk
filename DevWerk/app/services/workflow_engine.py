from __future__ import annotations

import json
import logging
import uuid
import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from app.models.protocol import FileOp, IdeChatResponse, PatchOp, ToolRequest
from app.services.agent_definition import default_agent_catalog
from app.services.capability_broker import CapabilityBroker
from app.services.capability_catalog import capability_catalog
from app.services.code_context import build_code_context_summary
from app.services.conversation_context import context_debug_payload, prepare_conversation_context
from app.services.kanban import (
    add_artifact,
    add_event,
    add_project_event,
    append_conversation_message,
    create_revision,
    finish_column_run,
    get_project_settings,
    get_project_workflow,
    get_task,
    move_task,
    start_column_run,
    update_conversation,
)
from app.services.session_store import read_project_memory
from app.services.skill_manager import resolve_agent_skills
from app.services.job_scheduler import JobScheduler
from app.services.llm_factory import get_llm_client
from app.services.local_capability_provider import (
    apply_file_changes,
    execute_tool_requests,
    local_backend_enabled,
)
from app.services.memory_system import build_context_pack, create_agent_run, handle_agent_writeback
from app.services.plugin_manager import get_plugin_agent
from app.services.provider_errors import is_retryable_llm_error
from app.services.tool_protocol import normalize_tool_request
from app.services.verification_policy import configured_post_apply_tool_requests, verification_feedback_summary
from app.services.workflow import apply_workflow_action, record_phase_output
from app.services.workflow_definition import WorkflowColumn, WorkflowDefinition, workflow_from_dict

_log = logging.getLogger("devwerk.workflow_engine")

@dataclass
class WorkflowRunState:
    context_bundle: dict[str, Any] = field(default_factory=dict)
    execute_response: IdeChatResponse | None = None
    phase_outputs: list[dict[str, Any]] = field(default_factory=list)
    rework_rounds: int = 0


@dataclass
class ColumnResult:
    action: str
    decision: str = "approve"
    response: IdeChatResponse | None = None
    target_status: str | None = None


class WorkflowEngine:
    def __init__(self) -> None:
        self._job_handlers = {
            "index_project_context": self._run_context_column,
        }
        self._scheduler: JobScheduler | None = None

    async def run(self, task_id: str, body: dict[str, Any]) -> None:
        project_id = str(body.get("project_id") or "default")
        settings_payload = get_project_settings(project_id)
        project_settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
        agent_overrides = project_settings.get("agents") if isinstance(project_settings, dict) else {}
        self._scheduler = JobScheduler(default_agent_catalog().with_project_overrides(agent_overrides))
        definition = workflow_from_dict(get_project_workflow(project_id).get("workflow") or {})
        workflow_summary = definition.summary()
        executable = _executable_columns(definition)
        _event(
            task_id,
            "workflow_started",
            {
                "entrypoint": "/v1/workflows",
                "workflow": workflow_summary,
                "executable_columns": [
                    {"status_key": col.status_key, "job_template": col.job_template, "success_action": col.success_action}
                    for col in executable
                ],
            },
        )

        if not executable:
            response = _failure_response(
                task_id,
                "WORKFLOW_EMPTY",
                "Workflow has no executable columns.",
                status_key=_failure_status(definition) or _current_status(task_id),
            )
            _safe_fail(task_id, {"phase": "workflow", "reason": response.error_message})
            _notify_project_conversation_failure(
                task_id,
                project_id,
                phase="workflow",
                reason=response.error_message,
                status_key=response.status_key,
            )
            _event(task_id, "workflow_finished", {"ok": False, "phase": "workflow", "status_key": response.status_key})
            add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
            return

        state = WorkflowRunState()
        resume_action = str(body.get("resume_action") or "").strip().lower()
        resume_status = str(body.get("resume_status") or "").strip().lower()
        resume_column = definition.column(resume_status) if resume_status else None
        current = resume_column if resume_column is not None and resume_column.executable else executable[0]
        if resume_action:
            update_conversation(task_id, state="running", waiting_for=None, active_column=current.status_key)
            _event(
                task_id,
                "workflow_resumed",
                {
                    "reason": resume_action,
                    "resume_status": resume_status or None,
                    "status_key": current.status_key,
                    "has_client_feedback": isinstance(body.get("client_feedback"), dict),
                    "has_verification_feedback": isinstance(body.get("verification_feedback"), dict),
                },
            )

        parameters = project_settings.get("parameters") if isinstance(project_settings, dict) else {}
        max_rounds = _positive_int(parameters.get("workflow_max_total_runs"), 512)
        max_rework_rounds = _positive_int(parameters.get("workflow_max_rework_runs"), 128)
        for round_no in range(1, max_rounds + 1):
            _log.debug(
                "workflow loop task_id=%s round=%s status_key=%s job_template=%s",
                task_id,
                round_no,
                current.status_key,
                current.job_template,
            )
            _event(
                task_id,
                "workflow_round_started",
                {"round": round_no, "status_key": current.status_key, "job_template": current.job_template},
            )

            try:
                result = await self._run_column(task_id, body, definition, workflow_summary, current, state)
            except UnsupportedJobError as exc:
                response = _failure_response(
                    task_id,
                    "UNSUPPORTED_WORKFLOW_JOB",
                    str(exc),
                    status_key=_failure_status(definition) or current.status_key,
                )
                _safe_fail(task_id, {"phase": current.status_key, "reason": response.error_message})
                _notify_project_conversation_failure(
                    task_id,
                    project_id,
                    phase=current.status_key,
                    reason=response.error_message,
                    status_key=response.status_key,
                )
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": response.status_key})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            target_status = result.target_status or _action_target(definition, result.action)
            if result.response is not None:
                if not result.response.waiting_for:
                    update_conversation(
                        task_id,
                        state="failed" if not result.response.ok else "active",
                        waiting_for=None,
                        active_column=result.response.status_key,
                    )
                boundary_payload = {
                    "ok": result.response.ok,
                    "phase": current.status_key,
                    "status_key": result.response.status_key,
                    "action": result.action,
                    "round": round_no,
                }
                if result.response.waiting_for:
                    boundary_payload.update(
                        {
                            "waiting_for": result.response.waiting_for,
                            "reason": str((result.response.interaction or {}).get("reason") or result.response.waiting_for),
                            "terminal": False,
                        }
                    )
                    _event(task_id, "workflow_run_paused", boundary_payload)
                else:
                    boundary_payload["terminal"] = bool(result.response.done or result.response.status_key in _terminal_statuses(definition))
                    _event(task_id, "workflow_finished", boundary_payload)
                if not result.response.ok and not result.response.waiting_for:
                    _notify_project_conversation_failure(
                        task_id,
                        project_id,
                        phase=current.status_key,
                        reason=result.response.error_message or result.response.reply or "Workflow failed before producing a result.",
                        status_key=result.response.status_key,
                    )
                add_artifact(task_id, artifact_type="workflow_result", payload=result.response.model_dump())
                return

            if result.action in {"request_replan", "request_rework"}:
                state.rework_rounds += 1
                if state.rework_rounds > max_rework_rounds:
                    response = _waiting_response(
                        task_id,
                        status_key=current.status_key,
                        waiting_for="user_guidance",
                        reply="Workflow needs guidance after repeated rework.",
                        interaction={
                            "type": "rework_guidance",
                            "reason": "rework_budget",
                            "review": {},
                            "round": round_no,
                            "rework_rounds": state.rework_rounds,
                            "max_rework_rounds": max_rework_rounds,
                            "actions": ["message", "cancel"],
                        },
                    )
                    update_conversation(task_id, state="waiting_user", waiting_for="user_guidance", active_column=current.status_key)
                    pause_payload = {
                        "phase": current.status_key,
                        "status_key": current.status_key,
                        "waiting_for": "user_guidance",
                        "reason": "rework_budget",
                        "round": round_no,
                        "rework_rounds": state.rework_rounds,
                        "max_rework_rounds": max_rework_rounds,
                        "terminal": False,
                    }
                    _event(task_id, "workflow_waiting_user", pause_payload)
                    _event(task_id, "workflow_run_paused", pause_payload)
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    return
                _event(
                    task_id,
                    "workflow_rework_loop",
                    {
                        "round": round_no,
                        "decision": result.action,
                        "target_status": target_status,
                        "review": {},
                    },
                )

            if not target_status:
                failure_status = _failure_status(definition) or current.status_key
                response = _failure_response(
                    task_id,
                    "WORKFLOW_BAD_ACTION",
                    f"Workflow action {result.action!r} has no target.",
                    status_key=failure_status,
                )
                apply_workflow_action(task_id, "fail", {"phase": current.status_key, "reason": response.error_message})
                _notify_project_conversation_failure(
                    task_id,
                    project_id,
                    phase=current.status_key,
                    reason=response.error_message,
                    status_key=response.status_key,
                )
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": response.status_key})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            next_column = _next_executable_after_transition(definition, target_status, current)
            if result.decision in {"request_replan", "request_rework"} or result.action == "retry":
                next_column = _next_executable_after_transition(
                    definition,
                    target_status,
                    current,
                    include_current=True,
                )
            if next_column is None:
                if result.action == "apply_result" and _is_success_terminal(definition, target_status):
                    response = _generic_done_response(task_id, project_id, target_status, state)
                    append_conversation_message(
                        task_id,
                        role="assistant",
                        content=response.reply or "Workflow completed after backend-local apply.",
                        message_type="workflow_result",
                        metadata={"status_key": target_status, "apply_provider": "devwerk-backend"},
                    )
                    update_conversation(task_id, state="done", waiting_for=None, active_column=target_status)
                    _event(
                        task_id,
                        "workflow_finished",
                        {
                            "ok": True,
                            "phase": current.status_key,
                            "status_key": response.status_key,
                            "round": round_no,
                            "apply_provider": "devwerk-backend",
                        },
                    )
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    return

                if result.action == "apply_result" and target_status in _failure_statuses(definition):
                    response = _failure_response(
                        task_id,
                        "BACKEND_LOCAL_APPLY_FAILED",
                        "Backend-local apply or verification failed.",
                        status_key=target_status or _failure_status(definition) or "failed",
                    )
                    _notify_project_conversation_failure(
                        task_id,
                        project_id,
                        phase=current.status_key,
                        reason=response.error_message or "",
                        status_key=response.status_key,
                    )
                    _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": response.status_key})
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    return

                if _is_success_terminal(definition, target_status) and state.execute_response is None and result.decision == "approve":
                    response = _generic_done_response(task_id, project_id, target_status, state)
                    append_conversation_message(
                        task_id,
                        role="assistant",
                        content=response.reply or "Workflow completed.",
                        message_type="workflow_result",
                        metadata={"status_key": target_status},
                    )
                    update_conversation(task_id, state="done", waiting_for=None, active_column=target_status)
                    _event(
                        task_id,
                        "workflow_finished",
                        {
                            "ok": response.ok,
                            "phase": current.status_key,
                            "status_key": response.status_key,
                            "round": round_no,
                            "generic_phase_outputs": len(state.phase_outputs),
                        },
                    )
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    return

                if state.execute_response is not None and result.decision == "approve":
                    response = _ready_response(task_id, project_id, target_status, state.execute_response)
                    append_conversation_message(
                        task_id,
                        role="assistant",
                        content=response.reply or "Code changes are ready for snapshot-protected apply and verification.",
                        message_type="code_result",
                    )
                    update_conversation(task_id, state="waiting_client", waiting_for="apply_result", active_column=response.status_key)
                    _event(
                        task_id,
                        "workflow_finished",
                        {
                            "ok": response.ok,
                            "phase": current.status_key,
                            "status_key": response.status_key,
                            "ops": len(response.ops),
                            "patch_ops": len(response.patch_ops),
                            "tool_requests": len(response.tool_requests),
                            "verification_required": bool(response.tool_requests),
                            "round": round_no,
                        },
                    )
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    return

                response = _failure_response(
                    task_id,
                    "WORKFLOW_NO_NEXT_COLUMN",
                    f"Workflow stopped at {target_status!r} without a code result.",
                    status_key=target_status or _failure_status(definition) or "failed",
                )
                if not _failure_status(definition) or target_status not in _failure_statuses(definition):
                    apply_workflow_action(task_id, "fail", {"phase": current.status_key, "reason": response.error_message})
                _notify_project_conversation_failure(
                    task_id,
                    project_id,
                    phase=current.status_key,
                    reason=response.error_message,
                    status_key=response.status_key,
                )
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": response.status_key})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            current = next_column

        response = _failure_response(
            task_id,
            "WORKFLOW_LOOP_EXHAUSTED",
            "Workflow exhausted without producing a result.",
            status_key=_failure_status(definition) or "failed",
        )
        _safe_fail(task_id, {"phase": "workflow", "reason": response.error_message})
        _notify_project_conversation_failure(
            task_id,
            project_id,
            phase="workflow",
            reason=response.error_message,
            status_key=response.status_key,
        )
        _event(task_id, "workflow_finished", {"ok": False, "phase": "workflow", "status_key": response.status_key})
        add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())

    async def _run_column(
        self,
        task_id: str,
        body: dict[str, Any],
        definition: WorkflowDefinition,
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        if self._scheduler is None or not column.job_template:
            raise UnsupportedJobError(f"Column {column.status_key!r} has no schedulable job template.")
        try:
            job = self._scheduler.schedule(
                task_id=task_id,
                column=column.status_key,
                job_template=column.job_template,
            )
        except (KeyError, LookupError) as exc:
            raise UnsupportedJobError(str(exc)) from exc
        handler = self._job_handlers.get(job.template.id)
        job_body = dict(
            body,
            _workflow_job_id=job.id,
            _workflow_job_template=job.template.id,
            _workflow_agent_id=job.agent.id,
            _workflow_agent_model_route=job.agent.model_route,
            _workflow_agent_capabilities=list(job.agent.capabilities),
            _workflow_agent_skills=list(job.agent.skills),
        )
        run = start_column_run(
            task_id,
            status_key=column.status_key,
            agent=job.agent.id,
            checkpoint={
                "workflow": workflow_summary.get("name"),
                "job_id": job.id,
                "job_template": job.template.id,
                "output_contract": job.template.output_contract,
                "runtime": job.agent.runtime,
                "model_route": job.agent.model_route,
                "capabilities": list(job.agent.capabilities),
                "skills": list(job.agent.skills),
                "input_artifacts": column.input_artifacts or [],
            },
        )
        _event(
            task_id,
            "job_scheduled",
            {
                "job_id": job.id,
                "job_template": job.template.id,
                "output_contract": job.template.output_contract,
                "agent": job.agent.id,
                "runtime": job.agent.runtime,
                "model_route": job.agent.model_route,
                "capabilities": list(job.agent.capabilities),
                "skills": list(job.agent.skills),
            },
        )
        _event(task_id, "column_run_started", {"column_run_id": run["id"], "run_no": run["run_no"], "status_key": column.status_key, "job_id": job.id, "agent": job.agent.id})
        try:
            if handler is None:
                outcome = await self._run_generic_column(task_id, job_body, definition, workflow_summary, column, state)
            else:
                result = handler(task_id, job_body, workflow_summary, column, state)
                outcome = await result if isinstance(result, Awaitable) else result
            run_state = "waiting_user" if outcome.response and outcome.response.waiting_for else "completed"
            finish_column_run(run["id"], state=run_state, checkpoint={"action": outcome.action, "decision": outcome.decision, "target_status": outcome.target_status})
            return outcome
        except Exception as exc:
            finish_column_run(run["id"], state="failed", checkpoint={"error": f"{type(exc).__name__}: {exc}"})
            raise

    async def _run_generic_column(
        self,
        task_id: str,
        body: dict[str, Any],
        definition: WorkflowDefinition,
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        agent = _active_agent(body)
        model_route = str(body.get("_workflow_agent_model_route") or "default").strip() or "default"
        job_template = str(body.get("_workflow_job_template") or column.job_template or column.status_key)
        output_artifact = column.output_artifact or f"{column.status_key}_bundle"
        if _current_status(task_id) != column.status_key:
            enter_action = _entry_action_for_status(definition, column.status_key)
            if enter_action:
                apply_workflow_action(task_id, enter_action, {"phase": column.status_key, "reason": "enter generic workflow column"})
            else:
                move_task(
                    task_id,
                    column.status_key,
                    force=True,
                    payload={
                        "phase": column.status_key,
                        "reason": "enter generic workflow column",
                        "action": "workflow_column_entered",
                    },
                )
            _event(task_id, "workflow_column_entered", {"status_key": column.status_key, "agent": agent})
        _event(
            task_id,
            "workflow_column_started",
            {
                "status_key": column.status_key,
                "agent": agent,
                "job_template": job_template,
                "runtime": "generic_llm_column",
            },
        )
        project_id = str((get_task(task_id).get("task") or {}).get("project_id") or body.get("project_id") or "default")
        token_budget = _column_token_budget(column, body)
        memory_run = create_agent_run(
            project_id=project_id,
            task_id=task_id,
            workflow_id=str(workflow_summary.get("name") or ""),
            agent_role=agent,
            stage=column.status_key,
            token_budget=token_budget,
        )
        context_pack_result = build_context_pack(
            project_id=project_id,
            task_id=task_id,
            workflow_id=str(workflow_summary.get("name") or ""),
            agent_role=agent,
            stage=column.status_key,
            token_budget=token_budget,
            run_id=str(memory_run.get("run_id") or ""),
            workspace=body.get("workspace") if isinstance(body.get("workspace"), dict) else None,
        )
        add_artifact(task_id, artifact_type="context_pack", payload=context_pack_result["context_pack"])
        context = _build_agent_context(
            task_id,
            column.status_key,
            agent,
            body,
            workflow_summary,
            column.input_artifacts or [],
        )
        context["context_pack"] = context_pack_result["context_pack"]
        context["memory_run_id"] = memory_run.get("run_id")
        _event(task_id, "agent_context_built", {"phase": column.status_key, "agent": agent, "context": _context_log_summary(context)})
        prompt = _generic_column_prompt(
            task_id=task_id,
            column=column,
            agent=agent,
            job_template=job_template,
            output_artifact=output_artifact,
            workflow_summary=workflow_summary,
            actions=definition.actions,
            context=context,
        )
        _event(
            task_id,
            "agent_prompt_context_prepared",
            {
                "phase": column.status_key,
                "agent": agent,
                "runtime": "generic_llm_column",
                "model_route": model_route,
                "input_artifacts": column.input_artifacts or [],
                "output_artifact": output_artifact,
            },
        )
        raw = await _chat_json_with_retry(model_route, prompt)
        if not isinstance(raw, dict):
            raise ValueError("generic column agent returned a non-object JSON response")
        raw, normalization_notes = _normalize_generic_agent_output(raw, definition, column)
        if normalization_notes:
            _event(
                task_id,
                "agent_output_normalized",
                {
                    "phase": column.status_key,
                    "agent": agent,
                    "notes": normalization_notes,
                    "keys": sorted(str(key) for key in raw.keys()),
                },
            )

        warnings = [str(item) for item in (raw.get("warnings") or []) if str(item).strip()]
        summary = str(raw.get("summary") or raw.get("reply") or f"{column.title} completed.").strip()
        outputs = raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
        writeback = raw.get("writeback") if isinstance(raw.get("writeback"), dict) else {}
        if not outputs:
            outputs = {
                key: value
                for key, value in raw.items()
                if key
                not in {"phase", "agent", "summary", "reply", "warnings", "decision", "next_action", "tool_requests", "writeback"}
            }
        phase_bundle = {
            "phase": raw.get("phase") or column.status_key,
            "agent": raw.get("agent") or agent,
            "job_template": job_template,
            "summary": summary,
            "outputs": outputs,
            "warnings": warnings,
            "raw": raw,
        }
        if writeback:
            phase_bundle["writeback"] = writeback

        tool_requests = _allowed_client_tool_requests(
            raw.get("tool_requests") or [],
            body.get("client_capabilities"),
            project_root=body.get("project_root"),
        )
        decision = _generic_decision(raw.get("decision"), raw.get("next_action"), has_tool_requests=bool(tool_requests))
        if decision == "need_client_tool" and tool_requests:
            if local_backend_enabled(body):
                root = _effective_project_root(body, raw=raw)
                if root:
                    tool_round = _positive_int(body.get("_backend_tool_round"), 0)
                    max_tool_rounds = _positive_int(body.get("backend_tool_max_rounds"), 64)
                    if tool_round >= max_tool_rounds:
                        raise RuntimeError(f"backend local tool loop exceeded {max_tool_rounds} rounds")
                    results = execute_tool_requests(tool_requests, project_root=root)
                    result_payload = {
                        "phase": column.status_key,
                        "agent": agent,
                        "provider": "devwerk-backend",
                        "project_root": root,
                        "requests": [request.model_dump() for request in tool_requests],
                        "results": [result.model_dump(exclude_none=True) for result in results],
                        "round": tool_round + 1,
                    }
                    add_artifact(task_id, artifact_type="backend_tool_result", payload=result_payload)
                    add_artifact(
                        task_id,
                        artifact_type="client_tool_result",
                        payload={"waiting_for": "backend_local_tool", "results": result_payload["results"]},
                    )
                    _event(
                        task_id,
                        "backend_local_tool_result_recorded",
                        {
                            "phase": column.status_key,
                            "agent": agent,
                            "project_root": root,
                            "result_count": len(results),
                            "all_ok": all(result.ok for result in results),
                            "round": tool_round + 1,
                        },
                    )
                    next_body = dict(body)
                    next_body["_backend_tool_round"] = tool_round + 1
                    existing_results = next_body.get("tool_results") if isinstance(next_body.get("tool_results"), list) else []
                    next_body["tool_results"] = [*existing_results, *result_payload["results"]]
                    return await self._run_generic_column(task_id, next_body, definition, workflow_summary, column, state)

            request_payload = {
                "phase": column.status_key,
                "agent": agent,
                "requests": [request.model_dump() for request in tool_requests],
            }
            add_artifact(task_id, artifact_type="client_tool_request", payload=request_payload)
            output = record_phase_output(
                task_id,
                phase=column.status_key,
                agent=agent,
                status_key=_current_status(task_id),
                summary=summary or "Column agent requested client tool evidence.",
                inputs=context,
                outputs={**phase_bundle, "client_tool_request": request_payload},
                warnings=warnings,
                decision="need_client_tool",
                next_action="tool_result",
            )
            state.phase_outputs.append(output)
            response = _waiting_response(
                task_id,
                status_key=_current_status(task_id),
                waiting_for="client_tool",
                reply=summary or "Collecting project evidence from a connected capability provider.",
                interaction={
                    "type": "client_tool",
                    "reason": "generic_column_evidence",
                    "phase": column.status_key,
                    "session_id": output["session_id"],
                    "actions": ["tool_result", "cancel"],
                },
            )
            response.tool_requests = tool_requests
            update_conversation(task_id, state="waiting_client", waiting_for="client_tool", active_column=column.status_key)
            _event(task_id, "workflow_client_tool_requested", request_payload)
            _event(task_id, "workflow_column_waiting", {**request_payload, "waiting_for": "client_tool"})
            return ColumnResult(action="need_client_tool", target_status=response.status_key, decision="wait", response=response)

        code_response = _generic_code_response(
            task_id=task_id,
            status_key=column.status_key,
            raw=raw,
            summary=summary,
            tool_requests=tool_requests,
        )
        if code_response is not None:
            state.execute_response = code_response
            changed_paths = [op.path for op in code_response.ops] + _patch_paths(code_response)
            revision = create_revision(
                task_id,
                summary=code_response.reply,
                ops=[op.model_dump() for op in code_response.ops],
                patch_ops=[op.model_dump() for op in code_response.patch_ops],
                changed_paths=changed_paths,
            )
            phase_bundle["code_change"] = {
                "revision_id": revision["id"],
                "ops": len(code_response.ops),
                "patch_ops": len(code_response.patch_ops),
                "tool_requests": len(code_response.tool_requests),
                "changed_paths": changed_paths,
            }
            add_artifact(
                task_id,
                artifact_type="code_ready_bundle",
                payload={
                    "revision_id": revision["id"],
                    "phase": column.status_key,
                    "agent": agent,
                    "changed_paths": changed_paths,
                    "ops_count": len(code_response.ops),
                    "patch_ops_count": len(code_response.patch_ops),
                    "tool_requests_count": len(code_response.tool_requests),
                    "requires_apply": True,
                    "created_at": revision.get("created_at"),
                },
            )
            if _should_force_code_ready(definition, column):
                action = "code_ready"
                output = record_phase_output(
                    task_id,
                    phase=column.status_key,
                    agent=agent,
                    status_key=_action_target(definition, action) or "ready_to_apply",
                    summary=summary,
                    inputs=context,
                    outputs=phase_bundle,
                    warnings=warnings,
                    decision=decision,
                    next_action=action,
                )
                state.phase_outputs.append(output)
                if writeback and memory_run.get("run_id"):
                    writeback_result = handle_agent_writeback(str(memory_run["run_id"]), writeback)
                    add_artifact(task_id, artifact_type="memory_writeback", payload=writeback_result)
                moved = apply_workflow_action(
                    task_id,
                    action,
                    {
                        "phase": column.status_key,
                        "session_id": output["session_id"],
                        "reason": summary,
                        "output_artifact": output_artifact,
                        "revision_id": revision["id"],
                        "changed_paths": changed_paths,
                        "ops_count": len(code_response.ops),
                        "patch_ops_count": len(code_response.patch_ops),
                        "_engine_internal": True,
                    },
                )
                _event(
                    task_id,
                    "workflow_column_completed",
                    {"status_key": column.status_key, "agent": agent, "decision": decision, "action": action},
                )
                response = _ready_response(task_id, project_id, (moved.get("task") or {}).get("status_key") or "ready_to_apply", code_response)
                response.planning = {
                    **(response.planning or {}),
                    "revision_id": revision["id"],
                    "changed_paths": changed_paths,
                    "ops_count": len(code_response.ops),
                    "patch_ops_count": len(code_response.patch_ops),
                    "requires_apply": True,
                    "requires_verification": _requires_post_apply_verification(project_id),
                }
                if local_backend_enabled(body):
                    root = _effective_project_root(body, raw=raw, response=code_response)
                    if root:
                        apply_payload = apply_file_changes(
                            ops=code_response.ops,
                            patch_ops=code_response.patch_ops,
                            project_root=root,
                        )
                        add_artifact(task_id, artifact_type="backend_local_apply_result", payload=apply_payload)
                        applied = apply_workflow_action(
                            task_id,
                            "apply_result",
                            {
                                **apply_payload,
                                "phase": "apply",
                                "revision_id": revision["id"],
                                "_engine_internal": True,
                            },
                        )
                        _event(
                            task_id,
                            "backend_local_apply_completed",
                            {
                                "phase": column.status_key,
                                "project_root": root,
                                "ok": bool(apply_payload.get("ok")),
                                "changed_paths": apply_payload.get("changed_paths") or [],
                                "error_message": apply_payload.get("error_message"),
                                "status_key": (applied.get("task") or {}).get("status_key"),
                            },
                        )
                        return ColumnResult(
                            action="apply_result",
                            target_status=(applied.get("task") or {}).get("status_key"),
                            decision=decision,
                        )
                append_conversation_message(
                    task_id,
                    role="assistant",
                    content=response.reply or "Code changes are ready for snapshot-protected apply and verification.",
                    message_type="code_result",
                )
                update_conversation(task_id, state="waiting_client", waiting_for="apply_result", active_column=response.status_key)
                _event(
                    task_id,
                    "workflow_waiting_apply_result",
                    {
                        "phase": column.status_key,
                        "status_key": response.status_key,
                        "revision_id": revision["id"],
                        "changed_paths": changed_paths,
                        "ops": len(response.ops),
                        "patch_ops": len(response.patch_ops),
                    },
                )
                return ColumnResult(action=action, target_status=response.status_key, decision=decision, response=response)

        add_artifact(task_id, artifact_type=output_artifact, payload=phase_bundle)
        action = _generic_column_action(definition, column, raw, decision)
        if action == "fail":
            summary = summary or f"{column.title} failed."
        output = record_phase_output(
            task_id,
            phase=column.status_key,
            agent=agent,
            status_key=_action_target(definition, action) or column.status_key,
            summary=summary,
            inputs=context,
            outputs=phase_bundle,
            warnings=warnings,
            decision=decision,
            next_action=action,
        )
        state.phase_outputs.append(output)
        if writeback and memory_run.get("run_id"):
            writeback_result = handle_agent_writeback(str(memory_run["run_id"]), writeback)
            add_artifact(task_id, artifact_type="memory_writeback", payload=writeback_result)
        _event(
            task_id,
            "agent_output_recorded",
            {"phase": column.status_key, "agent": agent, "artifact": output_artifact, "session_id": output["session_id"]},
        )
        append_conversation_message(
            task_id,
            role="assistant",
            content=summary,
            message_type="phase_summary",
            metadata={"phase": column.status_key, "agent": agent, "action": action},
        )
        moved = apply_workflow_action(
            task_id,
            action,
            {
                "phase": column.status_key,
                "session_id": output["session_id"],
                "reason": summary,
                "output_artifact": output_artifact,
                "_engine_internal": True,
            },
        )
        _event(
            task_id,
            "workflow_column_completed",
            {"status_key": column.status_key, "agent": agent, "decision": decision, "action": action},
        )
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision=decision)

    def _run_context_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        agent = _active_agent(body)
        _event(task_id, "workflow_column_started", {"status_key": column.status_key, "agent": agent})
        context = _build_agent_context(task_id, column.status_key, agent, body, workflow_summary, column.input_artifacts or [])
        code_context_summary = build_code_context_summary(body.get("workspace"))
        context_bundle = {
            "workspace": body.get("workspace"),
            "workspace_summary": _workspace_summary(body.get("workspace")),
            "code_context_summary": code_context_summary,
            "project_root": body.get("project_root"),
            "project_memory": context["project_memory"],
        }
        state.context_bundle = context_bundle
        add_artifact(task_id, artifact_type=column.output_artifact or "context_bundle", payload=context_bundle)
        add_artifact(task_id, artifact_type="code_context_summary", payload=code_context_summary)
        output = record_phase_output(
            task_id,
            phase=column.status_key,
            agent=agent,
            status_key=column.status_key,
            summary="Indexed client-provided workspace context and project memory for downstream agents.",
            inputs=context,
            outputs=context_bundle,
            warnings=[],
            decision="approve",
            next_action=column.success_action,
        )
        _event(task_id, "agent_context_built", {"phase": column.status_key, "agent": agent, "session_id": output["session_id"]})
        _event(
            task_id,
            "code_context_summary_built",
            {
                "available": bool(code_context_summary.get("available")),
                "source_map": code_context_summary.get("source_map"),
                "languages": code_context_summary.get("languages"),
                "representative_files": len(code_context_summary.get("representative_files") or []),
                "warnings": code_context_summary.get("warnings") or [],
            },
        )
        _event(task_id, "agent_output_recorded", {"phase": column.status_key, "agent": agent, "artifact": column.output_artifact or "context_bundle"})
        action = column.success_action or column.status_key
        moved = apply_workflow_action(
            task_id,
            action,
            {"phase": column.status_key, "session_id": output["session_id"], "_engine_internal": True},
        )
        _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "approve"})
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision="approve")

class UnsupportedJobError(ValueError):
    pass


def _generic_column_prompt(
    *,
    task_id: str,
    column: WorkflowColumn,
    agent: str,
    job_template: str,
    output_artifact: str,
    workflow_summary: dict[str, Any],
    actions: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, str]]:
    allowed_actions = {
        action: {"to": str(rule.get("to") or "")}
        for action, rule in actions.items()
        if isinstance(rule, dict)
    }
    prompt_context = {
        "task_id": task_id,
        "phase": column.status_key,
        "agent": agent,
        "column": {
            "status_key": column.status_key,
            "title": column.title,
            "job_template": job_template,
            "input_artifacts": column.input_artifacts or [],
            "output_artifact": output_artifact,
            "success_action": column.success_action,
            "failure_actions": column.failure_actions or [],
            "transition_to": column.transition_to,
            "context_policy": column.context_policy or {},
        },
        "workflow": workflow_summary,
        "actions": allowed_actions,
        "agent_context": context,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a DevWerk workflow column agent. Return one JSON object only. "
                "Execute the current Kanban column using the provided task context, prior artifacts, "
                "project memory, and workflow definition. Do not move Kanban columns yourself; choose "
                "a semantic next_action from the supplied actions. If the phase succeeds, prefer the "
                "column success_action. If it cannot succeed, use a configured failure action. "
                "When the current column is code-producing or has a code_change/output_contract, the "
                "result MUST include concrete file changes as either top-level ops or "
                "outputs.code_patch.files. Each file entry must include path and content, and code_patch "
                "may include target_root/project_root. Do not put generated files only in a prose summary "
                "or a generic outputs artifact; the workflow engine can only apply concrete file ops. "
                "Use workspace.write only for explicit tool-driven file writes, not as a substitute for "
                "returning the final code patch bundle in code-producing phases. "
                "If external evidence is required and available tools are described in context, return "
                "decision='need_client_tool' with tool_requests. Persist durable learning through an "
                "optional writeback object instead of repeating raw conversation. writeback may contain "
                "{task_updates:{progress, analysis_summary, code_context, decisions, handoff_summary, "
                "patch_summary, test_state, final_summary}, workflow_updates, run_updates, "
                "project_memory_candidates:[{target_memory_type, content, reason, confidence}]}. "
                "Project memory candidates are proposals only and are not auto-promoted. JSON shape: "
                "{phase, agent, summary, outputs, warnings, decision, next_action, tool_requests, writeback}. "
                "decision must be approve, fail, request_rework, request_replan, or need_client_tool."
            ),
        },
        {"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)},
    ]


def _column_token_budget(column: WorkflowColumn, body: dict[str, Any]) -> int:
    policy = column.context_policy if isinstance(column.context_policy, dict) else {}
    for value in (
        policy.get("token_budget"),
        policy.get("max_context_tokens"),
        (body.get("parameters") or {}).get("token_budget") if isinstance(body.get("parameters"), dict) else None,
        body.get("token_budget"),
    ):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 4096


async def _chat_json_with_retry(model_route: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            return get_llm_client(model_route).chat_json(messages)
        except Exception as exc:  # noqa: BLE001
            if attempt >= attempts or not is_retryable_llm_error(exc):
                raise
            delay = min(8.0, 1.5 * attempt)
            _log.warning(
                "generic column llm retry model_route=%s attempt=%s/%s error=%s",
                model_route,
                attempt,
                attempts,
                f"{type(exc).__name__}: {exc}",
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable generic LLM retry state")


def _generic_decision(decision: object, next_action: object, *, has_tool_requests: bool) -> str:
    value = str(decision or "").strip().lower().replace("-", "_")
    action = str(next_action or "").strip().lower().replace("-", "_")
    if has_tool_requests and value in {"need_client_tool", "tool", "tools", "need_tool"}:
        return "need_client_tool"
    if value in {"approve", "approved", "success", "succeeded", "done", "complete", "completed"}:
        return "approve"
    if value in {"fail", "failed", "failure", "error"}:
        return "fail"
    if value in {"request_replan", "replan"} or action == "request_replan":
        return "request_replan"
    if value in {"request_rework", "request_rewrite", "rewrite", "rework"}:
        return "request_rework"
    if action in {"fail", "request_replan", "request_rework"}:
        return action
    return "approve"


def _normalize_generic_agent_output(
    raw: dict[str, Any],
    definition: WorkflowDefinition,
    column: WorkflowColumn,
) -> tuple[dict[str, Any], list[str]]:
    """Align common LLM output shapes to the workflow action protocol.

    Column agents must emit semantic actions, but models often return target
    statuses or provider raw_text fallbacks. This function does not invent new
    workflow states; it only maps model output to actions already defined by
    the active workflow.
    """

    normalized = dict(raw)
    notes: list[str] = []
    for key in ("raw_text", "reply", "summary", "content"):
        parsed = _extract_json_object(str(normalized.get(key) or ""))
        if parsed is not None:
            normalized = {**normalized, **parsed}
            notes.append(f"embedded JSON parsed from {key}")
            break

    requested = _action_key(
        normalized.get("next_action")
        or normalized.get("action")
        or normalized.get("semantic_action")
        or normalized.get("command")
    )
    target = _status_key(
        normalized.get("target")
        or normalized.get("target_status")
        or normalized.get("target_column")
        or normalized.get("to")
    )
    if not target and requested and definition.column(requested) is not None:
        target = requested
        requested = ""
        notes.append(f"next_action looked like target status {target!r}")

    if target:
        mapped = _action_for_target(definition, column, target, preferred=requested)
        if mapped:
            if requested != mapped:
                notes.append(f"target status {target!r} mapped to action {mapped!r}")
            normalized["next_action"] = mapped
        elif requested:
            normalized["next_action"] = requested
    elif requested:
        alias_action = _action_alias(requested, definition, column)
        if alias_action != requested:
            notes.append(f"action alias {requested!r} normalized to {alias_action!r}")
        normalized["next_action"] = alias_action

    return normalized, notes


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text or "{" not in text:
        return None
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = text.find("{", start)
        if index < 0:
            return None
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(parsed, dict):
            return parsed
        start = index + 1


def _action_key(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _status_key(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _action_alias(action: str, definition: WorkflowDefinition, column: WorkflowColumn) -> str:
    if _is_valid_column_action(definition, column, action):
        return action
    if action in {"approve", "approved", "success", "succeeded", "done", "complete", "completed"}:
        return _preferred_success_action(definition, column) or action
    if action in {"fail", "failed", "failure", "error", "blocked"}:
        return _failure_action(column)
    if action in {"rework", "rewrite"}:
        return "request_rework"
    if action in {"replan"}:
        return "request_replan"
    return action


def _action_for_target(
    definition: WorkflowDefinition,
    column: WorkflowColumn,
    target: str,
    *,
    preferred: str = "",
) -> str | None:
    if preferred and _is_valid_column_action(definition, column, preferred) and _action_target(definition, preferred) == target:
        return preferred
    candidates = [
        column.success_action or "",
        *(column.failure_actions or []),
        "workflow_done",
        "complete",
        "completed",
        "code_ready",
        "apply_succeeded",
        "verification_failed",
        "fail",
        "abandon",
        "retry",
    ]
    candidates.extend(action for action in definition.actions if action not in candidates)
    for action in candidates:
        if action and _is_valid_column_action(definition, column, action) and _action_target(definition, action) == target:
            return action
    return None


def _generic_column_action(
    definition: WorkflowDefinition,
    column: WorkflowColumn,
    raw: dict[str, Any],
    decision: str,
) -> str:
    requested = str(raw.get("next_action") or raw.get("action") or "").strip().lower().replace("-", "_")
    if requested and _is_valid_column_action(definition, column, requested):
        return requested
    if decision == "fail":
        return _failure_action(column)
    if decision in {"request_replan", "request_rework"}:
        for action in [requested, *(column.failure_actions or [])]:
            if action and _is_valid_column_action(definition, column, action):
                return action
        return _failure_action(column)
    success = _preferred_success_action(definition, column)
    if success:
        return success
    return _failure_action(column)


def _preferred_success_action(definition: WorkflowDefinition, column: WorkflowColumn) -> str | None:
    if column.success_action and _is_valid_column_action(definition, column, column.success_action):
        return column.success_action
    preferred_targets = [target for target in column.transition_to if target != "failed"]
    for action, rule in definition.actions.items():
        target = str(rule.get("to") or "").strip().lower() if isinstance(rule, dict) else ""
        if target in preferred_targets:
            return action
    done_target = _action_target(definition, "workflow_done")
    if done_target in column.transition_to and _is_valid_column_action(definition, column, "workflow_done"):
        return "workflow_done"
    return None


def _is_valid_column_action(definition: WorkflowDefinition, column: WorkflowColumn, action: str) -> bool:
    target = _action_target(definition, action)
    if not target:
        return False
    if action in {"fail", "retry", "abandon"}:
        return True
    return target == column.status_key or target in set(column.transition_to or [])


def _should_force_code_ready(definition: WorkflowDefinition, column: WorkflowColumn) -> bool:
    if definition.action("code_ready") is None:
        return False
    if definition.is_coding:
        return True
    policy = column.context_policy if isinstance(column.context_policy, dict) else {}
    if policy.get("requires_apply") is True:
        return True
    return _action_target(definition, "code_ready") in set(column.transition_to or [])


def _generic_done_response(
    task_id: str,
    project_id: str,
    status_key: str,
    state: WorkflowRunState,
) -> IdeChatResponse:
    latest = state.phase_outputs[-1] if state.phase_outputs else {}
    summary = str(latest.get("summary") or "Workflow completed.")
    if state.execute_response is not None:
        response = state.execute_response.model_copy(deep=True)
        response.task_id = task_id
        response.status_key = status_key
        response.reply = response.reply or summary
        response.done = True
        response.ok = True
        response.phase_output = latest or response.phase_output
        response.planning = {
            "project_id": project_id,
            "phase_outputs": len(state.phase_outputs),
            "kind": "generic_workflow_code_result",
        }
        return response
    return IdeChatResponse(
        ok=True,
        reply=summary,
        done=True,
        task_id=task_id,
        status_key=status_key or "done",
        phase_output=latest or None,
        planning={
            "project_id": project_id,
            "phase_outputs": len(state.phase_outputs),
            "kind": "generic_workflow_result",
        },
    )


def _generic_code_response(
    *,
    task_id: str,
    status_key: str,
    raw: dict[str, Any],
    summary: str,
    tool_requests: list[ToolRequest],
) -> IdeChatResponse | None:
    raw_outputs = raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
    normalized_ops = raw.get("ops") or raw_outputs.get("ops") or _code_patch_file_ops(raw, raw_outputs)
    ops = _model_list(FileOp, normalized_ops)
    patch_ops = _model_list(PatchOp, raw.get("patch_ops") or raw_outputs.get("patch_ops"))
    code_tree = raw.get("code_tree") or raw_outputs.get("code_tree")
    if not ops and not patch_ops and not tool_requests and code_tree is None:
        return None
    response = IdeChatResponse(
        ok=bool(raw.get("ok", True)),
        reply=str(raw.get("reply") or summary or ""),
        task_id=task_id,
        status_key=status_key,
        code_tree=str(code_tree) if code_tree is not None else None,
        ops=ops,
        patch_ops=patch_ops,
        tool_requests=tool_requests,
        done=bool(raw.get("done", True)),
    )
    target_root = _code_patch_target_root(raw, raw_outputs)
    if target_root:
        response.planning = {"target_root": target_root}
    return response


def _code_patch_target_root(raw: dict[str, Any], raw_outputs: dict[str, Any] | None = None) -> str:
    outputs = raw_outputs if isinstance(raw_outputs, dict) else raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
    for candidate in _file_bundle_candidates(raw, outputs):
        if not isinstance(candidate, dict):
            continue
        for key in ("target_root", "project_root", "root", "base_dir", "base_path"):
            text = str(candidate.get(key) or "").strip()
            if text:
                return text
    return ""


def _code_patch_file_ops(raw: dict[str, Any], raw_outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert common LLM file-bundle payloads into DevWerk file ops.

    Some models produce an artifact-shaped code bundle such as
    outputs.code_patch.files instead of the IDE protocol's ops array. If the
    bundle contains concrete path/content pairs, it is already an actionable
    code result and should enter the snapshot/apply lifecycle instead of
    falling through to a generic phase artifact.
    """

    candidates = _file_bundle_candidates(raw, raw_outputs)
    files: object = None
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("files"), list):
            files = candidate.get("files")
            break
    if not isinstance(files, list):
        return []

    ops: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("file") or item.get("name") or "").strip().replace("\\", "/")
        content = item.get("content")
        if not path or content is None:
            continue
        operation = str(item.get("op") or item.get("operation") or "create_file").strip() or "create_file"
        op: dict[str, Any] = {
            "op": operation,
            "path": path,
            "content": str(content),
        }
        language = item.get("language") or _language_from_path(path)
        if language:
            op["language"] = str(language)
        ops.append(op)
    return ops


def _file_bundle_candidates(raw: dict[str, Any], raw_outputs: dict[str, Any]) -> list[object]:
    """Return likely file-bundle containers without coupling to column names.

    Dynamic workflows name artifacts freely, so the protocol should care about
    the shape (a dict with files containing path/content), not a hard-coded
    column or artifact label. The common labels are checked first, followed by
    all nested dict values in outputs.
    """

    labels = (
        "code_patch",
        "staged_patch",
        "source_bundle",
        "code_change",
        "code_change_bundle",
        "patch",
        "file_bundle",
        "files_bundle",
    )
    candidates: list[object] = []
    for label in labels:
        candidates.append(raw_outputs.get(label))
        candidates.append(raw.get(label))
    candidates.extend([raw_outputs, raw])
    candidates.extend(value for value in raw_outputs.values() if isinstance(value, dict))
    return candidates


def _language_from_path(path: str) -> str | None:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "java": "java",
        "kt": "kotlin",
        "kts": "kotlin",
        "js": "javascript",
        "ts": "typescript",
        "vue": "vue",
        "json": "json",
        "yml": "yaml",
        "yaml": "yaml",
        "xml": "xml",
        "md": "markdown",
        "sql": "sql",
        "py": "python",
        "html": "html",
        "css": "css",
    }.get(suffix)


def _model_list(model: type[FileOp] | type[PatchOp], value: object) -> list[FileOp] | list[PatchOp]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            _log.debug("generic code response ignored invalid %s item=%s error=%s", model.__name__, item, exc)
    return out


def _semantic_targets(definition: WorkflowDefinition, actions: tuple[str, ...]) -> set[str]:
    targets: set[str] = set()
    for action in actions:
        target = _action_target(definition, action)
        if target:
            targets.add(target)
    return targets


def _success_statuses(definition: WorkflowDefinition) -> set[str]:
    return _semantic_targets(definition, ("workflow_done", "complete", "completed"))


def _failure_statuses(definition: WorkflowDefinition) -> set[str]:
    return _semantic_targets(definition, ("fail", "abandon"))


def _failure_status(definition: WorkflowDefinition) -> str | None:
    return next(iter(_failure_statuses(definition)), None)


def _is_success_terminal(definition: WorkflowDefinition, status_key: str | None) -> bool:
    return str(status_key or "").strip().lower() in _success_statuses(definition)


def _terminal_statuses(definition: WorkflowDefinition) -> set[str]:
    return _success_statuses(definition) | _failure_statuses(definition)


def _safe_fail(task_id: str, payload: dict[str, Any]) -> None:
    try:
        apply_workflow_action(task_id, "fail", payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow safe fail skipped task_id=%s error=%s", task_id, exc)


def _notify_project_conversation_failure(
    task_id: str,
    project_id: str,
    *,
    phase: str,
    reason: str,
    status_key: str,
) -> None:
    content = (
        f"Task failed at `{phase}`.\n\n"
        f"Reason: {reason or 'Workflow failed before producing a result.'}\n\n"
        "The task is now in the failure state. You can inspect the task events/artifacts, "
        "send more guidance, or use Re-run to retry from the workflow retry target."
    )
    try:
        add_project_event(
            project_id,
            "project_conversation_message",
            {
                "role": "assistant",
                "content": content,
                "kind": "workflow_failed",
                "task_id": task_id,
                "status_key": status_key,
                "phase": phase,
                "retryable": True,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow failure project conversation notification skipped task_id=%s error=%s", task_id, exc)


def record_agent_message(
    task_id: str,
    *,
    from_agent: str,
    to_agent: str,
    topic: str,
    content: str,
    related_phase: str | None = None,
) -> dict[str, Any]:
    payload = {
        "id": str(uuid.uuid4()),
        "from_agent": from_agent,
        "to_agent": to_agent,
        "topic": topic,
        "content": content,
        "related_phase": related_phase,
    }
    add_artifact(task_id, artifact_type="agent_message", payload=payload)
    add_event(task_id, "agent_message_recorded", payload)
    return payload


def _build_agent_context(
    task_id: str,
    phase: str,
    agent: str,
    body: dict[str, Any],
    workflow_summary: dict[str, Any],
    required_artifacts: list[str],
) -> dict[str, Any]:
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    project_id = str(task.get("project_id") or body.get("project_id") or "default")
    artifacts = task.get("artifacts") or []
    conversation_context = prepare_conversation_context(task_id, fallback_messages=body.get("messages") or [])
    skill_ids = [str(item) for item in (body.get("_workflow_agent_skills") or []) if str(item).strip()]
    plugin_agent = _plugin_agent_context(body.get("_workflow_plugin_agent_id") or agent)
    return {
        "task_id": task_id,
        "project_id": project_id,
        "phase": phase,
        "agent": agent,
        "original_user_request": _first_user_text(conversation_context.get("messages") or body.get("messages") or []),
        "conversation": {
            "messages": conversation_context.get("messages") or [],
            "summary": conversation_context.get("summary") or "",
            "debug": context_debug_payload(conversation_context),
        },
        "task": {k: task.get(k) for k in ("id", "project_id", "title", "description", "status_key", "metadata")},
        "workflow": workflow_summary,
        "previous_artifacts": _latest_artifacts(artifacts, required_artifacts),
        "task_events": _event_summary(task.get("events") or []),
        "task_memory": _compact_task_memory(task, conversation_context),
        "project_memory": _compact_project_memory(read_project_memory(project_id)),
        "plugin_agent": plugin_agent,
        "skills": resolve_agent_skills(project_id, skill_ids),
        "capabilities": _capability_context(body),
        "tool_results": _compact_tool_results(body.get("tool_results")),
        "workspace": _workspace_summary(body.get("workspace")),
    }


def _capability_context(body: dict[str, Any]) -> dict[str, Any]:
    broker = CapabilityBroker()
    client_offers = []
    for offer in broker.offers(body.get("client_capabilities")).values():
        client_offers.append(
            {
                "capability": offer.capability,
                "provider": offer.provider,
                "implementation": offer.implementation,
            }
        )
    catalog = capability_catalog()
    return {
        "agent": [str(item) for item in (body.get("_workflow_agent_capabilities") or []) if str(item).strip()],
        "client_offers": sorted(client_offers, key=lambda item: item["capability"]),
        "catalog": catalog.get("capabilities") or [],
        "plugin_mcp_servers": catalog.get("plugin_mcp_servers") or [],
        "plugin_agents": catalog.get("plugin_agents") or [],
    }


def _plugin_agent_context(agent_ref: object) -> dict[str, Any] | None:
    text = str(agent_ref or "").strip()
    if not text or (":" not in text and "." not in text):
        return None
    try:
        agent = get_plugin_agent(text)
    except (KeyError, ValueError):
        return {
            "agent_id": text,
            "scope": "plugin",
            "available": False,
            "reason": "plugin agent is not installed, enabled, or valid",
        }
    return {
        "agent_id": agent.get("agent_id"),
        "plugin_id": agent.get("plugin_id"),
        "scope": "plugin",
        "available": True,
        "summary": agent.get("summary"),
        "content": agent.get("content"),
        "mcp_servers": agent.get("mcp_servers") or [],
    }


def _allowed_client_tool_requests(
    value: object,
    capabilities: object,
    *,
    project_root: object = None,
) -> list[ToolRequest]:
    broker = CapabilityBroker()
    allowed = broker.available(capabilities)
    if not isinstance(value, list) or not allowed:
        return []
    requests: list[ToolRequest] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        try:
            normalized_item = normalize_tool_request(item, index)
        except Exception as exc:  # noqa: BLE001
            _log.debug("client tool request ignored during normalization item=%s error=%s", item, exc)
            continue
        if str(normalized_item.get("tool") or "").strip() not in allowed:
            continue
        try:
            request = ToolRequest.model_validate(normalized_item)
        except Exception:  # noqa: BLE001
            continue
        offer = broker.resolve(capabilities, request.tool)
        if offer is None:
            continue
        requested_capability = offer.capability
        request.tool = offer.implementation or offer.capability
        if requested_capability == "process.run" and "cwd" in request.args:
            normalized_cwd = _normalize_client_cwd(request.args.get("cwd"), project_root)
            if normalized_cwd is None:
                continue
            request.args["cwd"] = normalized_cwd
        if request.id in seen_ids:
            continue
        requests.append(request)
        seen_ids.add(request.id)
        if len(requests) >= 8:
            break
    return requests


def _normalize_client_cwd(value: object, project_root: object) -> str | None:
    cwd = str(value or "").strip().replace("\\", "/").rstrip("/")
    root = str(project_root or "").strip().replace("\\", "/").rstrip("/")
    if cwd in {"", "."}:
        return ""
    if root and cwd.lower() == root.lower():
        return ""
    root_name = root.rsplit("/", 1)[-1] if root else ""
    if root_name and cwd.lower() == root_name.lower():
        return ""
    if root and cwd.lower().startswith(root.lower() + "/"):
        cwd = cwd[len(root) + 1 :]
    elif root_name and cwd.lower().startswith(root_name.lower() + "/"):
        cwd = cwd[len(root_name) + 1 :]
    if cwd.startswith("/") or (len(cwd) >= 2 and cwd[1] == ":"):
        return None
    parts = [part for part in cwd.split("/") if part not in {"", "."}]
    if ".." in parts:
        return None
    return "/".join(parts)


def _latest_task_artifact_payload(task_id: str, artifact_type: str) -> dict[str, Any] | None:
    try:
        task = get_task(task_id).get("task") or {}
    except Exception:  # noqa: BLE001
        return None
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            payload = artifact.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _effective_project_root(
    body: dict[str, Any],
    *,
    raw: dict[str, Any] | None = None,
    response: IdeChatResponse | None = None,
) -> str:
    project_id = str(body.get("project_id") or "default")
    settings_payload = get_project_settings(project_id)
    project_settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = project_settings.get("parameters") if isinstance(project_settings, dict) else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    workspace = body.get("workspace") if isinstance(body.get("workspace"), dict) else {}
    source_map = workspace.get("source_map") if isinstance(workspace.get("source_map"), dict) else {}

    candidates = [
        body.get("project_root"),
        metadata.get("project_root"),
        metadata.get("workspace_root"),
        metadata.get("local_project_root"),
        parameters.get("project_root") if isinstance(parameters, dict) else None,
        parameters.get("workspace_root") if isinstance(parameters, dict) else None,
        (response.planning or {}).get("target_root") if response and isinstance(response.planning, dict) else None,
        _code_patch_target_root(raw or {}) if isinstance(raw, dict) else None,
        source_map.get("root") if isinstance(source_map, dict) else None,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _ready_response(task_id: str, project_id: str, status_key: str, execute_response: IdeChatResponse) -> IdeChatResponse:
    response = execute_response.model_copy(deep=True)
    response.task_id = task_id
    response.status_key = status_key or "complete"
    if response.status_key == "ready_to_apply":
        response.next_action = "apply_result"
        response.waiting_for = "apply_result"
        response.done = False
    else:
        response.waiting_for = None
        response.done = bool(response.done)
    response.ok = True
    verification_requests = _ensure_post_apply_verification_requests(response, project_id)
    if verification_requests:
        response.tool_requests = verification_requests
    return response


def _requires_post_apply_verification(project_id: str) -> bool:
    settings_payload = get_project_settings(project_id)
    project_settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = project_settings.get("parameters") if isinstance(project_settings, dict) else {}
    return bool(configured_post_apply_tool_requests(project_settings)) or not _allow_done_without_verification(parameters)


def _allow_done_without_verification(parameters: dict[str, Any]) -> bool:
    lifecycle = parameters.get("coding_lifecycle") if isinstance(parameters, dict) else {}
    return bool(isinstance(lifecycle, dict) and lifecycle.get("allow_done_without_verification") is True)


def _ensure_post_apply_verification_requests(response: IdeChatResponse, project_id: str) -> list[ToolRequest]:
    by_id = {req.id: req for req in response.tool_requests}
    settings_payload = get_project_settings(project_id)
    project_settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    for request in configured_post_apply_tool_requests(project_settings):
        by_id.setdefault(request.id, request)
    return list(by_id.values())


def _failure_response(task_id: str, error_code: str, error_message: str, *, status_key: str = "failed") -> IdeChatResponse:
    return IdeChatResponse(
        ok=False,
        reply="",
        done=True,
        task_id=task_id,
        status_key=status_key,
        error_code=error_code,
        error_message=error_message,
        retryable=False,
    )


def _waiting_response(
    task_id: str,
    *,
    status_key: str,
    waiting_for: str,
    reply: str,
    interaction: dict[str, Any],
) -> IdeChatResponse:
    return IdeChatResponse(
        ok=True,
        reply=reply,
        done=False,
        task_id=task_id,
        status_key=status_key,
        next_action=waiting_for,
        waiting_for=waiting_for,
        interaction=interaction,
    )


def _normalize_review_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized


def _patch_paths(response: IdeChatResponse) -> list[str]:
    paths = []
    for patch in response.patch_ops:
        for line in (patch.content or "").splitlines():
            if line.startswith("+++ b/"):
                paths.append(line.removeprefix("+++ b/").strip())
    return paths


def _latest_artifacts(artifacts: list[dict[str, Any]], artifact_types: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    wanted = set(artifact_types)
    for artifact in reversed(artifacts):
        if not isinstance(artifact, dict):
            continue
        artifact_type = artifact.get("artifact_type")
        if artifact_type in wanted and artifact_type not in out:
            out[str(artifact_type)] = artifact.get("payload") or {}
    return out


def _workspace_summary(workspace: object) -> dict[str, Any]:
    if not isinstance(workspace, dict):
        return {"present": False}
    source_map = workspace.get("source_map") if isinstance(workspace.get("source_map"), dict) else None
    diagnostics = workspace.get("syntax_diagnostics")
    return {
        "present": True,
        "root_id": workspace.get("root_id"),
        "tree_preview_chars": len(str(workspace.get("tree_preview") or "")),
        "source_map_present": bool(source_map),
        "source_map_total_files": source_map.get("total_files") if source_map else None,
        "source_map_indexed_files": source_map.get("indexed_files") if source_map else None,
        "syntax_diagnostics_count": len(diagnostics) if isinstance(diagnostics, list) else 0,
    }


def _event_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": event.get("event_type"),
            "from_status": event.get("from_status"),
            "to_status": event.get("to_status"),
            "created_at": event.get("created_at"),
        }
        for event in events[-30:]
        if isinstance(event, dict)
    ]


def _compact_project_memory(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "frameworks": (memory.get("frameworks") or [])[-20:],
        "paths": (memory.get("paths") or [])[-80:],
        "commands": (memory.get("commands") or [])[-30:],
        "rules": (memory.get("rules") or [])[-30:],
        "recent_phase_summaries": (memory.get("phase_summaries") or [])[-20:],
    }


def _compact_task_memory(task: dict[str, Any], conversation_context: dict[str, Any]) -> dict[str, Any]:
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    phase_outputs = [
        artifact.get("payload") or {}
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_type") == "workflow_phase_output"
    ]
    return {
        "task_id": task.get("id"),
        "status_key": task.get("status_key"),
        "conversation_summary": conversation_context.get("summary") or "",
        "conversation_message_count": len(conversation_context.get("messages") or []),
        "recent_events": _event_summary(task.get("events") or [])[-20:],
        "artifact_types": [
            str(artifact.get("artifact_type") or "")
            for artifact in artifacts[-40:]
            if isinstance(artifact, dict)
        ],
        "recent_phase_outputs": [
            {
                "phase": item.get("phase"),
                "agent": item.get("agent"),
                "status_key": item.get("status_key"),
                "summary": item.get("summary"),
                "decision": item.get("decision"),
                "next_action": item.get("next_action"),
            }
            for item in phase_outputs[-20:]
            if isinstance(item, dict)
        ],
    }


def _compact_tool_results(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[-20:]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": item.get("id"),
                "ok": item.get("ok"),
                "content": str(item.get("content") or "")[:12000],
                "error": str(item.get("error") or "")[:4000] or None,
            }
        )
    return out


def _context_log_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": context.get("phase"),
        "agent": context.get("agent"),
        "artifact_keys": sorted((context.get("previous_artifacts") or {}).keys()),
        "event_count": len(context.get("task_events") or []),
        "task_memory_keys": sorted((context.get("task_memory") or {}).keys()),
        "project_memory_keys": sorted((context.get("project_memory") or {}).keys()),
        "plugin_agent": {
            "agent_id": (context.get("plugin_agent") or {}).get("agent_id"),
            "available": (context.get("plugin_agent") or {}).get("available"),
            "plugin_id": (context.get("plugin_agent") or {}).get("plugin_id"),
        }
        if isinstance(context.get("plugin_agent"), dict)
        else None,
        "skills": [
            {
                "id": skill.get("id"),
                "scope": skill.get("scope"),
                "enabled": skill.get("enabled"),
                "chars": len(str(skill.get("content") or "")),
            }
            for skill in (context.get("skills") or [])
            if isinstance(skill, dict)
        ],
        "workspace": context.get("workspace"),
    }


def _first_user_text(messages: list[Any]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            return str(item.get("content") or "")
    return ""


def _executable_columns(definition: WorkflowDefinition) -> list[WorkflowColumn]:
    return sorted([column for column in definition.columns if column.executable], key=lambda col: col.position)


def _first_executable_at_or_after(definition: WorkflowDefinition, status_key: str) -> WorkflowColumn | None:
    status = str(status_key or "").strip().lower()
    columns = sorted(definition.columns, key=lambda col: col.position)
    anchor = next((col for col in columns if col.status_key == status), None)
    if anchor is None:
        return None
    return next((col for col in columns if col.executable and col.position >= anchor.position), None)


def _next_executable_after_transition(
    definition: WorkflowDefinition,
    status_key: str,
    current: WorkflowColumn,
    *,
    include_current: bool = False,
) -> WorkflowColumn | None:
    status = str(status_key or "").strip().lower()
    columns = sorted(definition.columns, key=lambda col: col.position)
    anchor = next((col for col in columns if col.status_key == status), None)
    if anchor is None:
        return None
    minimum = anchor.position if include_current else (anchor.position + 1 if anchor.status_key == current.status_key else anchor.position)
    return next((col for col in columns if col.executable and col.position >= minimum), None)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _action_target(definition: WorkflowDefinition, action: str) -> str | None:
    rule = definition.action(action)
    if not isinstance(rule, dict):
        return None
    target = str(rule.get("to") or "").strip().lower()
    return target or None


def _entry_action_for_status(definition: WorkflowDefinition, status_key: str) -> str | None:
    status = str(status_key or "").strip().lower()
    candidates = []
    for action, rule in definition.actions.items():
        if str(rule.get("to") or "").strip().lower() == status:
            candidates.append(action)
    preferred = [f"{status}_started", f"start_{status}", status]
    for name in preferred:
        if name in candidates:
            return name
    for action in candidates:
        if action.endswith("_started"):
            return action
    return None


def _failure_action(column: WorkflowColumn) -> str:
    actions = column.failure_actions or []
    return "fail" if "fail" in actions else (actions[0] if actions else "fail")


def _active_agent(body: dict[str, Any]) -> str:
    agent = str(body.get("_workflow_agent_id") or "").strip()
    if not agent:
        raise UnsupportedJobError("Scheduled job did not provide a derived agent identity.")
    return agent


def _current_status(task_id: str) -> str:
    try:
        return str((get_task(task_id).get("task") or {}).get("status_key") or "")
    except Exception:  # noqa: BLE001
        return ""


def _event(task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    add_event(task_id, event_type, payload)
