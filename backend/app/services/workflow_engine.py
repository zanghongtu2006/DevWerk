from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.models.ide import IdeChatResponse
from app.models.plan import PlanResponse
from app.services.kanban import add_artifact, add_event, get_project_workflow, get_task
from app.services.session_store import read_project_memory
from app.services.workflow import apply_workflow_action, record_phase_output
from app.services.workflow_definition import workflow_from_dict

_log = logging.getLogger("devwerk.workflow_engine")

PlanRunner = Callable[[dict[str, Any]], Awaitable[PlanResponse]]
CodingRunner = Callable[[dict[str, Any]], Awaitable[IdeChatResponse]]


class WorkflowEngine:
    def __init__(self, *, plan_runner: PlanRunner, coding_runner: CodingRunner):
        self.plan_runner = plan_runner
        self.coding_runner = coding_runner

    async def run(self, task_id: str, body: dict[str, Any]) -> None:
        project_id = str(body.get("project_id") or "default")
        definition = workflow_from_dict(get_project_workflow(project_id).get("workflow") or {})
        workflow_summary = definition.summary()
        _event(task_id, "workflow_started", {"entrypoint": "/v1/workflows", "workflow": workflow_summary})

        context_bundle = self._run_context_column(task_id, body, workflow_summary)
        plan_response: PlanResponse | None = None
        execute_response: IdeChatResponse | None = None
        next_phase = "planned"
        max_rework_rounds = 3

        for round_no in range(1, max_rework_rounds + 2):
            _log.debug("workflow loop task_id=%s round=%s next_phase=%s", task_id, round_no, next_phase)
            _event(task_id, "workflow_round_started", {"round": round_no, "next_phase": next_phase})

            if next_phase == "planned" or plan_response is None:
                plan_response = await self._run_plan_column(task_id, body, workflow_summary, context_bundle)
                if not plan_response.ok:
                    response = IdeChatResponse(
                        ok=False,
                        reply="",
                        done=True,
                        task_id=task_id,
                        status_key=plan_response.status_key or "failed",
                        error_code=plan_response.error_code or "PLAN_ERROR",
                        error_message=plan_response.error_message or "Planning failed.",
                        retryable=True,
                    )
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    _event(task_id, "workflow_finished", {"ok": False, "phase": "planned", "status_key": response.status_key})
                    return

                if not plan_response.files:
                    response = IdeChatResponse(
                        ok=False,
                        reply="",
                        done=True,
                        task_id=task_id,
                        status_key="failed",
                        error_code="EMPTY_PLAN",
                        error_message="Planner produced no files for a coding workflow.",
                        retryable=True,
                    )
                    add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
                    apply_workflow_action(task_id, "fail", {"phase": "planned", "reason": response.error_message})
                    _event(task_id, "workflow_finished", {"ok": False, "phase": "planned", "status_key": "failed"})
                    return

            approved_paths = [file.path for file in plan_response.files]
            execute_response = await self._run_coding_column(task_id, body, workflow_summary, approved_paths)
            if not execute_response.ok:
                add_artifact(task_id, artifact_type="workflow_result", payload=execute_response.model_dump())
                apply_workflow_action(task_id, "fail", {"phase": "coding", "reason": execute_response.error_message})
                _event(task_id, "workflow_finished", {"ok": False, "phase": "coding", "status_key": "failed"})
                return

            review = self._run_review_column(task_id, body, workflow_summary, plan_response, execute_response)
            decision = str(review.get("decision") or "fail")
            if decision == "approve":
                execute_response.task_id = task_id
                execute_response.status_key = "ready_to_apply"
                add_artifact(task_id, artifact_type="workflow_result", payload=execute_response.model_dump())
                _event(
                    task_id,
                    "workflow_finished",
                    {
                        "ok": execute_response.ok,
                        "phase": "reviewed",
                        "status_key": execute_response.status_key,
                        "ops": len(execute_response.ops),
                        "patch_ops": len(execute_response.patch_ops),
                        "tool_requests": len(execute_response.tool_requests),
                        "round": round_no,
                    },
                )
                return

            if decision in {"request_replan", "request_recoding"} and round_no <= max_rework_rounds:
                next_phase = "planned" if decision == "request_replan" else "coding"
                _event(
                    task_id,
                    "workflow_rework_loop",
                    {"round": round_no, "decision": decision, "next_phase": next_phase, "review": review},
                )
                continue

            response = execute_response.model_copy(update={
                "ok": False,
                "done": True,
                "status_key": "failed",
                "error_code": "REVIEW_REWORK_LIMIT",
                "error_message": str(review.get("summary") or "Reviewer requested rework too many times."),
                "retryable": True,
            })
            add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
            apply_workflow_action(task_id, "fail", {"phase": "reviewed", "reason": response.error_message, "review": review})
            _event(task_id, "workflow_finished", {"ok": False, "phase": "reviewed", "status_key": "failed", "round": round_no})
            return

        response = IdeChatResponse(
            ok=False,
            reply="",
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="WORKFLOW_LOOP_EXHAUSTED",
            error_message="Workflow exhausted without producing a result.",
            retryable=True,
        )
        add_artifact(task_id, artifact_type="workflow_result", payload=response.model_dump())
        apply_workflow_action(task_id, "fail", {"phase": "workflow", "reason": response.error_message})
        _event(task_id, "workflow_finished", {"ok": False, "phase": "workflow", "status_key": "failed"})

    def _run_context_column(self, task_id: str, body: dict[str, Any], workflow_summary: dict[str, Any]) -> dict[str, Any]:
        _event(task_id, "workflow_column_started", {"status_key": "context_indexed", "agent": "context"})
        context = _build_agent_context(task_id, "context_indexed", "context", body, workflow_summary, [])
        context_bundle = {
            "workspace": body.get("workspace"),
            "workspace_summary": _workspace_summary(body.get("workspace")),
            "project_root": body.get("project_root"),
            "project_memory": context["project_memory"],
        }
        add_artifact(task_id, artifact_type="context_bundle", payload=context_bundle)
        output = record_phase_output(
            task_id,
            phase="context_indexed",
            agent="context",
            status_key="context_indexed",
            summary="Indexed IDE-provided workspace context and project memory for downstream agents.",
            inputs=context,
            outputs=context_bundle,
            warnings=[],
            decision="approve",
            next_action="plan_ready",
        )
        _event(task_id, "agent_context_built", {"phase": "context_indexed", "agent": "context", "session_id": output["session_id"]})
        _event(task_id, "agent_output_recorded", {"phase": "context_indexed", "agent": "context", "artifact": "context_bundle"})
        apply_workflow_action(task_id, "context_indexed", {"phase": "context_indexed", "session_id": output["session_id"]})
        _event(task_id, "workflow_column_completed", {"status_key": "context_indexed", "agent": "context", "decision": "approve"})
        return context_bundle

    async def _run_plan_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        context_bundle: dict[str, Any],
    ) -> PlanResponse:
        _event(task_id, "workflow_column_started", {"status_key": "planned", "agent": "planner"})
        _event(task_id, "agent_context_built", {"phase": "planned", "agent": "planner", "context": _context_log_summary(_build_agent_context(task_id, "planned", "planner", body, workflow_summary, ["context_bundle"]))})
        plan_response = await self.plan_runner(dict(body, task_id=task_id))
        plan_response.task_id = task_id
        add_artifact(task_id, artifact_type="plan_bundle", payload=plan_response.model_dump())
        _event(task_id, "agent_output_recorded", {"phase": "planned", "agent": "planner", "artifact": "plan_bundle"})
        if plan_response.ok:
            apply_workflow_action(task_id, "plan_ready", {"phase": "planned", "files": len(plan_response.files), "session_id": plan_response.session_id})
            _event(task_id, "workflow_column_completed", {"status_key": "planned", "agent": "planner", "decision": "approve"})
        else:
            apply_workflow_action(task_id, "fail", {"phase": "planned", "reason": plan_response.error_message})
            _event(task_id, "workflow_column_completed", {"status_key": "planned", "agent": "planner", "decision": "fail"})
        return plan_response

    async def _run_coding_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        approved_paths: list[str],
    ) -> IdeChatResponse:
        apply_workflow_action(task_id, "coding_started", {"phase": "coding", "approved_paths": approved_paths})
        _event(task_id, "workflow_column_started", {"status_key": "coding", "agent": "coder"})
        _event(task_id, "agent_context_built", {"phase": "coding", "agent": "coder", "context": _context_log_summary(_build_agent_context(task_id, "coding", "coder", body, workflow_summary, ["context_bundle", "plan_bundle"]))})
        execute_body = {
            "project_id": body.get("project_id"),
            "task_id": task_id,
            "mode": body.get("mode", "agent"),
            "project_root": body.get("project_root"),
            "messages": body.get("messages") or [],
            "workspace": body.get("workspace"),
            "approved_paths": approved_paths,
            "approved_ops": [],
        }
        execute_response = await self.coding_runner(execute_body)
        execute_response.task_id = task_id
        add_artifact(task_id, artifact_type="code_change_bundle", payload=execute_response.model_dump())
        _event(task_id, "agent_output_recorded", {"phase": "coding", "agent": "coder", "artifact": "code_change_bundle"})
        if execute_response.ok:
            apply_workflow_action(task_id, "coding_ready", {"phase": "coding", "ops": len(execute_response.ops), "patch_ops": len(execute_response.patch_ops), "session_id": execute_response.session_id})
            _event(task_id, "workflow_column_completed", {"status_key": "coding", "agent": "coder", "decision": "approve"})
        else:
            apply_workflow_action(task_id, "fail", {"phase": "coding", "reason": execute_response.error_message})
            _event(task_id, "workflow_column_completed", {"status_key": "coding", "agent": "coder", "decision": "fail"})
        return execute_response

    def _run_review_column(
        self,
        task_id: str,
        body: dict[str, Any],
        workflow_summary: dict[str, Any],
        plan_response: PlanResponse,
        execute_response: IdeChatResponse,
    ) -> dict[str, Any]:
        _event(task_id, "workflow_column_started", {"status_key": "reviewed", "agent": "reviewer"})
        context = _build_agent_context(task_id, "reviewed", "reviewer", body, workflow_summary, ["plan_bundle", "code_change_bundle"])
        _event(task_id, "agent_context_built", {"phase": "reviewed", "agent": "reviewer", "context": _context_log_summary(context)})
        review_result = _review_result(plan_response, execute_response)
        decision = review_result["decision"]
        review_bundle = {
            "decision": decision,
            "summary": _review_summary(decision, review_result),
            "plan_files": [file.path for file in plan_response.files],
            "changed_files": [op.path for op in execute_response.ops] + _patch_paths(execute_response),
            "normalized_plan_files": review_result["normalized_plan_files"],
            "normalized_changed_files": review_result["normalized_changed_files"],
            "missing_changed_files": review_result["missing_changed_files"],
            "unplanned_changed_files": review_result["unplanned_changed_files"],
            "ops": len(execute_response.ops),
            "patch_ops": len(execute_response.patch_ops),
            "tool_requests": len(execute_response.tool_requests),
        }
        _log.debug(
            "workflow review decision task_id=%s decision=%s plan=%s changed=%s missing=%s unplanned=%s",
            task_id,
            decision,
            review_result["normalized_plan_files"],
            review_result["normalized_changed_files"],
            review_result["missing_changed_files"],
            review_result["unplanned_changed_files"],
        )
        add_artifact(task_id, artifact_type="review_bundle", payload=review_bundle)
        output = record_phase_output(
            task_id,
            phase="reviewed",
            agent="reviewer",
            status_key="ready_to_apply" if decision == "approve" else decision,
            summary=review_bundle["summary"],
            inputs=context,
            outputs=review_bundle,
            warnings=[],
            decision=decision,
            next_action="apply_result" if decision == "approve" else decision,
        )
        _event(task_id, "agent_output_recorded", {"phase": "reviewed", "agent": "reviewer", "artifact": "review_bundle", "session_id": output["session_id"]})
        apply_workflow_action(task_id, decision, {"phase": "reviewed", "reason": review_bundle["summary"], "session_id": output["session_id"]})
        _event(task_id, "workflow_column_completed", {"status_key": "reviewed", "agent": "reviewer", "decision": decision})
        return review_bundle


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
    return {
        "task_id": task_id,
        "project_id": project_id,
        "phase": phase,
        "agent": agent,
        "original_user_request": _first_user_text(body.get("messages") or []),
        "task": {k: task.get(k) for k in ("id", "project_id", "title", "description", "status_key", "metadata")},
        "workflow": workflow_summary,
        "previous_artifacts": _latest_artifacts(artifacts, required_artifacts),
        "task_events": _event_summary(task.get("events") or []),
        "project_memory": _compact_project_memory(read_project_memory(project_id)),
        "workspace": _workspace_summary(body.get("workspace")),
    }


