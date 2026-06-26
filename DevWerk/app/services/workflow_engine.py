from __future__ import annotations

import json
import logging
import uuid
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.models.protocol import IdeChatResponse, ToolRequest
from app.models.plan import PlanResponse
from app.services.agent_definition import default_agent_catalog
from app.services.capability_broker import CapabilityBroker
from app.services.coder_harness import build_code_context_summary
from app.services.conversation_context import context_debug_payload, prepare_conversation_context
from app.services.kanban import (
    add_artifact,
    add_event,
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
from app.services.job_scheduler import JobScheduler
from app.services.llm_factory import get_llm_client
from app.services.provider_errors import is_retryable_llm_error
from app.services.verification_policy import configured_post_apply_tool_requests, verification_feedback_summary
from app.services.workflow import apply_workflow_action, record_phase_output
from app.services.workflow_definition import WorkflowColumn, WorkflowDefinition, workflow_from_dict

_log = logging.getLogger("devwerk.workflow_engine")

PlanRunner = Callable[[dict[str, Any]], Awaitable[PlanResponse]]
CodingRunner = Callable[[dict[str, Any]], Awaitable[IdeChatResponse]]
ReviewRunner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class WorkflowRunState:
    context_bundle: dict[str, Any] = field(default_factory=dict)
    plan_response: PlanResponse | None = None
    execute_response: IdeChatResponse | None = None
    review_feedback: dict[str, Any] | None = None
    phase_outputs: list[dict[str, Any]] = field(default_factory=list)
    rework_rounds: int = 0


@dataclass
class ColumnResult:
    action: str
    decision: str = "approve"
    response: IdeChatResponse | None = None
    target_status: str | None = None


class WorkflowEngine:
    def __init__(self, *, plan_runner: PlanRunner, coding_runner: CodingRunner, review_runner: ReviewRunner | None = None):
        self.plan_runner = plan_runner
        self.coding_runner = coding_runner
        self.review_runner = review_runner
        self._job_handlers = {
            "index_project_context": self._run_context_column,
            "produce_change_plan": self._run_plan_column,
            "generate_code_change": self._run_coding_column,
            "review_code_change": self._run_review_column,
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
            response = _failure_response(task_id, "WORKFLOW_EMPTY", "Workflow has no executable columns.")
            apply_workflow_action(task_id, "fail", {"phase": "workflow", "reason": response.error_message})
            _event(task_id, "workflow_finished", {"ok": False, "phase": "workflow", "status_key": "failed"})
            add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
            return

        state = WorkflowRunState()
        resume_action = str(body.get("resume_action") or "").strip().lower()
        if resume_action in {"confirm_plan", "revise_plan", "message"}:
            state.plan_response = _latest_plan_response(task_id)
        resume_feedback = _verification_resume_feedback(body)
        if resume_feedback:
            state.plan_response = _latest_plan_response(task_id)
            state.review_feedback = resume_feedback
            _event(
                task_id,
                "workflow_resumed",
                {
                    "reason": "apply_failed" if body.get("client_feedback") else "verification_failed",
                    "has_previous_plan": state.plan_response is not None,
                    "summary": resume_feedback.get("summary"),
                },
            )

        resume_status = str(body.get("resume_status") or "").strip().lower()
        resume_column = definition.column(resume_status) if resume_status else None
        current = (
            resume_column
            if resume_feedback and resume_column is not None and resume_column.executable
            else (_resume_column(definition, state) if resume_feedback else executable[0])
        )
        if resume_action == "confirm_plan" and state.plan_response is not None:
            current = _column_for_contract(definition, self._scheduler, "code_change_bundle") or current
            update_conversation(task_id, state="running", waiting_for=None, active_column=current.status_key)
            _event(task_id, "workflow_resumed", {"reason": "plan_confirmed", "status_key": current.status_key})
        elif resume_action in {"revise_plan", "message", "tool_result"}:
            current = _column_for_contract(definition, self._scheduler, "plan_bundle") or current
            update_conversation(task_id, state="running", waiting_for=None, active_column=current.status_key)
            _event(task_id, "workflow_resumed", {"reason": resume_action, "status_key": current.status_key})
        if current is None:
            current = executable[0]

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
                response = _failure_response(task_id, "UNSUPPORTED_WORKFLOW_JOB", str(exc))
                apply_workflow_action(task_id, "fail", {"phase": current.status_key, "reason": response.error_message})
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": "failed"})
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
                    boundary_payload["terminal"] = bool(
                        result.response.done or result.response.status_key in {"done", "failed"}
                    )
                    _event(task_id, "workflow_finished", boundary_payload)
                add_artifact(task_id, artifact_type="workflow_result", payload=result.response.model_dump())
                return

            if result.action in {"request_replan", "request_recoding"}:
                state.rework_rounds += 1
                if state.rework_rounds > max_rework_rounds:
                    response = _waiting_response(
                        task_id,
                        status_key=current.status_key,
                        waiting_for="user_guidance",
                        reply=str((state.review_feedback or {}).get("summary") or "Workflow needs guidance after repeated rework."),
                        interaction={
                            "type": "rework_guidance",
                            "reason": "rework_budget",
                            "review": state.review_feedback or {},
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
                        "review": state.review_feedback or {},
                    },
                )

            if not target_status:
                response = _failure_response(task_id, "WORKFLOW_BAD_ACTION", f"Workflow action {result.action!r} has no target.")
                apply_workflow_action(task_id, "fail", {"phase": current.status_key, "reason": response.error_message})
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": "failed"})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            next_column = _next_executable_after_transition(definition, target_status, current)
            if next_column is None:
                if target_status == "done" and state.execute_response is None and result.decision == "approve":
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
                )
                apply_workflow_action(task_id, "fail", {"phase": current.status_key, "reason": response.error_message})
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": "failed"})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            if _column_contract(next_column, self._scheduler) == "plan_bundle" and result.action != "request_replan":
                state.review_feedback = None
            current = next_column

        response = _failure_response(task_id, "WORKFLOW_LOOP_EXHAUSTED", "Workflow exhausted without producing a result.")
        apply_workflow_action(task_id, "fail", {"phase": "workflow", "reason": response.error_message})
        _event(task_id, "workflow_finished", {"ok": False, "phase": "workflow", "status_key": "failed"})
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
            if job.template.id == "generate_code_change":
                outcome = await handler(task_id, job_body, definition, workflow_summary, column, state)
            elif handler is None:
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
        context = _build_agent_context(
            task_id,
            column.status_key,
            agent,
            body,
            workflow_summary,
            column.input_artifacts or [],
        )
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

        warnings = [str(item) for item in (raw.get("warnings") or []) if str(item).strip()]
        summary = str(raw.get("summary") or raw.get("reply") or f"{column.title} completed.").strip()
        outputs = raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
        if not outputs:
            outputs = {
                key: value
                for key, value in raw.items()
                if key not in {"phase", "agent", "summary", "reply", "warnings", "decision", "next_action", "tool_requests"}
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

        tool_requests = _allowed_client_tool_requests(
            raw.get("tool_requests") or [],
            body.get("client_capabilities"),
            project_root=body.get("project_root"),
        )
        decision = _generic_decision(raw.get("decision"), raw.get("next_action"), has_tool_requests=bool(tool_requests))
        if decision == "need_client_tool" and tool_requests:
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
        moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "session_id": output["session_id"]})
        _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "approve"})
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision="approve")

    async def _run_plan_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        agent = _active_agent(body)
        _event(task_id, "workflow_column_started", {"status_key": column.status_key, "agent": agent})
        _event(
            task_id,
            "agent_context_built",
            {
                "phase": column.status_key,
                "agent": agent,
                "context": _context_log_summary(
                    _build_agent_context(task_id, column.status_key, agent, body, workflow_summary, column.input_artifacts or [])
                ),
            },
        )
        conversation_context = prepare_conversation_context(task_id, fallback_messages=body.get("messages") or [])
        plan_messages = list(conversation_context.get("messages") or body.get("messages") or [])
        client_tool_results = body.get("tool_results") if isinstance(body.get("tool_results"), list) else []
        if client_tool_results:
            plan_messages.append(
                {
                    "role": "user",
                    "content": "client_tool_results:\n" + json.dumps(client_tool_results, ensure_ascii=False),
                }
            )
            _event(
                task_id,
                "plan_client_tool_results_injected",
                {
                    "phase": column.status_key,
                    "result_count": len(client_tool_results),
                    "result_ids": [str(item.get("id") or "") for item in client_tool_results if isinstance(item, dict)],
                },
            )
        if state.review_feedback:
            replan_feedback = {
                "review": _compact_review_feedback(state.review_feedback),
                "verification": _compact_verification_feedback(state.review_feedback),
            }
            plan_messages.append(
                {
                    "role": "user",
                    "content": "workflow_replan_feedback:\n"
                    + json.dumps(replan_feedback, ensure_ascii=False),
                }
            )
        _event(task_id, "agent_prompt_context_prepared", {"phase": column.status_key, **context_debug_payload(conversation_context)})
        task_record = (get_task(task_id).get("task") or {})
        preflight_requests = []
        if not client_tool_results and _task_requires_executable_verification(task_record):
            preflight_requests = _allowed_client_tool_requests(
                [
                    {
                        "id": "project-compile-evidence",
                        "tool": "project.compile",
                        "args": {
                            "timeout_seconds": 300,
                            "max_errors": 200,
                            "reason": "Collect authoritative compiler evidence before planning a compile-error repair.",
                        },
                    }
                ],
                body.get("client_capabilities"),
                project_root=body.get("project_root"),
            )
        if preflight_requests:
            _event(
                task_id,
                "plan_client_tool_policy_triggered",
                {
                    "phase": column.status_key,
                    "reason": "compile_error_evidence",
                    "requests": [request.model_dump() for request in preflight_requests],
                },
            )
            plan_response = PlanResponse(
                summary="Collecting authoritative compiler diagnostics from a connected capability provider before planning.",
                tool_requests=preflight_requests,
                next_action="need_client_tool",
            )
        else:
            plan_response = await self.plan_runner(
                dict(
                    body,
                    task_id=task_id,
                    messages=plan_messages,
                    _workflow_engine_managed=True,
                )
            )
        plan_response.task_id = task_id
        state.plan_response = plan_response
        if plan_response.tool_requests:
            client_requests = _allowed_client_tool_requests(
                [request.model_dump() for request in plan_response.tool_requests],
                body.get("client_capabilities"),
                project_root=body.get("project_root"),
            )
            if not client_requests:
                action = _failure_action(column)
                message = "Planner requested client tools that are not declared by this client."
                moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": message})
                response = _failure_response(
                    task_id,
                    "CLIENT_TOOL_UNAVAILABLE",
                    message,
                    status_key=(moved.get("task") or {}).get("status_key") or "failed",
                )
                return ColumnResult(action=action, decision="fail", response=response, target_status=response.status_key)

            request_payload = {
                "phase": column.status_key,
                "agent": agent,
                "requests": [request.model_dump() for request in client_requests],
            }
            add_artifact(task_id, artifact_type="client_tool_request", payload=request_payload)
            output = record_phase_output(
                task_id,
                phase=column.status_key,
                agent=agent,
                status_key=_current_status(task_id),
                summary=plan_response.summary or "Planner requested client-provided project evidence.",
                inputs={"client_capabilities": body.get("client_capabilities") or {}},
                outputs=request_payload,
                warnings=plan_response.warnings,
                decision="need_client_tool",
                next_action="tool_result",
            )
            response = _waiting_response(
                task_id,
                status_key=_current_status(task_id),
                waiting_for="client_tool",
                reply=plan_response.summary or "Collecting project evidence from a connected capability provider.",
                interaction={
                    "type": "client_tool",
                    "reason": "planner_evidence",
                    "phase": column.status_key,
                    "session_id": output["session_id"],
                    "actions": ["tool_result", "cancel"],
                },
            )
            response.tool_requests = client_requests
            update_conversation(task_id, state="waiting_client", waiting_for="client_tool", active_column=column.status_key)
            _event(task_id, "workflow_client_tool_requested", request_payload)
            _event(task_id, "workflow_column_waiting", {**request_payload, "waiting_for": "client_tool"})
            return ColumnResult(action="need_client_tool", target_status=response.status_key, decision="wait", response=response)

        add_artifact(task_id, artifact_type=column.output_artifact or "plan_bundle", payload=plan_response.model_dump())
        _event(task_id, "agent_output_recorded", {"phase": column.status_key, "agent": agent, "artifact": column.output_artifact or "plan_bundle"})
        if not plan_response.ok:
            action = _failure_action(column)
            moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": plan_response.error_message})
            _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "fail"})
            response = IdeChatResponse(
                ok=False,
                reply="",
                done=True,
                task_id=task_id,
                status_key=(moved.get("task") or {}).get("status_key") or plan_response.status_key or "failed",
                error_code=plan_response.error_code or "PLAN_ERROR",
                error_message=plan_response.error_message or "Planning failed.",
                retryable=True,
            )
            return ColumnResult(action=action, decision="fail", response=response, target_status=response.status_key)

        if not plan_response.files:
            action = _failure_action(column)
            message = "Planner produced no files for a coding workflow."
            moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": message})
            _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "fail"})
            response = _failure_response(task_id, "EMPTY_PLAN", message, status_key=(moved.get("task") or {}).get("status_key") or "failed")
            return ColumnResult(action=action, decision="fail", response=response, target_status=response.status_key)

        action = column.success_action or "plan_ready"
        moved = apply_workflow_action(
            task_id,
            action,
            {"phase": column.status_key, "files": len(plan_response.files), "session_id": plan_response.session_id},
        )
        _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "approve"})
        if _requires_plan_confirmation(body):
            reply = _plan_confirmation_text(plan_response)
            append_conversation_message(
                task_id,
                role="assistant",
                content=reply,
                message_type="plan_proposal",
                metadata={"files": [file.model_dump() for file in plan_response.files]},
            )
            update_conversation(task_id, state="waiting_user", waiting_for="plan_confirmation", active_column=column.status_key)
            response = _waiting_response(
                task_id,
                status_key=(moved.get("task") or {}).get("status_key") or column.status_key,
                waiting_for="plan_confirmation",
                reply=reply,
                interaction={
                    "type": "plan_confirmation",
                    "reason": "plan_confirmation",
                    "summary": plan_response.summary,
                    "files": [file.model_dump() for file in plan_response.files],
                    "actions": ["confirm_plan", "revise_plan", "cancel"],
                },
            )
            _event(task_id, "workflow_waiting_user", {"phase": column.status_key, "waiting_for": "plan_confirmation"})
            return ColumnResult(action=action, target_status=response.status_key, decision="wait", response=response)
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision="approve")

    async def _run_coding_column(
        self,
        task_id: str,
        body: dict[str, Any],
        definition: WorkflowDefinition,
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        if state.plan_response is None:
            action = _failure_action(column)
            message = "Coder column cannot run without a plan artifact."
            moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": message})
            response = _failure_response(task_id, "MISSING_PLAN", message, status_key=(moved.get("task") or {}).get("status_key") or "failed")
            return ColumnResult(action=action, decision="fail", response=response, target_status=response.status_key)

        agent = _active_agent(body)
        enter_action = _entry_action_for_status(definition, column.status_key)
        if enter_action and _current_status(task_id) != column.status_key:
            apply_workflow_action(task_id, enter_action, {"phase": column.status_key, "approved_paths": [file.path for file in state.plan_response.files]})

        _event(task_id, "workflow_column_started", {"status_key": column.status_key, "agent": agent})
        _event(
            task_id,
            "agent_context_built",
            {
                "phase": column.status_key,
                "agent": agent,
                "context": _context_log_summary(
                    _build_agent_context(task_id, column.status_key, agent, body, workflow_summary, column.input_artifacts or [])
                ),
            },
        )
        approved_paths = [file.path for file in state.plan_response.files]
        conversation_context = prepare_conversation_context(task_id, fallback_messages=body.get("messages") or [])
        coding_messages = _coding_phase_messages(
            conversation_context.get("messages") or body.get("messages") or [],
            state.plan_response,
            state.review_feedback,
            previous_revision=state.execute_response,
            phase=column.status_key,
        )
        execute_body = {
            "project_id": body.get("project_id"),
            "task_id": task_id,
            "mode": body.get("mode", "agent"),
            "project_root": body.get("project_root"),
            "messages": coding_messages,
            "workspace": body.get("workspace"),
            "approved_paths": approved_paths,
            "approved_ops": [],
            "client_capabilities": body.get("client_capabilities") or {},
            "_workflow_engine_managed": True,
            "_workflow_agent_model_route": body.get("_workflow_agent_model_route"),
        }
        _event(
            task_id,
            "coding_context_prepared",
            {
                "phase": column.status_key,
                "plan_files": len(state.plan_response.files),
                "review_feedback": bool(state.review_feedback),
                "message_count": len(coding_messages),
                "missing_changed_files": (state.review_feedback or {}).get("missing_changed_files") or [],
                "unplanned_changed_files": (state.review_feedback or {}).get("unplanned_changed_files") or [],
            },
        )
        execute_response = await self.coding_runner(execute_body)
        execute_response.task_id = task_id
        execute_response.tool_requests = _allowed_client_tool_requests(
            [request.model_dump() for request in execute_response.tool_requests],
            body.get("client_capabilities"),
            project_root=body.get("project_root"),
        )
        state.execute_response = execute_response
        changed_paths = [op.path for op in execute_response.ops] + _patch_paths(execute_response)
        revision = create_revision(
            task_id,
            summary=execute_response.reply,
            ops=[op.model_dump() for op in execute_response.ops],
            patch_ops=[op.model_dump() for op in execute_response.patch_ops],
            changed_paths=changed_paths,
        )
        add_artifact(task_id, artifact_type=column.output_artifact or "code_change_bundle", payload=execute_response.model_dump())
        _event(task_id, "agent_output_recorded", {"phase": column.status_key, "agent": agent, "artifact": column.output_artifact or "code_change_bundle"})
        if not execute_response.ok:
            action = _failure_action(column)
            moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": execute_response.error_message})
            _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "fail"})
            execute_response.status_key = (moved.get("task") or {}).get("status_key") or "failed"
            return ColumnResult(action=action, decision="fail", response=execute_response, target_status=execute_response.status_key)

        action = column.success_action or "coding_ready"
        moved = apply_workflow_action(
            task_id,
            action,
            {
                "phase": column.status_key,
                "ops": len(execute_response.ops),
                "patch_ops": len(execute_response.patch_ops),
                "session_id": execute_response.session_id,
                "revision_id": revision["id"],
            },
        )
        _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": "approve"})
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision="approve")

    async def _run_review_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        column: WorkflowColumn,
        state: WorkflowRunState,
    ) -> ColumnResult:
        if state.plan_response is None or state.execute_response is None:
            action = _failure_action(column)
            message = "Reviewer column requires plan and code-change artifacts."
            moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": message})
            response = _failure_response(task_id, "MISSING_REVIEW_INPUT", message, status_key=(moved.get("task") or {}).get("status_key") or "failed")
            return ColumnResult(action=action, decision="fail", response=response, target_status=response.status_key)

        agent = _active_agent(body)
        _event(task_id, "workflow_column_started", {"status_key": column.status_key, "agent": agent})
        context = _build_agent_context(task_id, column.status_key, agent, body, workflow_summary, column.input_artifacts or [])
        _event(task_id, "agent_context_built", {"phase": column.status_key, "agent": agent, "context": _context_log_summary(context)})
        prior_changed_paths = (
            state.review_feedback.get("applied_changed_paths") or []
            if isinstance(state.review_feedback, dict)
            else []
        )
        review_result = _review_result(
            state.plan_response,
            state.execute_response,
            prior_changed_paths=prior_changed_paths,
        )
        protocol_decision = review_result["decision"]
        semantic_review: dict[str, Any] | None = None
        if protocol_decision == "approve" and self.review_runner is not None:
            semantic_review = await self.review_runner(
                {
                    "task": context.get("task"),
                    "plan": state.plan_response.model_dump(),
                    "candidate_revision": state.execute_response.model_dump(),
                    "workspace_summary": context.get("workspace"),
                    "verification_feedback": _compact_verification_feedback(state.review_feedback),
                    "client_capabilities": body.get("client_capabilities") or {},
                    "verification_required": _task_requires_executable_verification(context.get("task")),
                    "_workflow_agent_model_route": body.get("_workflow_agent_model_route"),
                }
            )
            semantic_decision = str(semantic_review.get("decision") or "approve").strip().lower()
            if semantic_decision == "fail":
                semantic_decision = "request_recoding"
                semantic_review["decision"] = semantic_decision
                semantic_review["warnings"] = list(semantic_review.get("warnings") or []) + [
                    "Terminal reviewer failure was converted to recoding because the candidate can be revised."
                ]
            if semantic_decision in {"approve", "request_recoding", "request_replan", "fail"}:
                review_result["decision"] = semantic_decision
            verification_requests = _allowed_client_tool_requests(
                semantic_review.get("verification_tool_requests"),
                body.get("client_capabilities"),
                project_root=body.get("project_root"),
            )
            if verification_requests:
                by_id = {request.id: request for request in state.execute_response.tool_requests}
                for request in verification_requests:
                    by_id[request.id] = request
                state.execute_response.tool_requests = list(by_id.values())
        decision = review_result["decision"]
        action = (column.success_action or "approve") if decision == "approve" else decision
        review_bundle = {
            "decision": action,
            "summary": str((semantic_review or {}).get("summary") or _review_summary(decision, review_result)),
            "plan_files": [file.path for file in state.plan_response.files],
            "changed_files": [op.path for op in state.execute_response.ops] + _patch_paths(state.execute_response),
            "normalized_plan_files": review_result["normalized_plan_files"],
            "normalized_changed_files": review_result["normalized_changed_files"],
            "missing_changed_files": review_result["missing_changed_files"],
            "required_missing_files": review_result["required_missing_files"],
            "unplanned_changed_files": review_result["unplanned_changed_files"],
            "ops": len(state.execute_response.ops),
            "patch_ops": len(state.execute_response.patch_ops),
            "tool_requests": len(state.execute_response.tool_requests),
            "protocol_decision": protocol_decision,
            "semantic_review": semantic_review,
            "verification_tool_requests": [request.model_dump() for request in state.execute_response.tool_requests],
            "applied_changed_paths": prior_changed_paths,
        }
        state.review_feedback = review_bundle
        _log.debug(
            "workflow review decision task_id=%s decision=%s action=%s plan=%s changed=%s missing=%s unplanned=%s",
            task_id,
            decision,
            action,
            review_result["normalized_plan_files"],
            review_result["normalized_changed_files"],
            review_result["missing_changed_files"],
            review_result["unplanned_changed_files"],
        )
        add_artifact(task_id, artifact_type=column.output_artifact or "review_bundle", payload=review_bundle)
        output = record_phase_output(
            task_id,
            phase=column.status_key,
            agent=agent,
            status_key="ready_to_apply" if decision == "approve" else decision,
            summary=review_bundle["summary"],
            inputs=context,
            outputs=review_bundle,
            warnings=list((semantic_review or {}).get("warnings") or []),
            decision=action,
            next_action="apply_result" if decision == "approve" else action,
        )
        _event(
            task_id,
            "agent_output_recorded",
            {"phase": column.status_key, "agent": agent, "artifact": column.output_artifact or "review_bundle", "session_id": output["session_id"]},
        )
        moved = apply_workflow_action(task_id, action, {"phase": column.status_key, "reason": review_bundle["summary"], "session_id": output["session_id"]})
        _event(task_id, "workflow_column_completed", {"status_key": column.status_key, "agent": agent, "decision": action})
        return ColumnResult(action=action, target_status=(moved.get("task") or {}).get("status_key"), decision=decision)


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
                "If external evidence is required and available tools are described in context, return "
                "decision='need_client_tool' with tool_requests. JSON shape: "
                "{phase, agent, summary, outputs, warnings, decision, next_action, tool_requests}. "
                "decision must be approve, fail, request_rework, request_replan, request_recoding, "
                "or need_client_tool."
            ),
        },
        {"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)},
    ]


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
    if value in {"request_recoding", "recoding", "request_rewrite", "rewrite", "request_rework"}:
        return "request_recoding" if "coding" in value else "request_rework"
    if action in {"fail", "request_replan", "request_recoding"}:
        return action
    return "approve"


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
    if decision in {"request_replan", "request_recoding", "request_rework"}:
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
    if "done" in column.transition_to and _is_valid_column_action(definition, column, "workflow_done"):
        return "workflow_done"
    return None


