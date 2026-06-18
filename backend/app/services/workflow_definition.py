from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class WorkflowColumn:
    status_key: str
    title: str
    position: int
    transition_to: list[str]
    agent: str | None = None
    input_artifacts: list[str] | None = None
    output_artifact: str | None = None
    success_action: str | None = None
    failure_actions: list[str] | None = None
    context_policy: dict[str, Any] | None = None

    @property
    def executable(self) -> bool:
        return bool(self.agent)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: int
    columns: list[WorkflowColumn]
    actions: dict[str, dict[str, Any]]

    def column(self, status_key: str) -> WorkflowColumn | None:
        key = str(status_key or "").strip().lower()
        return next((col for col in self.columns if col.status_key == key), None)

    def action(self, action: str) -> dict[str, Any] | None:
        return self.actions.get(str(action or "").strip().lower().replace("-", "_"))

    def columns_for_kanban(self) -> list[dict[str, Any]]:
        return [
            {
                "status_key": col.status_key,
                "title": col.title,
                "position": col.position,
                "transition_to": col.transition_to,
            }
            for col in self.columns
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "columns": [
                {
                    "status_key": col.status_key,
                    "title": col.title,
                    "agent": col.agent,
                    "success_action": col.success_action,
                    "output_artifact": col.output_artifact,
                }
                for col in self.columns
            ],
        }


def default_workflow_definition() -> WorkflowDefinition:
    path = _default_workflow_path()
    if path.is_file():
        return workflow_from_dict(json.loads(path.read_text(encoding="utf-8")))
    return workflow_from_dict(_embedded_default_workflow())


def workflow_from_dict(value: dict[str, Any]) -> WorkflowDefinition:
    columns = []
    for raw in value.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        status_key = str(raw.get("status_key") or "").strip().lower()
        if not status_key:
            continue
        columns.append(
            WorkflowColumn(
                status_key=status_key,
                title=str(raw.get("title") or status_key).strip(),
                position=int(raw.get("position") or (len(columns) + 1) * 10),
                transition_to=[str(item).strip().lower() for item in raw.get("transition_to") or [] if str(item).strip()],
                agent=_none_if_blank(raw.get("agent")),
                input_artifacts=[str(item).strip() for item in raw.get("input_artifacts") or [] if str(item).strip()],
                output_artifact=_none_if_blank(raw.get("output_artifact")),
                success_action=_none_if_blank(raw.get("success_action")),
                failure_actions=[str(item).strip().lower() for item in raw.get("failure_actions") or [] if str(item).strip()],
                context_policy=raw.get("context_policy") if isinstance(raw.get("context_policy"), dict) else {},
            )
        )
    actions = value.get("actions") if isinstance(value.get("actions"), dict) else {}
    return WorkflowDefinition(
        name=str(value.get("name") or "default"),
        version=int(value.get("version") or 1),
        columns=columns or [WorkflowColumn(**col) for col in _fallback_columns()],
        actions={str(key).strip().lower().replace("-", "_"): val for key, val in actions.items() if isinstance(val, dict)},
    )


def default_columns() -> list[dict[str, Any]]:
    return default_workflow_definition().columns_for_kanban()


def _default_workflow_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "config" / "workflows" / "default.json"


def _none_if_blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _fallback_columns() -> list[dict[str, Any]]:
    return [
        {"status_key": "draft", "title": "Draft", "position": 10, "transition_to": ["context_indexed", "failed"]},
        {
            "status_key": "context_indexed",
            "title": "Context Indexed",
            "position": 20,
            "transition_to": ["planned", "failed"],
            "agent": "context",
            "input_artifacts": ["workflow_request"],
            "output_artifact": "context_bundle",
            "success_action": "context_indexed",
            "failure_actions": ["fail"],
            "context_policy": {"use_workspace": True, "use_project_memory": True},
        },
        {
            "status_key": "planned",
            "title": "Planned",
            "position": 30,
            "transition_to": ["coding", "draft", "failed"],
            "agent": "planner",
            "input_artifacts": ["context_bundle"],
            "output_artifact": "plan_bundle",
            "success_action": "plan_ready",
            "failure_actions": ["request_replan", "fail"],
            "context_policy": {"use_workspace": True, "use_project_memory": True},
        },
        {
            "status_key": "coding",
            "title": "Coding",
            "position": 40,
            "transition_to": ["reviewed", "planned", "failed"],
            "agent": "coder",
            "input_artifacts": ["context_bundle", "plan_bundle"],
            "output_artifact": "code_change_bundle",
            "success_action": "coding_ready",
            "failure_actions": ["request_replan", "fail"],
            "context_policy": {"use_workspace": True, "use_project_memory": True},
        },
        {
            "status_key": "reviewed",
            "title": "Reviewed",
            "position": 45,
            "transition_to": ["ready_to_apply", "coding", "planned", "failed"],
            "agent": "reviewer",
            "input_artifacts": ["plan_bundle", "code_change_bundle"],
            "output_artifact": "review_bundle",
            "success_action": "approve",
            "failure_actions": ["request_recoding", "request_replan", "fail"],
            "context_policy": {"use_project_memory": True},
        },
        {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 50, "transition_to": ["applied", "coding", "failed"]},
        {"status_key": "applied", "title": "Applied", "position": 60, "transition_to": ["verified", "coding", "planned", "failed"]},
        {"status_key": "verified", "title": "Verified", "position": 70, "transition_to": ["done", "applied", "failed"]},
        {"status_key": "done", "title": "Done", "position": 80, "transition_to": []},
        {"status_key": "failed", "title": "Failed", "position": 90, "transition_to": ["draft"]},
    ]


def _embedded_default_workflow() -> dict[str, Any]:
    return {
        "name": "default",
        "version": 1,
        "columns": _fallback_columns(),
        "actions": {
            "context_indexed": {"to": "context_indexed"},
            "plan_ready": {"to": "planned"},
            "coding_started": {"to": "coding"},
            "coding_ready": {"to": "reviewed"},
            "approve": {"to": "ready_to_apply"},
            "ready_to_apply": {"to": "ready_to_apply"},
            "apply_succeeded": {"to": "applied"},
            "verification_passed": {"to": "verified"},
            "verification_failed": {"to": "coding"},
            "workflow_done": {"to": "done"},
            "request_recoding": {"to": "coding"},
            "request_replan": {"to": "planned"},
            "fail": {"to": "failed"},
            "retry": {"to": "draft"},
            "abandon": {"to": "failed"},
        },
    }
