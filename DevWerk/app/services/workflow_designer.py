from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app.services.llm_factory import get_llm_client
from app.kanban.definition import (
    WorkflowColumn,
    WorkflowDefinition,
    canonical_workflow_key,
    validate_managed_workflow_definition,
    workflow_column_can_produce_code,
    workflow_from_dict,
)

_log = logging.getLogger("devwerk.workflow_designer")


class WorkflowDesignError(ValueError):
    def __init__(self, message: str, *, debug: dict[str, Any] | None = None):
        super().__init__(message)
        self.debug = debug or {}


def design_project_workflow(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    current_workflow: dict[str, Any] | None = None,
    current_agents: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or revise a managed workflow draft for a project.

    The designer is intentionally not part of the coding workflow runtime. It is
    a project-setup helper that produces the same JSON configuration the runtime
    already consumes, then the normal workflow validator remains the authority.
    """

    base_workflow = _workflow_payload(current_workflow)
    agents = dict(current_agents or {})
    user_text = _latest_user_text(messages)
    if not user_text:
        raise ValueError("project conversation requires a user message")

    debug: dict[str, Any] = {"normalization_notes": []}
    try:
        raw_reply = _ask_llm_with_retries(
            project_id=project_id,
            messages=messages,
            base_workflow=base_workflow,
            agents=agents,
            debug=debug,
        )
    except Exception as exc:  # noqa: BLE001
        llm_error = f"{type(exc).__name__}: {exc}"
        _log.warning("workflow designer llm failed project_id=%s error=%s", project_id, llm_error)
        raise WorkflowDesignError(f"project LLM agent failed: {llm_error}", debug=debug) from exc
    debug.setdefault("llm_output", raw_reply)

    first_error_message = ""
    try:
        return _build_design_result(
            project_id=project_id,
            raw_reply=raw_reply,
            base_workflow=base_workflow,
            base_agents=agents,
            debug=debug,
            repaired=False,
        )
    except ValueError as first_error:
        first_error_message = str(first_error)
        _log.warning(
            "workflow designer output failed validation project_id=%s error=%s debug=%s",
            project_id,
            first_error_message,
            _safe_json(_debug_summary(debug)),
        )

    try:
        repaired_reply = _ask_llm_repair(
            project_id=project_id,
            messages=messages,
            base_workflow=base_workflow,
            agents=agents,
            raw_reply=raw_reply,
            validation_error=first_error_message,
            debug=debug,
        )
        return _build_design_result(
            project_id=project_id,
            raw_reply=repaired_reply,
            base_workflow=base_workflow,
            base_agents=agents,
            debug=debug,
            repaired=True,
        )
    except Exception as repair_error:  # noqa: BLE001
        debug["repair_error"] = f"{type(repair_error).__name__}: {repair_error}"
        _log.warning(
            "workflow designer repair failed project_id=%s original_error=%s repair_error=%s debug=%s",
            project_id,
            first_error_message,
            debug["repair_error"],
            _safe_json(_debug_summary(debug)),
        )
        raise WorkflowDesignError(first_error_message, debug=debug) from repair_error


def _build_design_result(
    *,
    project_id: str,
    raw_reply: dict[str, Any],
    base_workflow: dict[str, Any],
    base_agents: dict[str, Any],
    debug: dict[str, Any],
    repaired: bool,
) -> dict[str, Any]:
    workflow_payload = raw_reply.get("workflow") or base_workflow
    if isinstance(workflow_payload, dict) and not isinstance(workflow_payload.get("actions"), dict) and isinstance(raw_reply.get("actions"), dict):
        workflow_payload = {**workflow_payload, "actions": raw_reply.get("actions")}
        _note(debug, "top-level actions merged into workflow.actions")
    try:
        workflow = _normalize_workflow(
            workflow_payload,
            base_workflow=base_workflow,
            debug=debug,
        )
    except ValueError as exc:
        debug["normalization_error"] = str(exc)
        raise
    agent_overrides = _normalize_agents(raw_reply.get("agents") or base_agents)
    reply = str(raw_reply.get("reply") or "Workflow draft updated.")

    definition = workflow_from_dict(workflow)
    try:
        validate_managed_workflow_definition(definition)
    except ValueError as exc:
        debug["validation_error"] = str(exc)
        raise
    workflow = _definition_to_payload(definition)
    debug["validated_workflow"] = workflow
    debug["agents"] = agent_overrides
    if repaired:
        debug["repair_applied"] = True
    return {
        "ok": True,
        "project_id": project_id,
        "source": "llm_repaired" if repaired else "llm",
        "reply": reply,
        "workflow": workflow,
        "agents": agent_overrides,
        "warnings": [],
        "summary": _workflow_summary(definition, agent_overrides),
        "debug": debug,
    }


def _ask_llm_with_retries(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    base_workflow: dict[str, Any],
    agents: dict[str, Any],
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts = _env_int("DEVWERK_WORKFLOW_DESIGN_LLM_ATTEMPTS", 2, minimum=1, maximum=5)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            if debug is not None:
                debug["llm_attempt"] = attempt
                debug["llm_attempts"] = attempts
            return _ask_llm(
                project_id=project_id,
                messages=messages,
                base_workflow=base_workflow,
                agents=agents,
                debug=debug,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            errors.append(error)
            if debug is not None:
                debug["llm_errors"] = list(errors)
            if attempt >= attempts:
                raise
            _log.warning(
                "workflow designer llm attempt failed project_id=%s attempt=%s/%s error=%s",
                project_id,
                attempt,
                attempts,
                error,
            )
            time.sleep(min(2.0, 0.5 * attempt))
    raise RuntimeError("workflow designer LLM attempts exhausted")


def _ask_llm(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    base_workflow: dict[str, Any],
    agents: dict[str, Any],
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = [
        {
            "role": "system",
            "content": (
                "You are DevWerk's workflow designer. Return one JSON object only. "
                "Design a managed Kanban workflow and project agent overrides. "
                "Do not assume any default columns. The user defines the workflow. "
                "The runtime is definition-driven: every completion, failure, retry, "
                "cancel, apply, and verification path must be represented by explicit actions "
                "with a kind field and a target column. Never rely on a no-transition column "
                "as an implicit terminal state. "
                "If you return a workflow, workflow.columns MUST be a non-empty array. "
                "Each column MUST include status_key and title. Terminal columns MUST set "
                "terminal=true and terminal_kind='success' or 'failure'. Every non-terminal "
                "executable column MUST declare success_action and failure_actions. "
                "If the user intent is insufficient to design a workflow, do not return an "
                "empty workflow; instead return a reply asking for clarification. "
                "Never return workflow: {}. Never return columns under a nested workflow.workflow object. "
                "For code-producing workflows, set workflow_type='coding' or requires_apply=true, "
                "the code-producing column must set output_contract='code_change' or "
                "context_policy.output_contract='code_change'. If backend should write files, "
                "include a runtime column with runtime='backend_apply' and an action with kind='apply'. "
                "Columns may define status_key, title, position, transition_to, "
                "job_template, input_artifacts, output_artifact, success_action, "
                "failure_actions, context_policy, kind, runtime, terminal, terminal_kind. "
                "Actions use shape actions: {action_key: {to, kind, from?}} where kind is "
                "advance, success, failure, retry, cancel, wait, apply, or verify. "
                "Keep workflow implementation-neutral. "
                "Do not hard-code Java, IntelliJ, VS Code, or CI assumptions. "
                "Use capabilities by name only, such as workspace.read, project.compile, "
                "source.diagnostics, process.run, browser.cdp, browser.playwright, "
                "network.http, and network.web. Add browser-automation/network-access skills "
                "to agents that need eyes or external information, then let runtime clients "
                "provide those capabilities. JSON shape: {reply, workflow, agents, notes}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "project_id": project_id,
                    "current_workflow": base_workflow,
                    "current_agents": agents,
                    "conversation": messages[-12:],
                },
                ensure_ascii=False,
            ),
        },
    ]
    if debug is not None:
        debug["llm_input"] = prompt
    _log.debug("workflow designer llm input project_id=%s messages=%s", project_id, _safe_json(prompt))
    obj = get_llm_client("project").chat_json(prompt)
    if not isinstance(obj, dict):
        raise ValueError("workflow designer LLM returned non-object JSON")
    if debug is not None:
        debug["llm_output"] = obj
    _log.debug("workflow designer llm output project_id=%s response=%s", project_id, _safe_json(obj))
    return obj


def _ask_llm_repair(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    base_workflow: dict[str, Any],
    agents: dict[str, Any],
    raw_reply: dict[str, Any],
    validation_error: str,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = [
        {
            "role": "system",
            "content": (
                "You repair DevWerk workflow JSON. Return one JSON object only. "
                "Do not redesign the user's project. Keep project-specific columns and names when possible. "
                "Fix only protocol shape, action kinds, terminal metadata, transitions, and agent overrides. "
                "A managed workflow must have at least one explicit success terminal column and one explicit "
                "failure terminal column. Every transition must be represented by action.to and/or "
                "column.transition_to. Never rely on an implicit terminal column. "
                "For coding workflows, code-producing columns must set output_contract='code_change'. "
                "If files should be written locally, add a runtime='backend_apply' column and an action kind='apply'. "
                "JSON shape: {reply, workflow, agents, notes}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "project_id": project_id,
                    "validation_error": validation_error,
                    "current_workflow": base_workflow,
                    "current_agents": agents,
                    "conversation": messages[-12:],
                    "invalid_llm_reply": raw_reply,
                },
                ensure_ascii=False,
            ),
        },
    ]
    if debug is not None:
        debug["repair_llm_input"] = prompt
    _log.debug("workflow designer repair llm input project_id=%s messages=%s", project_id, _safe_json(prompt))
    obj = get_llm_client("project").chat_json(prompt)
    if not isinstance(obj, dict):
        raise ValueError("workflow designer repair LLM returned non-object JSON")
    if debug is not None:
        debug["repair_llm_output"] = obj
    _log.debug("workflow designer repair llm output project_id=%s response=%s", project_id, _safe_json(obj))
    return obj


def _workflow_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("columns"):
        return value
    return {"name": "project-workflow", "version": 1, "columns": [], "actions": {}}


def _normalize_workflow(
    value: object,
    *,
    base_workflow: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("workflow designer LLM did not return a workflow object")
    workflow = _repair_workflow_object(value, debug=debug)
    workflow["name"] = str(workflow.get("name") or "project-workflow")
    workflow["version"] = int(workflow.get("version") or 1)
    workflow["workflow_type"] = canonical_workflow_key(workflow.get("workflow_type"))
    workflow["requires_apply"] = bool(workflow.get("requires_apply", False))
    workflow["parameters"] = workflow.get("parameters") if isinstance(workflow.get("parameters"), dict) else {}
    workflow["columns"] = _normalize_columns(workflow.get("columns"), base_workflow=base_workflow)
    _infer_missing_column_transitions(workflow["columns"], debug=debug)
    workflow["actions"] = _normalize_actions(
        workflow.get("actions"),
        workflow["columns"],
        debug=debug,
        coding=workflow["requires_apply"] or workflow["workflow_type"] == "coding",
    )
    _ensure_column_success_actions(workflow["columns"], workflow["actions"], debug=debug)
    _align_column_transitions_with_actions(workflow["columns"], workflow["actions"], debug=debug)
    _sanitize_terminal_columns(workflow["columns"], workflow["actions"], debug=debug)
    if debug is not None:
        debug["normalized_workflow"] = workflow
    return workflow


def normalize_workflow_payload(
    value: object,
    *,
    base_workflow: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a persisted workflow without calling an LLM.

    Project workflow JSON can arrive from the project conversation, direct API
    edits, imported databases, or hand-written dashboard changes. All of those
    paths must pass through the same deterministic repair layer before the
    state machine sees the definition.
    """

    return _normalize_workflow(value, base_workflow=base_workflow, debug=debug)


def _repair_workflow_object(value: dict[str, Any], *, debug: dict[str, Any] | None = None) -> dict[str, Any]:
    workflow = dict(value)
    nested = workflow.get("workflow")
    if isinstance(nested, dict) and nested.get("columns"):
        _note(debug, "workflow.workflow.columns unwrapped to workflow.columns")
        workflow = {**nested, **{key: val for key, val in workflow.items() if key not in {"workflow", "columns", "actions"}}}
    kanban = workflow.get("kanban")
    if not workflow.get("columns") and isinstance(kanban, dict) and kanban.get("columns"):
        workflow["columns"] = kanban.get("columns")
        _note(debug, "kanban.columns normalized to workflow.columns")
    if not workflow.get("columns") and workflow.get("states"):
        workflow["columns"] = _states_to_columns(workflow.get("states"))
        _note(debug, "states normalized to workflow.columns")
    return workflow


def _normalize_columns(value: object, *, base_workflow: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("workflow.columns must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_columns = {
        _status_key(item.get("status_key")): item
        for item in ((base_workflow or {}).get("columns") or [])
        if isinstance(item, dict) and _status_key(item.get("status_key"))
    }
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        key = _status_key(raw.get("status_key") or raw.get("key") or raw.get("id") or raw.get("state"))
        if not key or key in seen:
            continue
        seen.add(key)
        transitions = raw.get("transition_to")
        if transitions is None:
            transitions = raw.get("transitions") or raw.get("next") or raw.get("next_states") or raw.get("to")
        if isinstance(transitions, str):
            transitions = [transitions]
        col = {
            "status_key": key,
            "title": str(raw.get("title") or raw.get("name") or key.replace("_", " ").title()),
            "position": int(raw.get("position") or (index + 1) * 10),
            "transition_to": [_status_key(item) for item in transitions or [] if _status_key(item)],
        }
        base_col = base_columns.get(key) if isinstance(base_columns.get(key), dict) else {}
        for optional in (
            "job_template",
            "input_artifacts",
            "output_artifact",
            "success_action",
            "failure_actions",
            "context_policy",
            "kind",
            "agent",
            "runtime",
            "output_contract",
            "retry_policy",
            "terminal",
            "terminal_kind",
            "terminal_type",
            "is_terminal",
        ):
            if optional in raw:
                col[optional] = raw[optional]
            elif optional in base_col:
                col[optional] = base_col[optional]
        out.append(col)
    if not out:
        raise ValueError("workflow must define project-specific columns")
    return sorted(out, key=lambda item: int(item.get("position") or 0))


def _infer_missing_column_transitions(columns: list[dict[str, Any]], *, debug: dict[str, Any] | None = None) -> None:
    ordered = sorted(columns, key=lambda item: int(item.get("position") or 0))
    for index, column in enumerate(ordered[:-1]):
        if column.get("transition_to") or column.get("terminal") or column.get("is_terminal"):
            continue
        target = _status_key(ordered[index + 1].get("status_key"))
        if not target:
            continue
        column["transition_to"] = [target]
        _note(debug, f"column {column.get('status_key')!r} transition_to inferred from position order: {target!r}")


def _states_to_columns(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        states = []
        for index, (key, raw) in enumerate(value.items()):
            item = dict(raw) if isinstance(raw, dict) else {}
            item.setdefault("status_key", key)
            item.setdefault("position", (index + 1) * 10)
            states.append(item)
        return states
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _normalize_actions(
    value: object,
    columns: list[dict[str, Any]],
    *,
    debug: dict[str, Any] | None = None,
    coding: bool = False,
) -> dict[str, dict[str, Any]]:
    known = {str(col.get("status_key") or "") for col in columns}
    actions = dict(value) if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, raw in actions.items():
        if isinstance(raw, str):
            raw = {"to": _action_string_target(raw)}
        if not isinstance(raw, dict):
            continue
        action_key = _status_key(key)
        if raw.get("to") in (None, "") and any(
            raw.get(alias) not in (None, "")
            for alias in ("target", "target_column", "target_status", "to_status", "status", "column", "transition_to")
        ):
            _note(debug, f"action {action_key!r} target normalized from alternate action target field")
        target = _status_key(
            raw.get("to")
            or raw.get("target")
            or raw.get("target_column")
            or raw.get("target_status")
            or raw.get("to_status")
            or raw.get("status")
            or raw.get("column")
            or raw.get("transition_to")
        )
        repaired = _repair_action_target(action_key, target, known, coding=coding)
        if repaired != target:
            _note(debug, f"action {action_key!r} target {target!r} normalized to {repaired!r}")
            target = repaired
        if target in known:
            rule = {"to": target}
            kind = _status_key(raw.get("kind") or raw.get("type") or raw.get("semantic"))
            if kind:
                rule["kind"] = kind
            from_status = _status_key(raw.get("from") or raw.get("from_status"))
            if from_status:
                rule["from"] = from_status
            normalized[action_key] = rule
    for column in columns:
        if not isinstance(column, dict):
            continue
        success_action = _status_key(column.get("success_action"))
        if not success_action or success_action in normalized:
            continue
        failure_targets = _failure_terminal_targets(columns)
        transitions = [_status_key(item) for item in column.get("transition_to") or [] if _status_key(item)]
        target = next((item for item in transitions if item in known and item not in failure_targets), None)
        target = target or next((item for item in transitions if item in known), None)
        if target:
            normalized[success_action] = {"to": target, "kind": "advance"}
            _note(debug, f"action {success_action!r} inferred from explicit column success_action")
    return normalized


def _action_string_target(value: str) -> str:
    target = _status_key(value)
    for prefix in ("move_to_", "go_to_", "transition_to_"):
        if target.startswith(prefix):
            return target[len(prefix) :]
    return target


def _ensure_column_success_actions(
    columns: list[dict[str, Any]],
    actions: dict[str, dict[str, Any]],
    *,
    debug: dict[str, Any] | None,
) -> None:
    for column in columns:
        if not isinstance(column, dict):
            continue
        action = _status_key(column.get("success_action"))
        if action and action in actions:
            continue
        if not column.get("job_template"):
            continue
        failure_targets = _failure_terminal_targets(columns)
        transitions = [_status_key(item) for item in column.get("transition_to") or [] if _status_key(item)]
        target = next((item for item in transitions if item not in failure_targets), "")
        target = target or (transitions[0] if transitions else "")
        if not target:
            continue
        action = action or _status_key(f"{column.get('status_key')}_complete")
        column["success_action"] = action
        actions.setdefault(action, {"to": target})
        _note(debug, f"column {column.get('status_key')!r} success_action {action!r} inferred to {target!r}")


def _align_column_transitions_with_actions(
    columns: list[dict[str, Any]],
    actions: dict[str, dict[str, Any]],
    *,
    debug: dict[str, Any] | None,
) -> None:
    for column in columns:
        if not isinstance(column, dict):
            continue
        action_names = [_status_key(column.get("success_action"))]
        action_names.extend(_status_key(item) for item in column.get("failure_actions") or [])
        transitions = [_status_key(item) for item in column.get("transition_to") or [] if _status_key(item)]
        changed = False
        for action_name in action_names:
            target = _status_key((actions.get(action_name) or {}).get("to"))
            if target and target != _status_key(column.get("status_key")) and target not in transitions:
                transitions.append(target)
                changed = True
                _note(
                    debug,
                    f"column {column.get('status_key')!r} transition_to appended {target!r} from action {action_name!r}",
                )
        if changed:
            column["transition_to"] = transitions


def _sanitize_terminal_columns(
    columns: list[dict[str, Any]],
    actions: dict[str, dict[str, Any]],
    *,
    debug: dict[str, Any] | None,
) -> None:
    for column in columns:
        if not isinstance(column, dict) or not bool(column.get("terminal") or column.get("is_terminal")):
            continue
        removed = []
        for key in ("job_template", "success_action", "failure_actions", "input_artifacts", "output_artifact"):
            if column.get(key):
                removed.append(key)
                column.pop(key, None)
        if removed:
            _note(
                debug,
                f"terminal column {column.get('status_key')!r} execution fields removed: {', '.join(removed)}",
            )


def _failure_terminal_targets(columns: list[dict[str, Any]]) -> set[str]:
    targets: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            continue
        if bool(column.get("terminal") or column.get("is_terminal")) and _status_key(column.get("terminal_kind")) == "failure":
            status = _status_key(column.get("status_key"))
            if status:
                targets.add(status)
    return targets


def _first_existing_status(candidates: tuple[str, ...], known: set[str]) -> str:
    return next((candidate for candidate in candidates if candidate in known), "")


def _first_retry_status(columns: list[dict[str, Any]], terminal_targets: set[str]) -> str:
    for column in sorted(columns, key=lambda item: int(item.get("position") or 0)):
        status = str(column.get("status_key") or "")
        if status and status not in terminal_targets:
            return status
    return ""


def _repair_action_target(action: str, target: str, known: set[str], *, coding: bool = False) -> str:
    return target


def _note(debug: dict[str, Any] | None, message: str) -> None:
    if debug is None:
        return
    notes = debug.setdefault("normalization_notes", [])
    if isinstance(notes, list):
        notes.append(message)


def _definition_to_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "version": definition.version,
        "workflow_type": definition.workflow_type,
        "requires_apply": bool(definition.requires_apply),
        "parameters": dict(definition.parameters or {}),
        "columns": [
            {
                "status_key": column.status_key,
                "title": column.title,
                "position": column.position,
                "transition_to": list(column.transition_to),
                "kind": column.kind,
                **({"agent": column.agent} if column.agent else {}),
                **({"runtime": column.runtime} if column.runtime else {}),
                **({"job_template": column.job_template} if column.job_template else {}),
                **({"input_artifacts": list(column.input_artifacts or [])} if column.input_artifacts else {}),
                **({"output_artifact": column.output_artifact} if column.output_artifact else {}),
                **({"output_contract": column.output_contract} if column.output_contract else {}),
                **({"success_action": column.success_action} if column.success_action else {}),
                **({"failure_actions": list(column.failure_actions or [])} if column.failure_actions else {}),
                **({"context_policy": dict(column.context_policy or {})} if column.context_policy else {}),
                **({"retry_policy": dict(column.retry_policy or {})} if column.retry_policy else {}),
                **({"terminal": True, "terminal_kind": column.terminal_kind} if column.terminal else {}),
            }
            for column in definition.columns
        ],
        "actions": dict(definition.actions),
    }


def _normalize_agents(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if isinstance(message, dict) and str(message.get("role") or "").lower() == "user":
            return str(message.get("content") or "").strip()
    return ""


def _status_key(value: object) -> str:
    return canonical_workflow_key(value)


def _workflow_summary(definition: WorkflowDefinition, agents: dict[str, Any]) -> dict[str, Any]:
    return {
        "columns": len(definition.columns),
        "executable_columns": [column.status_key for column in definition.columns if column.executable],
        "actions": sorted(definition.actions.keys()),
        "agent_overrides": sorted(agents.keys()),
    }


def _safe_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _debug_summary(debug: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "normalization_error",
        "validation_error",
        "repair_error",
        "normalization_notes",
        "llm_errors",
        "repair_applied",
    )
    return {key: debug.get(key) for key in keys if key in debug}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
