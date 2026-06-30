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
from app.services.code_context import build_code_context_summary
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
from app.services.skill_manager import resolve_agent_skills
from app.services.job_scheduler import JobScheduler
from app.services.llm_factory import get_llm_client
from app.services.provider_errors import is_retryable_llm_error
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
                _event(task_id, "workflow_finished", {"ok": False, "phase": current.status_key, "status_key": response.status_key})
                add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                return

            next_column = _next_executable_after_transition(definition, target_status, current)
            if next_column is None:
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
                "If external evidence is required and available tools are described in context, return "
                "decision='need_client_tool' with tool_requests. JSON shape: "
                "{phase, agent, summary, outputs, warnings, decision, next_action, tool_requests}. "
                "decision must be approve, fail, request_rework, request_replan, or need_client_tool."
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
    if value in {"request_rework", "request_rewrite", "rewrite", "rework"}:
        return "request_rework"
    if action in {"fail", "request_replan", "request_rework"}:
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
    ops = _model_list(FileOp, raw.get("ops") or raw_outputs.get("ops"))
    patch_ops = _model_list(PatchOp, raw.get("patch_ops") or raw_outputs.get("patch_ops"))
    code_tree = raw.get("code_tree") or raw_outputs.get("code_tree")
    if not ops and not patch_ops and not tool_requests and code_tree is None:
        return None
    return IdeChatResponse(
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
        "skills": resolve_agent_skills(project_id, skill_ids),
        "workspace": _workspace_summary(body.get("workspace")),
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
    response.status_key = status_key or "complete"
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


def _context_log_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": context.get("phase"),
        "agent": context.get("agent"),
        "artifact_keys": sorted((context.get("previous_artifacts") or {}).keys()),
        "event_count": len(context.get("task_events") or []),
        "task_memory_keys": sorted((context.get("task_memory") or {}).keys()),
        "project_memory_keys": sorted((context.get("project_memory") or {}).keys()),
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
) -> WorkflowColumn | None:
    status = str(status_key or "").strip().lower()
    columns = sorted(definition.columns, key=lambda col: col.position)
    anchor = next((col for col in columns if col.status_key == status), None)
    if anchor is None:
        return None
    minimum = anchor.position + 1 if anchor.status_key == current.status_key else anchor.position
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
