from __future__ import annotations

import logging
import uuid
from typing import Any

from app.services.kanban import add_artifact, add_event, get_project_workflow, get_task, move_task
from app.services.session_store import record_phase_memory
from app.services.verification_policy import (
    verification_failed,
    verification_has_policy,
    verification_feedback_summary,
)
from app.services.workflow_definition import WorkflowDefinition, workflow_from_dict

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
ACTION_APPLY_SUCCEEDED = "apply_succeeded"
ACTION_VERIFICATION_PASSED = "verification_passed"
ACTION_VERIFICATION_FAILED = "verification_failed"
ACTION_WORKFLOW_DONE = "workflow_done"

CLIENT_VISIBLE_ACTIONS = {ACTION_RETRY, ACTION_ABANDON}


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

    if _workflow_has_action(task_id, action_key):
        return _transition_by_definition(task_id, action_key, data)

    raise ValueError(f"unknown workflow action: {action}")


def _workflow_has_action(task_id: str, action: str) -> bool:
    return _definition_for_task(task_id).action(action) is not None


def _transition_by_definition(task_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    current_status = str(task.get("status_key") or "")
    definition = _definition_from_task_record(task)
    action_rule = definition.action(action)
    if action_rule is None:
        raise ValueError(f"unknown workflow action for project workflow: {action}")

    to_status = str(action_rule.get("to") or "").strip().lower()
    if not to_status:
        raise ValueError(f"workflow action {action!r} has no target status")
    current_column = definition.column(current_status)
    if current_column is not None and not _target_allowed(current_column, to_status):
        raise ValueError(
            f"workflow action {action!r} cannot move from {current_status!r} to {to_status!r}"
        )

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
    definition = _definition_for_task(task_id)
    add_artifact(task_id, artifact_type="apply_result", payload=payload)
    add_event(task_id, "apply_result_received", payload)

    if not ok:
        status_key = _action_target(definition, ACTION_REQUEST_RECODING) or "coding"
        record_phase_output(
            task_id,
            phase="apply",
            agent="plugin",
            status_key=status_key,
            summary=str(payload.get("error_message") or "Plugin failed to apply generated changes."),
            outputs={
                "ok": False,
                "snapshot_id": payload.get("snapshot_id"),
                "changed_paths": payload.get("changed_paths") or [],
                "verification": payload.get("verification") or {},
            },
            warnings=[str(payload.get("error_message") or "apply failed")],
            next_action=ACTION_REQUEST_RECODING,
        )
        add_event(
            task_id,
            "apply_failed",
            {"phase": "apply", "reason": payload.get("error_message") or "Client failed to apply generated changes."},
        )
        return _apply_configured_action(task_id, definition, ACTION_REQUEST_RECODING, {"phase": "apply", **payload})

    verification = payload.get("verification")
    has_verification_policy = verification_has_policy(verification)
    passed = not verification_failed(verification) if has_verification_policy else True
    status_key = (
        _first_action_target(definition, [ACTION_WORKFLOW_DONE, ACTION_VERIFICATION_PASSED, ACTION_APPLY_SUCCEEDED])
        if passed
        else _first_action_target(definition, [ACTION_VERIFICATION_FAILED, ACTION_FAIL])
    )
    record_phase_output(
        task_id,
        phase="apply",
        agent="plugin",
        status_key=status_key or ("done" if passed else "failed"),
        summary="Plugin applied generated changes through the snapshot-protected path.",
        outputs={
            "ok": True,
            "snapshot_id": payload.get("snapshot_id"),
            "changed_paths": payload.get("changed_paths") or [],
            "verification": verification or {},
        },
        warnings=[] if passed else ["Verification requirements did not pass."],
        next_action=None if passed else ACTION_REQUEST_RECODING,
    )

    latest = _apply_configured_action(task_id, definition, ACTION_APPLY_SUCCEEDED, {"phase": "apply", **payload})
    if passed:
        verified = _apply_optional_action(
            task_id,
            definition,
            ACTION_VERIFICATION_PASSED,
            {"phase": "verify", "verification": verification or {}},
        )
        if verified is not None:
            latest = verified
        reason = "verification_passed" if has_verification_policy else "apply_completed_without_verification_policy"
        done = _apply_optional_action(task_id, definition, ACTION_WORKFLOW_DONE, {"phase": "workflow", "reason": reason})
        return done or latest

    reason = (
        verification_feedback_summary(verification)
        if has_verification_policy
        else "No verification policy was provided by the generated workflow result."
    )
    add_event(
        task_id,
        "verification_failed",
        {
            "phase": "verify",
            "reason": reason,
            "verification": verification or {},
            "can_resume": has_verification_policy,
        },
    )
    failed = _apply_optional_action(
        task_id,
        definition,
        ACTION_VERIFICATION_FAILED,
        {"phase": "verify", "reason": reason, **payload},
    )
    if failed is not None:
        return failed
    return _apply_configured_action(task_id, definition, ACTION_FAIL, {"phase": "verify", "reason": reason, **payload})


def current_workflow_state(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    task_record = task.get("task", {}) or {}
    status = task_record.get("status_key")
    definition = _definition_from_task_record(task_record)
    return {
        "ok": True,
        "task_id": task_id,
        "status_key": status,
        "task": task.get("task"),
        "actions": available_actions_for_status(str(status or ""), definition),
    }


def available_actions_for_status(status_key: str, definition: WorkflowDefinition | None = None) -> list[str]:
    definition = definition or workflow_from_dict({})
    status = str(status_key or "").strip().lower()
    column = definition.column(status)
    if column is None:
        return []

    actions: list[str] = []
    if _can_apply_result_from(definition, column):
        actions.append(ACTION_APPLY_RESULT)

    for action, rule in definition.actions.items():
        if not _is_client_visible_action(action, rule):
            continue
        target = _rule_target(rule)
        if target and _target_allowed(column, target):
            actions.append(action)
    return _dedupe(actions)


def _definition_for_task(task_id: str) -> WorkflowDefinition:
    task_detail = get_task(task_id)
    return _definition_from_task_record(task_detail.get("task") or {})


def _definition_from_task_record(task: dict[str, Any]) -> WorkflowDefinition:
    project_id = str(task.get("project_id") or "default")
    workflow_payload = get_project_workflow(project_id).get("workflow") or {}
    return workflow_from_dict(workflow_payload)


def _apply_configured_action(
    task_id: str,
    definition: WorkflowDefinition,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if definition.action(action) is None:
        raise ValueError(f"project workflow does not define required action: {action}")
    return _transition_by_definition(task_id, action, payload)


def _action_target(definition: WorkflowDefinition, action: str) -> str | None:
    rule = definition.action(action)
    if rule is None:
        return None
    return _rule_target(rule)


def _first_action_target(definition: WorkflowDefinition, actions: list[str]) -> str | None:
    for action in actions:
        target = _action_target(definition, action)
        if target:
            return target
    return None


def _apply_optional_action(
    task_id: str,
    definition: WorkflowDefinition,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if definition.action(action) is None:
        return None
    return _transition_by_definition(task_id, action, payload)


def _rule_target(rule: dict[str, Any]) -> str | None:
    target = str(rule.get("to") or "").strip().lower()
    return target or None


def _can_apply_result_from(definition: WorkflowDefinition, column: Any) -> bool:
    target = _action_target(definition, ACTION_APPLY_SUCCEEDED)
    return bool(target and _target_allowed(column, target))


def _is_client_visible_action(action: str, rule: dict[str, Any]) -> bool:
    return bool(rule.get("client_visible")) or action in CLIENT_VISIBLE_ACTIONS


def _target_allowed(column: Any, target: str) -> bool:
    allowed = set(column.transition_to or [])
    return target == column.status_key or target in allowed


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