def _is_valid_column_action(definition: WorkflowDefinition, column: WorkflowColumn, action: str) -> bool:
    target = _action_target(definition, action)
    if not target:
        return False
    if action in {"fail", "retry", "abandon"}:
        return True
    return target == column.status_key or target in set(column.transition_to or [])


def _generic_done_response(
    task_id: str,
    project_id: str,
    status_key: str,
    state: WorkflowRunState,
) -> IdeChatResponse:
    latest = state.phase_outputs[-1] if state.phase_outputs else {}
    summary = str(latest.get("summary") or "Workflow completed.")
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
        "project_memory": _compact_project_memory(read_project_memory(project_id)),
        "workspace": _workspace_summary(body.get("workspace")),
    }


def _coding_phase_messages(
    messages: list[dict[str, Any]],
    plan_response: PlanResponse,
    review_feedback: dict[str, Any] | None = None,
    *,
    previous_revision: IdeChatResponse | None = None,
    phase: str = "coding",
) -> list[dict[str, str]]:
    context = {
        "phase": phase,
        "planner_output": {
            "summary": plan_response.summary,
            "warnings": plan_response.warnings,
            "files": [
                {
                    "path": file.path,
                    "nature": file.nature,
                    "description": file.description,
                    "confidence": file.confidence,
                    "intent": file.intent,
                    "required": file.required,
                }
                for file in plan_response.files
            ],
        },
        "review_feedback": _compact_review_feedback(review_feedback),
        "verification_feedback": _compact_verification_feedback(review_feedback),
        "previous_revision": _compact_previous_revision(previous_revision),
        "rules": [
            "Treat intent=create/modify/delete paths as approved candidates; inspect paths are read-only evidence.",
            "A candidate path is not a requirement to edit it unless required=true.",
            "For nature=deleted, emit a delete_path operation when the file should be removed.",
            "If review_feedback is present, continue from previous_revision and address semantic defects or unplanned_changed_files before returning done=true.",
            "If verification_feedback is present, fix the reported compile/test/tool errors before returning done=true.",
            "If review_feedback.client_feedback.kind is apply_failed, do not return patch_ops again. Read each target file and return complete update_file/create_file ops so the client can apply the revision deterministically.",
            "If a required file is not actually needed anymore, explain why in reply and avoid inventing unrelated paths.",
        ],
    }
    return list(messages or []) + [
        {
            "role": "user",
            "content": "workflow_phase_context:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        }
    ]


def _compact_previous_revision(response: IdeChatResponse | None) -> dict[str, Any] | None:
    if response is None:
        return None
    return {
        "reply": response.reply,
        "done": response.done,
        "ops": [op.model_dump() for op in response.ops],
        "patch_ops": [op.model_dump() for op in response.patch_ops],
        "tool_requests": [request.model_dump() for request in response.tool_requests],
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
    for item in value:
        if not isinstance(item, dict) or str(item.get("tool") or "").strip() not in allowed:
            continue
        try:
            request = ToolRequest.model_validate(item)
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


def _compact_review_feedback(review_feedback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(review_feedback, dict):
        return None
    return {
        "decision": review_feedback.get("decision"),
        "summary": review_feedback.get("summary"),
        "missing_changed_files": review_feedback.get("missing_changed_files") or [],
        "required_missing_files": review_feedback.get("required_missing_files") or [],
        "unplanned_changed_files": review_feedback.get("unplanned_changed_files") or [],
        "normalized_plan_files": review_feedback.get("normalized_plan_files") or [],
        "normalized_changed_files": review_feedback.get("normalized_changed_files") or [],
        "client_feedback": review_feedback.get("client_feedback"),
    }


def _compact_verification_feedback(review_feedback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(review_feedback, dict):
        return None
    verification = review_feedback.get("verification")
    if not isinstance(verification, dict):
        return None
    return {
        "summary": review_feedback.get("summary"),
        "required": verification.get("required") or [],
        "results": verification.get("results") or {},
        "tool_results": verification.get("tool_results") or [],
        "applied_changed_paths": review_feedback.get("applied_changed_paths") or [],
    }


def _verification_resume_feedback(body: dict[str, Any]) -> dict[str, Any] | None:
    client_feedback = body.get("client_feedback")
    if isinstance(client_feedback, dict):
        return {
            "decision": "request_recoding",
            "summary": str(client_feedback.get("summary") or "Client failed to apply the generated changes."),
            "client_feedback": client_feedback,
        }
    verification = body.get("verification_feedback")
    if not isinstance(verification, dict):
        return None
    return {
        "decision": "request_recoding",
        "summary": verification_feedback_summary(verification),
        "verification": verification,
        "applied_changed_paths": verification.get("applied_changed_paths") or [],
    }


def _task_requires_executable_verification(task: object) -> bool:
    if not isinstance(task, dict):
        return False
    text = " ".join(str(task.get(key) or "") for key in ("title", "description")).lower()
    terms = (
        "compile",
        "compilation",
        "build error",
        "test failure",
        "tests failing",
        "typecheck",
        "lint error",
        "编译",
        "构建失败",
        "测试失败",
        "类型检查",
    )
    return any(term in text for term in terms)


def _latest_plan_response(task_id: str) -> PlanResponse | None:
    payload = _latest_task_artifact_payload(task_id, "plan_bundle")
    if not payload:
        return None
    try:
        return PlanResponse.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow resume skipped invalid plan_bundle task_id=%s error=%s", task_id, exc)
        return None


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


def _ready_response(task_id: str, project_id: str, status_key: str, execute_response: IdeChatResponse) -> IdeChatResponse:
    response = execute_response.model_copy(deep=True)
    response.task_id = task_id
    response.status_key = status_key or "ready_to_apply"
    response.next_action = "apply_result"
    verification_requests = _ensure_post_apply_verification_requests(response, project_id)
    if verification_requests:
        response.tool_requests = verification_requests
    return response


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


def _review_result(
    plan_response: PlanResponse,
    execute_response: IdeChatResponse,
    *,
    prior_changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    writable_files = [file for file in plan_response.files if file.intent != "inspect"]
    planned_paths = {_normalize_review_path(file.path) for file in writable_files if file.path}
    required_paths = {_normalize_review_path(file.path) for file in writable_files if file.path and file.required}
    changed_paths = {
        _normalize_review_path(path)
        for path in ([op.path for op in execute_response.ops] + _patch_paths(execute_response))
        if path
    }
    completed_paths = changed_paths | {
        _normalize_review_path(path) for path in list(prior_changed_paths or []) if path
    }
    missing_changed_files = sorted(planned_paths - completed_paths)
    required_missing_files = sorted(required_paths - completed_paths)
    unplanned_changed_files = sorted(changed_paths - planned_paths)

    if execute_response.tool_requests and not changed_paths:
        decision = "request_recoding"
    elif not execute_response.done:
        decision = "request_recoding"
    elif not changed_paths:
        decision = "request_recoding"
    elif planned_paths and unplanned_changed_files:
        decision = "request_replan"
    else:
        decision = "approve"

    return {
        "decision": decision,
        "normalized_plan_files": sorted(planned_paths),
        "normalized_changed_files": sorted(changed_paths),
        "missing_changed_files": missing_changed_files,
        "required_missing_files": required_missing_files,
        "unplanned_changed_files": unplanned_changed_files,
    }


def _review_summary(decision: str, review_result: dict[str, Any]) -> str:
    if decision == "approve":
        return "Reviewer approved generated changes for snapshot-protected apply."
    if decision == "request_recoding":
        return "Reviewer requested recoding because no complete changed-file result was produced."
    unplanned = review_result.get("unplanned_changed_files") or []
    if unplanned:
        return f"Reviewer requested replan because generated files were outside the normalized plan: {unplanned[:8]}"
    return "Reviewer requested rework."


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


def _context_log_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": context.get("phase"),
        "agent": context.get("agent"),
        "artifact_keys": sorted((context.get("previous_artifacts") or {}).keys()),
        "event_count": len(context.get("task_events") or []),
        "project_memory_keys": sorted((context.get("project_memory") or {}).keys()),
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
) -> WorkflowColumn | None:
    status = str(status_key or "").strip().lower()
    columns = sorted(definition.columns, key=lambda col: col.position)
    anchor = next((col for col in columns if col.status_key == status), None)
    if anchor is None:
        return None
    minimum = anchor.position + 1 if anchor.status_key == current.status_key else anchor.position
    return next((col for col in columns if col.executable and col.position >= minimum), None)


def _resume_column(definition: WorkflowDefinition, state: WorkflowRunState) -> WorkflowColumn | None:
    if state.plan_response is not None:
        return _first_column_by_contract(definition, "code_change_bundle")
    return _first_column_by_contract(definition, "plan_bundle")


def _first_column_by_contract(definition: WorkflowDefinition, contract: str) -> WorkflowColumn | None:
    catalog = default_agent_catalog()
    for column in _executable_columns(definition):
        if _column_contract(column, JobScheduler(catalog)) == contract:
            return column
    return None


def _column_for_contract(
    definition: WorkflowDefinition,
    scheduler: JobScheduler | None,
    contract: str,
) -> WorkflowColumn | None:
    return next(
        (column for column in _executable_columns(definition) if _column_contract(column, scheduler) == contract),
        None,
    )


def _column_contract(column: WorkflowColumn, scheduler: JobScheduler | None) -> str:
    if scheduler is None or not column.job_template:
        return ""
    try:
        return scheduler.catalog.job(column.job_template).output_contract
    except KeyError:
        return ""


def _requires_plan_confirmation(body: dict[str, Any]) -> bool:
    if str(body.get("resume_action") or "").strip().lower() == "confirm_plan":
        return False
    return str(body.get("interaction_mode") or "auto").strip().lower() in {"confirm_plan", "interactive"}


def _plan_confirmation_text(plan: PlanResponse) -> str:
    paths = [file.path for file in plan.files if file.intent != "inspect"]
    lines = [plan.summary or "The implementation plan is ready."]
    if paths:
        lines.append("\nPlanned change candidates:")
        lines.extend(f"- {path}" for path in paths[:16])
        if len(paths) > 16:
            lines.append(f"- ... and {len(paths) - 16} more")
    if plan.warnings:
        lines.append("\nOpen questions / missing evidence:")
        lines.extend(f"- {warning}" for warning in plan.warnings[:8])
    lines.append("\nWaiting for plan confirmation. Confirm to start coding, or send corrections to revise the plan.")
    return "\n".join(lines)


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
