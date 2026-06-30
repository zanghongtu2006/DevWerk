from __future__ import annotations

import logging
import uuid
from typing import Any

from app.services.kanban import add_artifact, add_event, get_project_workflow, get_task, move_task
from app.services.memory_system import upsert_memory_item
from app.services.session_store import record_phase_memory
from app.services.verification_policy import (
    verification_failed,
    verification_has_policy,
    verification_feedback_summary,
)
from app.services.workflow_definition import WorkflowDefinition, workflow_from_dict

_log = logging.getLogger("devwerk.workflow")


ACTION_REQUEST_REPLAN = "request_replan"
ACTION_REQUEST_REWORK = "request_rework"
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
LIFECYCLE_ACTIONS = {ACTION_FAIL, ACTION_RETRY, ACTION_ABANDON}


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

    Each workflow column may spawn a different runtime agent, but all column
    agents persist the same phase output contract.
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

    if action_key == ACTION_WORKFLOW_DONE:
        definition = _definition_for_task(task_id)
        if definition.is_coding and not data.get("lifecycle_done_guard_passed"):
            task_detail = get_task(task_id)
            add_event(
                task_id,
                "workflow_done_guard_blocked",
                {
                    "action": action_key,
                    "reason": "coding workflow cannot enter done without apply_result and verification gate",
                    **data,
                },
            )
            task_detail["action_ignored"] = True
            task_detail["ignored_action"] = ACTION_WORKFLOW_DONE
            return task_detail

    if action_key == ACTION_RETRY:
        task_detail = get_task(task_id)
        current_status = str((task_detail.get("task") or {}).get("status_key") or "")
        if current_status not in _failure_statuses(_definition_for_task(task_id)):
            add_event(
                task_id,
                "workflow_retry_deduplicated",
                {"status_key": current_status, "reason": "retry is already active or task is not in a workflow failure terminal"},
            )
            task_detail["action_ignored"] = True
            task_detail["ignored_action"] = ACTION_RETRY
            return task_detail

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
    if definition.column(to_status) is None:
        raise ValueError(f"workflow action {action!r} targets unknown status {to_status!r}")
    if current_column is not None and not _target_allowed(current_column, to_status, action=action):
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
    if action in {ACTION_REQUEST_REWORK, ACTION_REQUEST_REPLAN}:
        add_event(task_id, "workflow_rework_requested", data)
    else:
        add_event(task_id, f"workflow_{action}", data)
    return move_task(task_id, to_status, force=True, payload=data)


