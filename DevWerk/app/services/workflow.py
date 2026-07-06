from __future__ import annotations

import logging
import uuid
from typing import Any

from app.kanban.definition import (
    ActionKind,
    TerminalKind,
    WorkflowColumn,
    WorkflowDefinition,
    canonical_workflow_key,
    workflow_from_dict,
)
from app.kanban.state_machine import WorkflowStateMachine
from app.kanban.store import add_artifact, add_event, get_project_workflow, get_task, move_task
from app.services.memory_system import upsert_memory_item
from app.services.session_store import record_phase_memory

_log = logging.getLogger("devwerk.workflow")


CONTROL_ACTION_KINDS = {
    "fail": ActionKind.FAILURE.value,
    "failed": ActionKind.FAILURE.value,
    "error": ActionKind.FAILURE.value,
    "abandon": ActionKind.CANCEL.value,
    "cancel": ActionKind.CANCEL.value,
    "retry": ActionKind.RETRY.value,
    "rerun": ActionKind.RETRY.value,
    "re_run": ActionKind.RETRY.value,
    "apply_result": ActionKind.APPLY.value,
    "applied": ActionKind.APPLY.value,
}


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
    data = dict(payload or {})
    task_detail = get_task(task_id)
    task = task_detail.get("task") or {}
    current_status = canonical_workflow_key(task.get("status_key"))
    definition = _definition_from_task_record(task)
    machine = WorkflowStateMachine(definition)
    action_key = _resolve_action(definition, current_status, action)
    _log.debug(
        "workflow action task_id=%s requested_action=%s resolved_action=%s current=%s payload=%s",
        task_id,
        action,
        action_key,
        current_status,
        data,
    )

    decision = machine.decide(current_status, action_key, data)
    if decision.action_kind == ActionKind.RETRY.value and decision.from_status == decision.to_status:
        add_event(
            task_id,
            "workflow_action_ignored",
            {
                "action": decision.action,
                "action_kind": decision.action_kind,
                "status_key": decision.from_status,
                "reason": data.get("reason") or "retry target is already active",
            },
        )
        add_event(
            task_id,
            "workflow_retry_deduplicated",
            {
                "action": decision.action,
                "status_key": decision.from_status,
                "reason": data.get("reason") or "retry target is already active",
            },
        )
        ignored = get_task(task_id)
        ignored["action_ignored"] = True
        ignored["ignored_action"] = decision.action
        return ignored
    data.setdefault("action", decision.action)
    data.setdefault("action_kind", decision.action_kind)
    data.setdefault("from_status", decision.from_status)
    data.setdefault("to_status", decision.to_status)
    add_event(
        task_id,
        "workflow_transition_decided",
        {
            "action": decision.action,
            "action_kind": decision.action_kind,
            "from_status": decision.from_status,
            "to_status": decision.to_status,
            "terminal": decision.terminal,
            "terminal_kind": decision.terminal_kind,
            "reason": data.get("reason") or data.get("error_message"),
        },
    )
    add_event(task_id, f"workflow_action_{decision.action}", data)
    if decision.action_kind in {ActionKind.FAILURE.value, ActionKind.CANCEL.value}:
        _create_failure_bundle(
            task_id,
            definition,
            current_status=decision.from_status,
            target_status=decision.to_status,
            reason=str(data.get("reason") or data.get("error_message") or "Workflow entered a failure terminal."),
            retryable=decision.action_kind != ActionKind.CANCEL.value,
            payload=data,
        )
    moved = move_task(task_id, decision.to_status, force=True, payload=data)
    if decision.terminal:
        add_event(
            task_id,
            "workflow_terminal_reached",
            {
                "status_key": decision.to_status,
                "terminal_kind": decision.terminal_kind,
                "ok": decision.terminal_kind == TerminalKind.SUCCESS.value,
            },
        )
    return moved


def current_workflow_state(task_id: str) -> dict[str, Any]:
    detail = get_task(task_id)
    task = detail.get("task") or {}
    definition = _definition_from_task_record(task)
    status = canonical_workflow_key(task.get("status_key"))
    machine = WorkflowStateMachine(definition)
    return {
        "ok": True,
        "task_id": task_id,
        "status_key": status,
        "terminal": machine.is_terminal(status),
        "terminal_kind": machine.terminal_kind(status),
        "actions": available_actions_for_status(status, definition),
        "workflow": definition.summary(),
    }


def available_actions_for_status(status_key: str, definition: WorkflowDefinition | None = None) -> list[str]:
    if definition is None:
        return []
    status = canonical_workflow_key(status_key)
    column = definition.column(status)
    if column is None:
        return []
    actions: list[str] = []
    for action, rule in definition.actions.items():
        target = canonical_workflow_key(rule.get("to"))
        if not target:
            continue
        if column.terminal:
            from_rule = canonical_workflow_key(rule.get("from") or rule.get("from_status"))
            if definition.action_kind(action) == ActionKind.RETRY.value and (
                target in set(column.transition_to) or from_rule == status
            ):
                actions.append(action)
            continue
        if target in set(column.transition_to) or action in column.declared_actions():
            actions.append(action)
            continue
        from_rule = canonical_workflow_key(rule.get("from") or rule.get("from_status"))
        if from_rule == status:
            actions.append(action)
    return _dedupe(actions)


def _resolve_action(definition: WorkflowDefinition, current_status: str, action: str) -> str:
    requested = canonical_workflow_key(action)
    if definition.action(requested) is not None:
        return requested
    kind = CONTROL_ACTION_KINDS.get(requested)
    if not kind:
        raise ValueError(f"unknown workflow action: {requested}")
    candidates = available_actions_for_status(current_status, definition)
    for candidate in candidates:
        candidate_kind = definition.action_kind(candidate)
        if candidate_kind == kind:
            return candidate
        if kind == ActionKind.FAILURE.value and candidate_kind == ActionKind.CANCEL.value:
            return candidate
        if kind == ActionKind.CANCEL.value and candidate_kind == ActionKind.FAILURE.value:
            return candidate
    raise ValueError(f"workflow action {requested!r} has no matching {kind!r} action from {current_status!r}")


def _definition_from_task_record(task: dict[str, Any]) -> WorkflowDefinition:
    project_id = str(task.get("project_id") or "default")
    workflow_payload = get_project_workflow(project_id).get("workflow") or {}
    return workflow_from_dict(workflow_payload)


def _create_failure_bundle(
    task_id: str,
    definition: WorkflowDefinition,
    *,
    current_status: str,
    target_status: str,
    reason: str,
    retryable: bool,
    payload: dict[str, Any],
) -> None:
    task = (get_task(task_id).get("task") or {})
    project_id = str(task.get("project_id") or "default")
    bundle = {
        "task_id": task_id,
        "project_id": project_id,
        "workflow": definition.summary(),
        "current_status": current_status,
        "target_status": target_status,
        "reason": reason,
        "retryable": retryable,
        "payload": payload,
    }
    add_artifact(task_id, artifact_type="failure_bundle", payload=bundle)
    add_event(task_id, "failure_bundle_created", bundle)
    try:
        upsert_memory_item(
            project_id=project_id,
            item_type="failure",
            key=f"task:{task_id}:failure",
            content=reason,
            metadata=bundle,
            confidence=0.9,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("failure memory skipped task_id=%s error=%s", task_id, exc)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
