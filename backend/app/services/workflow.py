from __future__ import annotations

import logging
from typing import Any

from app.services.kanban import add_artifact, add_event, get_task, move_task

_log = logging.getLogger("devwerk.workflow")


ACTION_CONTEXT_INDEXED = "context_indexed"
ACTION_PLAN_READY = "plan_ready"
ACTION_PLAN_FAILED = "plan_failed"
ACTION_CODING_STARTED = "coding_started"
ACTION_READY_TO_APPLY = "ready_to_apply"
ACTION_CODING_FAILED = "coding_failed"
ACTION_APPLY_RESULT = "apply_result"
ACTION_RETRY = "retry"
ACTION_ABANDON = "abandon"


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

    if action_key == ACTION_CONTEXT_INDEXED:
        add_event(task_id, "workflow_context_indexed", data)
        return move_task(task_id, "context_indexed", force=True, payload=data)

    if action_key == ACTION_PLAN_READY:
        add_event(task_id, "workflow_plan_ready", data)
        return move_task(task_id, "planned", force=True, payload=data)

    if action_key == ACTION_PLAN_FAILED:
        add_event(task_id, "workflow_plan_failed", data)
        return move_task(task_id, "failed", force=True, payload={"phase": "plan", **data})

    if action_key == ACTION_CODING_STARTED:
        add_event(task_id, "workflow_coding_started", data)
        return move_task(task_id, "coding", force=True, payload=data)

    if action_key == ACTION_READY_TO_APPLY:
        add_event(task_id, "workflow_ready_to_apply", data)
        return move_task(task_id, "ready_to_apply", force=True, payload=data)

    if action_key == ACTION_CODING_FAILED:
        add_event(task_id, "workflow_coding_failed", data)
        return move_task(task_id, "failed", force=True, payload={"phase": "coding", **data})

    if action_key == ACTION_APPLY_RESULT:
        return _apply_result(task_id, data)

    if action_key == ACTION_RETRY:
        add_event(task_id, "manual_retry_requested", data or {"reason": "user_requested_retry"})
        return move_task(task_id, "draft", force=True, payload={"reason": "manual_retry", **data})

    if action_key == ACTION_ABANDON:
        add_event(task_id, "manual_abandon_requested", data or {"reason": "user_abandoned_task"})
        return move_task(task_id, "failed", force=True, payload={"reason": "manual_abandon", **data})

    raise ValueError(f"unknown workflow action: {action}")


def _apply_result(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok = bool(payload.get("ok", True))
    add_artifact(task_id, artifact_type="apply_result", payload=payload)
    add_event(task_id, "apply_result_received", payload)

    if not ok:
        return move_task(task_id, "failed", force=True, payload={"phase": "apply", **payload})

    applied = move_task(task_id, "applied", force=True, payload=payload)
    verification = payload.get("verification")
    required = verification.get("required") if isinstance(verification, dict) else None
    results = verification.get("results") if isinstance(verification, dict) else None
    if isinstance(required, list) and isinstance(results, dict):
        passed = all(str(results.get(item)).lower() == "passed" for item in required)
        if passed:
            move_task(task_id, "verified", force=True, payload={"verification": verification})
            return move_task(task_id, "done", force=True, payload={"reason": "verification_passed"})
    return applied


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
    if status in {"planned", "coding", "ready_to_apply", "applied", "verified"}:
        return [ACTION_ABANDON]
    if status in {"done"}:
        return []
    return [ACTION_ABANDON]
