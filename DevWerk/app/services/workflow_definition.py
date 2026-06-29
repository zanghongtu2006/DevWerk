from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowColumn:
    status_key: str
    title: str
    position: int
    transition_to: list[str]
    job_template: str | None = None
    input_artifacts: list[str] | None = None
    output_artifact: str | None = None
    success_action: str | None = None
    failure_actions: list[str] | None = None
    context_policy: dict[str, Any] | None = None

    @property
    def executable(self) -> bool:
        return bool(self.job_template)


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
                    "job_template": col.job_template,
                    "success_action": col.success_action,
                    "output_artifact": col.output_artifact,
                }
                for col in self.columns
            ],
        }


def empty_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(name="unconfigured", version=1, columns=[], actions={})


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
                job_template=_none_if_blank(raw.get("job_template")),
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
        columns=columns,
        actions={str(key).strip().lower().replace("-", "_"): val for key, val in actions.items() if isinstance(val, dict)},
    )


def validate_managed_workflow_definition(definition: WorkflowDefinition) -> None:
    """Validate the lifecycle contract required by the persistent workflow supervisor."""
    keys = [column.status_key for column in definition.columns]
    known = set(keys)
    if len(keys) != len(known):
        raise ValueError("workflow column status_key values must be unique")

    if not keys:
        raise ValueError("managed workflow requires at least one column")

    for column in definition.columns:
        unknown = set(column.transition_to) - known
        if unknown:
            raise ValueError(
                f"workflow column {column.status_key!r} references unknown transitions: {sorted(unknown)}"
            )
    for action, rule in definition.actions.items():
        target = str(rule.get("to") or "").strip().lower()
        if not target:
            raise ValueError(f"workflow action {action!r} has no target status")
        if target not in known:
            raise ValueError(f"workflow action {action!r} references unknown target {target!r}")

    for action in ("fail", "abandon"):
        rule = definition.action(action)
        target = str((rule or {}).get("to") or "").strip().lower()
        if rule is None or target not in known:
            raise ValueError(f"managed workflow action {action!r} must target an existing terminal/failure column")

    retry = definition.action("retry")
    retry_target = str((retry or {}).get("to") or "").strip().lower()
    terminal_targets = {
        str((definition.action(action) or {}).get("to") or "").strip().lower()
        for action in ("workflow_done", "complete", "fail", "abandon")
    }
    terminal_targets.discard("")
    if not retry_target or retry_target in terminal_targets:
        raise ValueError("managed workflow action 'retry' must target a non-terminal column")


def _none_if_blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
