from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm_factory import get_llm_client
from app.services.workflow_definition import (
    WorkflowDefinition,
    validate_managed_workflow_definition,
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
        raw_reply = _ask_llm(
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

    try:
        workflow = _normalize_workflow(
            raw_reply.get("workflow") or base_workflow,
            base_workflow=base_workflow,
            debug=debug,
        )
    except ValueError as exc:
        debug["normalization_error"] = str(exc)
        raise WorkflowDesignError(str(exc), debug=debug) from exc
    agent_overrides = _normalize_agents(raw_reply.get("agents") or agents)
    reply = str(raw_reply.get("reply") or "Workflow draft updated.")

    definition = workflow_from_dict(workflow)
    try:
        validate_managed_workflow_definition(definition)
    except ValueError as exc:
        debug["validation_error"] = str(exc)
        raise WorkflowDesignError(str(exc), debug=debug) from exc
    workflow = _definition_to_payload(definition)
    debug["validated_workflow"] = workflow
    debug["agents"] = agent_overrides
    return {
        "ok": True,
        "project_id": project_id,
        "source": "llm",
        "reply": reply,
        "workflow": workflow,
        "agents": agent_overrides,
        "warnings": [],
        "summary": _workflow_summary(definition, agent_overrides),
        "debug": debug,
    }


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
                "The runtime requires explicit semantic actions workflow_done, fail, "
                "abandon, and retry, but their target column names are project-specific. "
                "Never rely on a no-transition column as an implicit terminal state. "
                "If you return a workflow, workflow.columns MUST be a non-empty array. "
                "Each column MUST include status_key and title. Every workflow MUST include "
                "explicit actions workflow_done, fail, abandon, and retry. "
                "If the user intent is insufficient to design a workflow, do not return an "
                "empty workflow; instead return a reply asking for clarification. "
                "Never return workflow: {}. Never return columns under a nested workflow.workflow object. "
                "For code-producing workflows, set workflow_type='coding' or requires_apply=true, "
                "include ready_to_apply/done/failed lifecycle columns, define code_ready, "
                "apply_succeeded, verification_failed, workflow_done, fail, abandon, and retry. "
                "Code-producing columns must use success_action='code_ready' and must not use "
                "success_action=workflow_done; generated code must pass through ready_to_apply "
                "and wait for apply_result before done. "
                "Columns may define status_key, title, position, transition_to, "
                "job_template, input_artifacts, output_artifact, success_action, "
                "failure_actions, context_policy. Keep workflow implementation-neutral. "
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
    workflow["workflow_type"] = str(workflow.get("workflow_type") or "").strip().lower()
    workflow["requires_apply"] = bool(workflow.get("requires_apply", False))
    workflow["parameters"] = workflow.get("parameters") if isinstance(workflow.get("parameters"), dict) else {}
    workflow["columns"] = _normalize_columns(workflow.get("columns"), base_workflow=base_workflow)
    workflow["actions"] = _normalize_actions(
        workflow.get("actions"),
        workflow["columns"],
        debug=debug,
        coding=workflow["requires_apply"] or workflow["workflow_type"] == "coding",
    )
    if debug is not None:
        debug["normalized_workflow"] = workflow
    return workflow


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
        ):
            if optional in raw:
                col[optional] = raw[optional]
            elif optional in base_col:
                col[optional] = base_col[optional]
        out.append(col)
    if not out:
        raise ValueError("workflow must define project-specific columns")
    return sorted(out, key=lambda item: int(item.get("position") or 0))


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
        if not isinstance(raw, dict):
            continue
        action_key = _status_key(key)
        if raw.get("to") in (None, "") and any(
            raw.get(alias) not in (None, "")
            for alias in ("target", "target_column", "target_status", "status", "column")
        ):
            _note(debug, f"action {action_key!r} target normalized from alternate action target field")
        target = _status_key(
            raw.get("to")
            or raw.get("target")
            or raw.get("target_column")
            or raw.get("target_status")
            or raw.get("status")
            or raw.get("column")
        )
        repaired = _repair_action_target(action_key, target, known, coding=coding)
        if repaired != target:
            _note(debug, f"action {action_key!r} target {target!r} normalized to {repaired!r}")
            target = repaired
        if target in known:
            normalized[action_key] = {"to": target}
    for column in columns:
        if not isinstance(column, dict):
            continue
        success_action = _status_key(column.get("success_action"))
        if not success_action or success_action in normalized:
            continue
        transitions = [_status_key(item) for item in column.get("transition_to") or [] if _status_key(item)]
        target = next((item for item in transitions if item in known and item != "failed"), None)
        if target:
            normalized[success_action] = {"to": target}
            _note(debug, f"action {success_action!r} inferred from explicit column success_action")
    fail_target = str((normalized.get("fail") or {}).get("to") or "")
    if fail_target and "abandon" not in normalized:
        normalized["abandon"] = {"to": fail_target}
        _note(debug, f"action 'abandon' aligned to explicit fail target {fail_target!r}")
    return normalized


def _repair_action_target(action: str, target: str, known: set[str], *, coding: bool = False) -> str:
    failure_aliases = {"abandoned", "abandon", "blocked", "failure", "error", "cancelled", "canceled"}
    if coding and action in {"fail", "abandon"} and target in failure_aliases and "failed" in known:
        return "failed"
    if target in known:
        return target
    success_aliases = {"complete", "completed", "success", "successful"}
    if action in {"fail", "abandon"} and target in failure_aliases and "failed" in known:
        return "failed"
    if action in {"workflow_done", "complete", "completed"} and target in success_aliases and "done" in known:
        return "done"
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
                **({"job_template": column.job_template} if column.job_template else {}),
                **({"input_artifacts": list(column.input_artifacts or [])} if column.input_artifacts else {}),
                **({"output_artifact": column.output_artifact} if column.output_artifact else {}),
                **({"success_action": column.success_action} if column.success_action else {}),
                **({"failure_actions": list(column.failure_actions or [])} if column.failure_actions else {}),
                **({"context_policy": dict(column.context_policy or {})} if column.context_policy else {}),
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
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


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
