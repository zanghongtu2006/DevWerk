from __future__ import annotations

import logging
import uuid
from typing import Any

from app.services.kanban import add_artifact, add_event, get_project_workflow, get_task, move_task
from app.services.session_store import record_phase_memory
from app.services.workflow_definition import workflow_from_dict

_log = logging.getLogger("devwerk.workflow")


ACTION_CONTEXT_INDEXED = "context_indexed"
ACTION_PLAN_READY = "plan_ready"
ACTION_PLAN_FAILED = "plan_failed"
ACTION_CODING_STARTED = "coding_started"
ACTION_CODING_READY = "coding_ready"
ACTION_READY_TO_APPLY = "ready_to_apply"
ACTION_CODING_FAILED = "coding_failed"
ACTION_REQUEST_RECODING = "request_recoding"
ACTION_REQUEST_REPLAN = "request_replan"
ACTION_APPROVE = "approve"
ACTION_FAIL = "fail"
ACTION_NEED_CLIENT_TOOL = "need_client_tool"
ACTION_APPLY_RESULT = "apply_result"
ACTION_RETRY = "retry"
ACTION_ABANDON = "abandon"


def record_phase_output(
    task_id: str,
    *,
    phase: str,
    agent: str,
    status_key: str,
    summary: str,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    session_id: str | None = None,
    next_action: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """
    Persist the stable output contract for one workflow phase.

    Today one configured LLM profile may execute every phase. Future planner,
    coder, and tester agents can keep this same artifact shape while owning
    their own session context.
    """
    sid = session_id or f"{phase}-{uuid.uuid4()}"
    payload = {
        "session_id": sid,
        "phase": phase,
        "agent": agent,
        "status_key": status_key,
        "summary": summary or "",
        "inputs": inputs or {},
        "outputs": outputs or {},
        "warnings": warnings or [],
        "decision": decision,
        "next_action": next_action,
    }
    _log.debug(
        "workflow phase output task_id=%s phase=%s agent=%s status=%s session_id=%s next_action=%s summary=%s",
        task_id,
        phase,
        agent,
        status_key,
        sid,
        next_action,
        summary,
    )
    add_artifact(task_id, artifact_type="workflow_phase_output", payload=payload)
    add_event(
        task_id,
        "workflow_phase_output_recorded",
        {
            "session_id": sid,
            "phase": phase,
            "agent": agent,
            "status_key": status_key,
            "decision": decision,
            "next_action": next_action,
        },
    )
    try:
        task = get_task(task_id).get("task") or {}
        project_id = str(task.get("project_id") or "default")
        record_phase_memory(project_id=project_id, task_id=task_id, phase_output=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow phase memory skipped task_id=%s phase=%s error=%s", task_id, phase, exc)
    return payload


def apply_workflow_action(task_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Apply a semantic workflow action to a kanban task.

    Clients should report actions like "apply_result" or "abandon", not columns.
    This function is the backend-owned state machine boundary: future workflow
    changes should update this mapping while keeping the action protocol stable.
    """
    action_key = str(action or "").strip().lower().replace("-", "_")
    data = dict(payload or {})
    _log.debug("workflow action task_id=%s action=%s payload=%s", task_id, action_key, data)

    if action_key == ACTION_APPLY_RESULT:
        return _apply_result(task_id, data)

    semantic = {
        ACTION_CONTEXT_INDEXED,
        ACTION_PLAN_READY,
        ACTION_PLAN_FAILED,
        ACTION_CODING_STARTED,
        ACTION_CODING_READY,
        ACTION_READY_TO_APPLY,
        ACTION_CODING_FAILED,
        ACTION_REQUEST_RECODING,
        ACTION_REQUEST_REPLAN,
        ACTION_APPROVE,
        ACTION_FAIL,
        ACTION_NEED_CLIENT_TOOL,
        ACTION_RETRY,
        ACTION_ABANDON,
    }
    if action_key in semantic:
        return _transition_by_definition(task_id, action_key, data)

    raise ValueError(f"unknown workflow action: {action}")


def _transition_by_definition(task_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    project_id = str(task.get("project_id") or "default")
    current_status = str(task.get("status_key") or "")
    workflow_payload = get_project_workflow(project_id).get("workflow") or {}
    definition = workflow_from_dict(workflow_payload)
    action_rule = definition.action(action)
    if action_rule is None:
        raise ValueError(f"unknown workflow action for project workflow: {action}")

    to_status = str(action_rule.get("to") or "").strip().lower()
    if not to_status:
        raise ValueError(f"workflow action {action!r} has no target status")

    data = dict(payload or {})
    data.setdefault("action", action)
    add_event(
        task_id,
        "workflow_transition_decided",
        {
            "action": action,
            "from_status": current_status,
            "to_status": to_status,
            "reason": data.get("reason"),
            "phase": data.get("phase"),
        },
    )
    if action == ACTION_RETRY:
        add_event(task_id, "manual_retry_requested", data or {"reason": "user_requested_retry"})
    if action == ACTION_ABANDON:
        add_event(task_id, "manual_abandon_requested", data or {"reason": "user_abandoned_task"})
    if action in {ACTION_REQUEST_RECODING, ACTION_REQUEST_REPLAN}:
        add_event(task_id, "workflow_rework_requested", data)
    else:
        add_event(task_id, f"workflow_{action}", data)
    return move_task(task_id, to_status, force=True, payload=data)


def _apply_result(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok = bool(payload.get("ok", True))
    add_artifact(task_id, artifact_type="apply_result", payload=payload)
    add_event(task_id, "apply_result_received", payload)

    if not ok:
        record_phase_output(
            task_id,
            phase="apply",
            agent="plugin",
            status_key="failed",
            summary=str(payload.get("error_message") or "Plugin failed to apply generated changes."),
            outputs={
                "ok": False,
                "snapshot_id": payload.get("snapshot_id"),
                "changed_paths": payload.get("changed_paths") or [],
                "verification": payload.get("verification") or {},
            },
            warnings=[str(payload.get("error_message") or "apply failed")],
            next_action=ACTION_RETRY,
        )
        return move_task(task_id, "failed", force=True, payload={"phase": "apply", **payload})

    verification = payload.get("verification")
    required = verification.get("required") if isinstance(verification, dict) else None
    results = verification.get("results") if isinstance(verification, dict) else None
    has_verification_policy = isinstance(required, list) and isinstance(results, dict)
    passed = all(str(results.get(item)).lower() == "passed" for item in required) if has_verification_policy else True
    auto_done = passed
    record_phase_output(
        task_id,
        phase="apply",
        agent="plugin",
        status_key="done" if auto_done else "failed",
        summary="Plugin applied generated changes through the snapshot-protected path.",
        outputs={
            "ok": True,
            "snapshot_id": payload.get("snapshot_id"),
            "changed_paths": payload.get("changed_paths") or [],
            "verification": verification or {},
        },
        warnings=[] if auto_done else ["Verification requirements did not pass."],
        next_action=None if auto_done else ACTION_RETRY,
    )

    applied = move_task(task_id, "applied", force=True, payload=payload)
    if auto_done:
        move_task(task_id, "verified", force=True, payload={"verification": verification or {}})
        reason = "verification_passed" if has_verification_policy else "apply_completed_without_verification_policy"
        return move_task(task_id, "done", force=True, payload={"reason": reason})
    return move_task(task_id, "failed", force=True, payload={"phase": "verify", **payload})


def current_workflow_state(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    status = task.get("task", {}).get("status_key")
    return {
        "ok": True,
        "task_id": task_id,
        "status_key": status,
        "task": task.get("task"),
        "actions": available_actions_for_status(str(status or "")),
    }


def available_actions_for_status(status_key: str) -> list[str]:
    status = str(status_key or "").strip().lower()
    if status == "failed":
        return [ACTION_RETRY, ACTION_ABANDON]
    if status == "ready_to_apply":
        return [ACTION_APPLY_RESULT, ACTION_ABANDON]
    if status == "reviewed":
        return [ACTION_APPROVE, ACTION_REQUEST_RECODING, ACTION_REQUEST_REPLAN, ACTION_ABANDON]
    if status in {"planned", "coding", "ready_to_apply", "applied", "verified"}:
        return [ACTION_ABANDON]
    if status in {"done"}:
        return []
    return [ACTION_ABANDON]