def _review_decision(plan_response: PlanResponse, execute_response: IdeChatResponse) -> str:
    return _review_result(plan_response, execute_response)["decision"]


def _review_result(plan_response: PlanResponse, execute_response: IdeChatResponse) -> dict[str, Any]:
    planned_paths = {_normalize_review_path(file.path) for file in plan_response.files if file.path}
    changed_paths = {
        _normalize_review_path(path)
        for path in ([op.path for op in execute_response.ops] + _patch_paths(execute_response))
        if path
    }
    missing_changed_files = sorted(planned_paths - changed_paths)
    unplanned_changed_files = sorted(changed_paths - planned_paths)

    if execute_response.tool_requests and not changed_paths:
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
        "unplanned_changed_files": unplanned_changed_files,
    }


def _review_summary(decision: str, review_result: dict[str, Any]) -> str:
    if decision == "approve":
        return "Reviewer approved generated changes for snapshot-protected apply."
    if decision == "request_recoding":
        return "Reviewer requested recoding because no changed files were produced."
    unplanned = review_result.get("unplanned_changed_files") or []
    if unplanned:
        return f"Reviewer requested replan because generated files were outside the normalized plan: {unplanned[:8]}"
    return "Reviewer requested rework."


def _normalize_review_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")

    for marker in ("/src/", "/backend/", "/frontend/", "/idea-plugin/"):
        idx = normalized.find(marker)
        if idx > 0:
            return normalized[idx + 1:]

    root_files = (
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        "gradlew",
        "gradlew.bat",
        "README.md",
    )
    for filename in root_files:
        suffix = f"/{filename}"
        if normalized.endswith(suffix):
            return filename
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
    return {
        "present": True,
        "root_id": workspace.get("root_id"),
        "tree_preview_chars": len(str(workspace.get("tree_preview") or "")),
        "source_map_present": bool(source_map),
        "source_map_total_files": source_map.get("total_files") if source_map else None,
        "source_map_indexed_files": source_map.get("indexed_files") if source_map else None,
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


def _event(task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    add_event(task_id, event_type, payload)