def _apply_result(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok = bool(payload.get("ok", True))
    definition = _definition_for_task(task_id)
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    current_status = str(task.get("status_key") or "")
    current_column = definition.column(current_status)
    if current_column is None or not _can_apply_result_from(definition, current_column):
        add_artifact(task_id, artifact_type="stale_apply_result", payload=payload)
        add_event(
            task_id,
            "stale_apply_result_ignored",
            {
                "status_key": current_status,
                "ok": ok,
                "snapshot_id": payload.get("snapshot_id"),
                "reason": "Task is no longer waiting for a client apply result.",
            },
        )
        task_detail["action_ignored"] = True
        task_detail["ignored_action"] = ACTION_APPLY_RESULT
        return task_detail
    add_artifact(task_id, artifact_type="apply_result", payload=payload)
    add_event(task_id, "apply_result_received", payload)

    if not ok:
        rework_action = ACTION_REQUEST_REWORK if definition.action(ACTION_REQUEST_REWORK) is not None else ACTION_FAIL
        status_key = _action_target(definition, rework_action) or _action_target(definition, ACTION_FAIL) or current_status
        _create_failure_bundle(
            task_id,
            definition,
            failure_stage="apply",
            reason=str(payload.get("error_message") or "Client failed to apply generated changes."),
            retryable=True,
            payload=payload,
            target_status_key=status_key,
        )
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
            next_action=rework_action,
        )
        add_event(
            task_id,
            "apply_failed",
            {"phase": "apply", "reason": payload.get("error_message") or "Client failed to apply generated changes."},
        )
        return _apply_configured_action(task_id, definition, rework_action, {"phase": "apply", **payload})

    verification = payload.get("verification")
    has_verification_policy = verification_has_policy(verification)
    allow_skip_verification = (not definition.is_coding) or _allow_done_without_verification(definition)
    passed = not verification_failed(verification) if has_verification_policy else allow_skip_verification
    status_key = (
        _first_action_target(definition, [ACTION_WORKFLOW_DONE, ACTION_VERIFICATION_PASSED, ACTION_APPLY_SUCCEEDED])
        if passed
        else _first_action_target(definition, [ACTION_VERIFICATION_FAILED, ACTION_FAIL])
    )
    record_phase_output(
        task_id,
        phase="apply",
        agent="plugin",
        status_key=status_key or current_status,
        summary="Plugin applied generated changes through the snapshot-protected path.",
        outputs={
            "ok": True,
            "snapshot_id": payload.get("snapshot_id"),
            "changed_paths": payload.get("changed_paths") or [],
            "verification": verification or {},
        },
        warnings=[] if passed else ["Verification requirements did not pass."],
        next_action=None if passed else ACTION_REQUEST_REWORK,
    )

    latest = _apply_configured_action(task_id, definition, ACTION_APPLY_SUCCEEDED, {"phase": "apply", **payload})
    if passed:
        if not has_verification_policy:
            skip_payload = {
                "reason": "Project policy explicitly allows done without verification.",
                "changed_paths": payload.get("changed_paths") or [],
                "snapshot_id": payload.get("snapshot_id"),
            }
            add_artifact(task_id, artifact_type="verification_skipped", payload=skip_payload)
            add_event(task_id, "verification_skipped", skip_payload)
        verified = _apply_optional_action(
            task_id,
            definition,
            ACTION_VERIFICATION_PASSED,
            {"phase": "verify", "verification": verification or {}},
        )
        if verified is not None:
            latest = verified
        reason = "verification_passed" if has_verification_policy else "verification_skipped_explicitly"
        add_event(task_id, "workflow_done_guard_passed", {"phase": "workflow", "reason": reason})
        done = _apply_optional_action(
            task_id,
            definition,
            ACTION_WORKFLOW_DONE,
            {"phase": "workflow", "reason": reason, "lifecycle_done_guard_passed": True},
        )
        return done or latest

    reason = (
        verification_feedback_summary(verification)
        if has_verification_policy
        else "No verification policy was provided and project policy does not allow skipping verification."
    )
    add_event(task_id, "workflow_done_guard_blocked", {"phase": "verify", "reason": reason})
    _create_failure_bundle(
        task_id,
        definition,
        failure_stage="verification",
        reason=reason,
        retryable=True,
        payload=payload,
        target_status_key=_action_target(definition, ACTION_VERIFICATION_FAILED) or _action_target(definition, ACTION_FAIL) or current_status,
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
        failure_statuses = _failure_statuses(definition)
        terminal_statuses = _terminal_statuses(definition)
        if action == ACTION_RETRY and status not in failure_statuses:
            continue
        if action == ACTION_ABANDON and status in terminal_statuses:
            continue
        target = _rule_target(rule)
        if target and _target_allowed(column, target, action=action):
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


def _targets(definition: WorkflowDefinition, actions: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for action in actions:
        target = _action_target(definition, action)
        if target:
            out.add(target)
    return out


def _failure_statuses(definition: WorkflowDefinition) -> set[str]:
    return _targets(definition, (ACTION_FAIL, ACTION_ABANDON))


def _success_statuses(definition: WorkflowDefinition) -> set[str]:
    return _targets(definition, (ACTION_WORKFLOW_DONE, "complete", "completed"))


def _terminal_statuses(definition: WorkflowDefinition) -> set[str]:
    return _failure_statuses(definition) | _success_statuses(definition)


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
    if definition.is_coding and str(getattr(column, "status_key", "") or "") != "ready_to_apply":
        return False
    return bool(target and _target_allowed(column, target))


def _allow_done_without_verification(definition: WorkflowDefinition) -> bool:
    parameters = definition.parameters if isinstance(definition.parameters, dict) else {}
    lifecycle = parameters.get("coding_lifecycle") if isinstance(parameters, dict) else {}
    return bool(isinstance(lifecycle, dict) and lifecycle.get("allow_done_without_verification") is True)


def _create_failure_bundle(
    task_id: str,
    definition: WorkflowDefinition,
    *,
    failure_stage: str,
    reason: str,
    retryable: bool,
    payload: dict[str, Any],
    target_status_key: str,
) -> dict[str, Any]:
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    phase_outputs = [
        item.get("payload") or {}
        for item in artifacts
        if isinstance(item, dict) and item.get("artifact_type") == "workflow_phase_output"
    ]
    revisions = task.get("revisions") if isinstance(task.get("revisions"), list) else []
    latest_revision = revisions[-1] if revisions else {}
    verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else {}
    failed_checks = _failed_verification_checks(verification)
    bundle = {
        "id": f"failure-{uuid.uuid4()}",
        "failure_stage": failure_stage,
        "reason": reason,
        "retryable": bool(retryable),
        "last_status_key": str(task.get("status_key") or ""),
        "target_status_key": target_status_key,
        "last_revision_id": latest_revision.get("id"),
        "changed_paths": payload.get("changed_paths") or latest_revision.get("changed_paths") or [],
        "apply_result": payload if failure_stage == "apply" or "snapshot_id" in payload else {},
        "verification": verification,
        "failed_checks": failed_checks,
        "last_agent_summary": str((phase_outputs[-1] or {}).get("summary") or "") if phase_outputs else "",
        "recommended_rework_entry": _recommended_rework_entry(definition),
        "created_at": _now(),
    }
    add_artifact(task_id, artifact_type="failure_bundle", payload=bundle)
    add_event(
        task_id,
        "failure_bundle_created",
        {
            "failure_bundle_id": bundle["id"],
            "failure_stage": failure_stage,
            "reason": reason,
            "retryable": retryable,
            "target_status_key": target_status_key,
        },
    )
    project_id = str(task.get("project_id") or "default")
    source_ref = {"failure_bundle_id": bundle["id"], "task_id": task_id}
    upsert_memory_item(
        project_id=project_id,
        task_id=task_id,
        scope="task",
        memory_type="task_handoff_summary",
        key=f"failure-{uuid.uuid4()}",
        content={
            "failure_bundle_id": bundle["id"],
            "summary": reason,
            "recommended_rework_entry": bundle["recommended_rework_entry"],
            "retryable": bool(retryable),
        },
        source_type="failure_bundle",
        source_ref=source_ref,
    )
    upsert_memory_item(
        project_id=project_id,
        task_id=task_id,
        scope="task",
        memory_type="task_test_state",
        key="latest",
        content={
            "failure_bundle_id": bundle["id"],
            "failure_stage": failure_stage,
            "verification": verification,
            "failed_checks": failed_checks,
        },
        source_type="failure_bundle",
        source_ref=source_ref,
    )
    upsert_memory_item(
        project_id=project_id,
        task_id=task_id,
        scope="task",
        memory_type="patch_summary",
        key="latest",
        content={
            "failure_bundle_id": bundle["id"],
            "last_revision_id": bundle["last_revision_id"],
            "changed_paths": bundle["changed_paths"],
            "apply_result": bundle["apply_result"],
        },
        source_type="failure_bundle",
        source_ref=source_ref,
    )
    return bundle


def _failed_verification_checks(verification: dict[str, Any]) -> list[dict[str, Any]]:
    results = verification.get("results") if isinstance(verification, dict) else {}
    if not isinstance(results, dict):
        return []
    failed = []
    for check, value in results.items():
        if str(value).lower() not in {"passed", "pass", "ok", "true", "success"}:
            failed.append({"check": str(check), "result": value})
    return failed


def _recommended_rework_entry(definition: WorkflowDefinition) -> str:
    retry_target = _action_target(definition, ACTION_RETRY)
    if retry_target:
        return retry_target
    for column in definition.columns:
        if column.executable:
            return column.status_key
    return ""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _is_client_visible_action(action: str, rule: dict[str, Any]) -> bool:
    return bool(rule.get("client_visible")) or action in CLIENT_VISIBLE_ACTIONS


def _target_allowed(column: Any, target: str, *, action: str | None = None) -> bool:
    if action in LIFECYCLE_ACTIONS:
        return True
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
