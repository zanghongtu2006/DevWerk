from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_KEY_ALIASES = {
    "readytoapply": "ready_to_apply",
    "ready_toapply": "ready_to_apply",
    "readyto_apply": "ready_to_apply",
    "codeready": "code_ready",
    "applysucceeded": "apply_succeeded",
    "applyok": "apply_succeeded",
    "verificationpassed": "verification_passed",
    "verificationfailed": "verification_failed",
    "workflowdone": "workflow_done",
    "inprogress": "in_progress",
    "requestrework": "request_rework",
    "requestreplan": "request_replan",
}


class WorkflowAction:
    REQUEST_REPLAN = "request_replan"
    REQUEST_REWORK = "request_rework"
    APPROVE = "approve"
    FAIL = "fail"
    NEED_CLIENT_TOOL = "need_client_tool"
    APPLY_RESULT = "apply_result"
    RETRY = "retry"
    ABANDON = "abandon"
    APPLY_SUCCEEDED = "apply_succeeded"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    WORKFLOW_DONE = "workflow_done"
    CODE_READY = "code_ready"


class WorkflowStatus:
    READY_TO_APPLY = "ready_to_apply"
    DONE = "done"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"


def canonical_workflow_key(value: object) -> str:
    """Normalize model/user-facing workflow keys at the state-machine boundary."""

    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()
    compact = text.replace("_", "")
    return _KEY_ALIASES.get(text) or _KEY_ALIASES.get(compact) or text


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
    workflow_type: str = ""
    requires_apply: bool = False
    parameters: dict[str, Any] | None = None

    @property
    def is_coding(self) -> bool:
        return self.requires_apply or self.workflow_type == "coding"

    def column(self, status_key: str) -> WorkflowColumn | None:
        key = canonical_workflow_key(status_key)
        return next((col for col in self.columns if col.status_key == key), None)

    def action(self, action: str) -> dict[str, Any] | None:
        return self.actions.get(canonical_workflow_key(action))

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
        status_key = canonical_workflow_key(raw.get("status_key"))
        if not status_key:
            continue
        columns.append(
            WorkflowColumn(
                status_key=status_key,
                title=str(raw.get("title") or status_key).strip(),
                position=int(raw.get("position") or (len(columns) + 1) * 10),
                transition_to=[canonical_workflow_key(item) for item in raw.get("transition_to") or [] if canonical_workflow_key(item)],
                job_template=_none_if_blank(raw.get("job_template")),
                input_artifacts=[str(item).strip() for item in raw.get("input_artifacts") or [] if str(item).strip()],
                output_artifact=_none_if_blank(raw.get("output_artifact")),
                success_action=_none_if_blank(canonical_workflow_key(raw.get("success_action"))),
                failure_actions=[canonical_workflow_key(item) for item in raw.get("failure_actions") or [] if canonical_workflow_key(item)],
                context_policy=raw.get("context_policy") if isinstance(raw.get("context_policy"), dict) else {},
            )
        )
    actions = value.get("actions") if isinstance(value.get("actions"), dict) else {}
    normalized_actions: dict[str, dict[str, Any]] = {}
    for key, val in actions.items():
        if not isinstance(val, dict):
            continue
        action_key = canonical_workflow_key(key)
        rule = dict(val)
        if "to" in rule:
            rule["to"] = canonical_workflow_key(rule.get("to"))
        elif "target" in rule:
            rule["to"] = canonical_workflow_key(rule.get("target"))
        elif "target_status" in rule:
            rule["to"] = canonical_workflow_key(rule.get("target_status"))
        if action_key:
            normalized_actions[action_key] = rule
    return WorkflowDefinition(
        name=str(value.get("name") or "default"),
        version=int(value.get("version") or 1),
        columns=columns,
        actions=normalized_actions,
        workflow_type=canonical_workflow_key(value.get("workflow_type")),
        requires_apply=bool(value.get("requires_apply", False)),
        parameters=value.get("parameters") if isinstance(value.get("parameters"), dict) else {},
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
        target = canonical_workflow_key(rule.get("to"))
        if not target:
            raise ValueError(f"workflow action {action!r} has no target status")
        if target not in known:
            raise ValueError(f"workflow action {action!r} references unknown target {target!r}")

    success_targets = {
        canonical_workflow_key((definition.action(action) or {}).get("to"))
        for action in ("workflow_done", "complete", "completed")
    }
    success_targets.discard("")
    if not success_targets:
        raise ValueError(
            "managed workflow requires an explicit success action: workflow_done, complete, or completed"
        )

    for action in ("fail", "abandon"):
        rule = definition.action(action)
        target = canonical_workflow_key((rule or {}).get("to"))
        if rule is None or target not in known:
            raise ValueError(f"managed workflow action {action!r} must target an existing terminal/failure column")

    retry = definition.action("retry")
    retry_target = canonical_workflow_key((retry or {}).get("to"))
    terminal_targets = {
        canonical_workflow_key((definition.action(action) or {}).get("to"))
        for action in ("workflow_done", "complete", "fail", "abandon")
    }
    terminal_targets.discard("")
    if not retry_target or retry_target in terminal_targets:
        raise ValueError("managed workflow action 'retry' must target a non-terminal column")

    if definition.is_coding:
        _validate_coding_lifecycle(definition, known)


def _none_if_blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _validate_coding_lifecycle(definition: WorkflowDefinition, known: set[str]) -> None:
    required_actions = (
        "code_ready",
        "apply_succeeded",
        "verification_failed",
        "workflow_done",
        "fail",
        "abandon",
        "retry",
    )
    targets: dict[str, str] = {}
    for action in required_actions:
        rule = definition.action(action)
        target = canonical_workflow_key((rule or {}).get("to"))
        if rule is None or not target:
            raise ValueError(f"coding workflow missing lifecycle action: {action}")
        if target not in known:
            raise ValueError(f"coding workflow action {action!r} references unknown target {target!r}")
        targets[action] = target

    if targets["code_ready"] in {targets["workflow_done"], targets["fail"], targets["abandon"]}:
        raise ValueError("coding workflow action 'code_ready' must target a non-terminal apply gate column")

    retry_target = targets["retry"]
    if retry_target in {targets["workflow_done"], targets["fail"], targets["abandon"]}:
        raise ValueError("coding workflow action 'retry' must not target terminal lifecycle columns")

    for column in definition.columns:
        if not column.executable:
            continue
        if workflow_column_can_produce_code(column):
            if column.success_action != "code_ready":
                raise ValueError(
                    f"coding workflow code-producing column {column.status_key!r} "
                    "must use success_action='code_ready'"
                )
            success_target = canonical_workflow_key((definition.action(column.success_action or "") or {}).get("to"))
            if success_target != targets["code_ready"]:
                raise ValueError(
                    f"coding workflow code-producing column {column.status_key!r} "
                    f"must transition to {targets['code_ready']!r} via code_ready"
                )


def workflow_column_can_produce_code(column: WorkflowColumn) -> bool:
    policy = column.context_policy if isinstance(column.context_policy, dict) else {}
    status = str(column.status_key or "").lower()
    if status == "code_ready":
        return False
    if policy.get("output_contract") != "code_change" and status.startswith("ready_to_"):
        return False
    if policy.get("output_contract") != "code_change" and any(
        token in status for token in ("ready_to_apply", "apply", "verify", "done", "fail", "abandon", "retry")
    ):
        return False
    output = str(column.output_artifact or "").lower()
    template = str(column.job_template or "").lower()
    if policy.get("requires_apply") is True or policy.get("output_contract") == "code_change":
        return True
    if any(token in status for token in ("implement", "coding", "code", "patch", "scaffold")):
        return True
    return any(token in template for token in ("code", "patch", "repair", "change", "implement", "generate", "scaffold")) or any(
        token in output for token in ("code", "patch", "repair")
    )
