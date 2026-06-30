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

    try:
        raw_reply = _ask_llm(project_id=project_id, messages=messages, base_workflow=base_workflow, agents=agents)
    except Exception as exc:  # noqa: BLE001
        llm_error = f"{type(exc).__name__}: {exc}"
        _log.warning("workflow designer llm failed project_id=%s error=%s", project_id, llm_error)
        raise ValueError(f"project LLM agent failed: {llm_error}") from exc

    workflow = _normalize_workflow(raw_reply.get("workflow") or base_workflow)
    agent_overrides = _normalize_agents(raw_reply.get("agents") or agents)
    reply = str(raw_reply.get("reply") or "Workflow draft updated.")

    definition = workflow_from_dict(workflow)
    validate_managed_workflow_definition(definition)
    workflow = _definition_to_payload(definition)
    return {
        "ok": True,
        "project_id": project_id,
        "source": "llm",
        "reply": reply,
        "workflow": workflow,
        "agents": agent_overrides,
        "warnings": [],
        "summary": _workflow_summary(definition, agent_overrides),
    }


def _ask_llm(
    *,
    project_id: str,
    messages: list[dict[str, Any]],
    base_workflow: dict[str, Any],
    agents: dict[str, Any],
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
    obj = get_llm_client("project").chat_json(prompt)
    if not isinstance(obj, dict):
        raise ValueError("workflow designer LLM returned non-object JSON")
    return obj


def _workflow_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("columns"):
        return value
    return {"name": "project-workflow", "version": 1, "columns": [], "actions": {}}


def _normalize_workflow(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("workflow designer LLM did not return a workflow object")
    workflow = dict(value)
    workflow["name"] = str(workflow.get("name") or "project-workflow")
    workflow["version"] = int(workflow.get("version") or 1)
    workflow["columns"] = _normalize_columns(workflow.get("columns"))
    workflow["actions"] = _normalize_actions(workflow.get("actions"), workflow["columns"])
    return workflow


def _normalize_columns(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("workflow.columns must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        key = _status_key(raw.get("status_key"))
        if not key or key in seen:
            continue
        seen.add(key)
        col = {
            "status_key": key,
            "title": str(raw.get("title") or key.replace("_", " ").title()),
            "position": int(raw.get("position") or (index + 1) * 10),
            "transition_to": [_status_key(item) for item in raw.get("transition_to") or [] if _status_key(item)],
        }
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
        out.append(col)
    if not out:
        raise ValueError("workflow must define project-specific columns")
    return sorted(out, key=lambda item: int(item.get("position") or 0))


def _normalize_actions(value: object, columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    known = {str(col.get("status_key") or "") for col in columns}
    actions = dict(value) if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, raw in actions.items():
        if not isinstance(raw, dict):
            continue
        target = _status_key(raw.get("to"))
        if target in known:
            normalized[_status_key(key)] = {"to": target}
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
    return normalized


def _definition_to_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "version": definition.version,
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
