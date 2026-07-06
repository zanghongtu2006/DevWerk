from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class ColumnKind(str, Enum):
    STATE = "state"
    AGENT = "agent"
    RUNTIME = "runtime"
    GATE = "gate"
    TERMINAL = "terminal"


class TerminalKind(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class ActionKind(str, Enum):
    ADVANCE = "advance"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    CANCEL = "cancel"
    WAIT = "wait"
    APPLY = "apply"
    VERIFY = "verify"


def canonical_workflow_key(value: object) -> str:
    """Normalize arbitrary user/model workflow keys without semantic aliasing."""

    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


@dataclass(frozen=True)
class WorkflowColumn:
    status_key: str
    title: str
    position: int
    transition_to: list[str]
    kind: str = ColumnKind.STATE.value
    agent: str | None = None
    runtime: str | None = None
    job_template: str | None = None
    input_artifacts: list[str] | None = None
    output_artifact: str | None = None
    output_contract: str | None = None
    success_action: str | None = None
    failure_actions: list[str] | None = None
    context_policy: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None
    terminal: bool = False
    terminal_kind: str | None = None

    @property
    def executable(self) -> bool:
        return self.kind in {ColumnKind.AGENT.value, ColumnKind.RUNTIME.value} or bool(self.job_template)

    @property
    def is_runtime(self) -> bool:
        return self.kind == ColumnKind.RUNTIME.value or bool(self.runtime)

    @property
    def is_terminal_success(self) -> bool:
        return self.terminal and self.terminal_kind == TerminalKind.SUCCESS.value

    @property
    def is_terminal_failure(self) -> bool:
        return self.terminal and self.terminal_kind == TerminalKind.FAILURE.value

    def declared_actions(self) -> set[str]:
        actions = set(self.failure_actions or [])
        if self.success_action:
            actions.add(self.success_action)
        return {canonical_workflow_key(item) for item in actions if canonical_workflow_key(item)}


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

    def action_target(self, action: str) -> str:
        rule = self.action(action) or {}
        return canonical_workflow_key(rule.get("to"))

    def action_kind(self, action: str) -> str:
        rule = self.action(action) or {}
        kind = canonical_workflow_key(rule.get("kind") or rule.get("type") or rule.get("semantic"))
        if kind:
            return kind
        target = self.column(self.action_target(action))
        if target and target.is_terminal_success:
            return ActionKind.SUCCESS.value
        if target and target.is_terminal_failure:
            return ActionKind.FAILURE.value
        return ActionKind.ADVANCE.value

    def terminal_statuses(self, kind: str | TerminalKind | None = None) -> set[str]:
        if isinstance(kind, TerminalKind):
            expected = kind.value
        else:
            expected = canonical_workflow_key(kind)
        statuses: set[str] = set()
        for column in self.columns:
            if not column.terminal:
                continue
            if expected and column.terminal_kind != expected:
                continue
            statuses.add(column.status_key)
        return statuses

    def start_column(self) -> WorkflowColumn | None:
        if not self.columns:
            return None
        return sorted(self.columns, key=lambda item: item.position)[0]

    def columns_for_kanban(self) -> list[dict[str, Any]]:
        return [
            {
                "status_key": col.status_key,
                "title": col.title,
                "position": col.position,
                "transition_to": col.transition_to,
                "kind": col.kind,
                "terminal": col.terminal,
                "terminal_kind": col.terminal_kind,
            }
            for col in self.columns
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "workflow_type": self.workflow_type,
            "columns": [
                {
                    "status_key": col.status_key,
                    "title": col.title,
                    "kind": col.kind,
                    "agent": col.agent,
                    "runtime": col.runtime,
                    "success_action": col.success_action,
                    "output_artifact": col.output_artifact,
                    "output_contract": col.output_contract,
                    "terminal": col.terminal,
                    "terminal_kind": col.terminal_kind,
                }
                for col in self.columns
            ],
        }


def empty_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(name="unconfigured", version=1, columns=[], actions={})


def workflow_from_dict(value: dict[str, Any]) -> WorkflowDefinition:
    columns: list[WorkflowColumn] = []
    for raw in value.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        status_key = canonical_workflow_key(raw.get("status_key"))
        if not status_key:
            continue
        policy = raw.get("context_policy") if isinstance(raw.get("context_policy"), dict) else {}
        terminal_kind = _terminal_kind(raw, policy)
        terminal = bool(raw.get("terminal") or raw.get("is_terminal") or terminal_kind)
        columns.append(
            WorkflowColumn(
                status_key=status_key,
                title=str(raw.get("title") or status_key).strip(),
                position=int(raw.get("position") or (len(columns) + 1) * 10),
                transition_to=[
                    canonical_workflow_key(item)
                    for item in raw.get("transition_to") or []
                    if canonical_workflow_key(item)
                ],
                kind=_column_kind(raw, policy, terminal),
                agent=_none_if_blank(raw.get("agent") or policy.get("agent")),
                runtime=_none_if_blank(raw.get("runtime") or policy.get("runtime")),
                job_template=_none_if_blank(raw.get("job_template")),
                input_artifacts=[str(item).strip() for item in raw.get("input_artifacts") or [] if str(item).strip()],
                output_artifact=_none_if_blank(raw.get("output_artifact")),
                output_contract=_none_if_blank(raw.get("output_contract") or policy.get("output_contract")),
                success_action=_none_if_blank(canonical_workflow_key(raw.get("success_action"))),
                failure_actions=[
                    canonical_workflow_key(item)
                    for item in raw.get("failure_actions") or []
                    if canonical_workflow_key(item)
                ],
                context_policy=policy,
                retry_policy=raw.get("retry_policy") if isinstance(raw.get("retry_policy"), dict) else {},
                terminal=terminal,
                terminal_kind=terminal_kind,
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
        rule["kind"] = _normalize_action_kind(action_key, rule)
        if action_key:
            normalized_actions[action_key] = rule
    terminal_by_status: dict[str, str] = {}
    for rule in normalized_actions.values():
        target = canonical_workflow_key(rule.get("to"))
        kind = canonical_workflow_key(rule.get("kind"))
        if not target:
            continue
        if kind == ActionKind.SUCCESS.value:
            terminal_by_status.setdefault(target, TerminalKind.SUCCESS.value)
        elif kind in {ActionKind.FAILURE.value, ActionKind.CANCEL.value}:
            terminal_by_status.setdefault(target, TerminalKind.FAILURE.value)
    if terminal_by_status:
        aligned_columns: list[WorkflowColumn] = []
        for column in columns:
            inferred_kind = terminal_by_status.get(column.status_key)
            if inferred_kind and not column.terminal:
                aligned_columns.append(
                    replace(
                        column,
                        terminal=True,
                        terminal_kind=inferred_kind,
                        kind=ColumnKind.TERMINAL.value,
                    )
                )
            else:
                aligned_columns.append(column)
        columns = aligned_columns
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
        for action in column.declared_actions():
            rule = definition.action(action)
            if rule is None:
                raise ValueError(f"workflow column {column.status_key!r} declares unknown action {action!r}")
            target = canonical_workflow_key(rule.get("to"))
            if target not in known:
                raise ValueError(f"workflow action {action!r} references unknown target {target!r}")

    for action, rule in definition.actions.items():
        target = canonical_workflow_key(rule.get("to"))
        if not target:
            raise ValueError(f"workflow action {action!r} has no target status")
        if target not in known:
            raise ValueError(f"workflow action {action!r} references unknown target {target!r}")

    if not definition.terminal_statuses(TerminalKind.SUCCESS):
        raise ValueError("managed workflow requires at least one explicit success terminal column or explicit success action")
    if not definition.terminal_statuses(TerminalKind.FAILURE):
        raise ValueError("managed workflow requires at least one explicit failure terminal column")

    start = definition.start_column()
    if start is None:
        raise ValueError("managed workflow requires a start column")
    unreachable = known - _reachable_from(definition, start.status_key)
    if unreachable:
        raise ValueError(f"workflow has unreachable columns: {sorted(unreachable)}")

    for column in definition.columns:
        if column.terminal:
            continue
        if not _can_reach_terminal(definition, column.status_key):
            raise ValueError(f"workflow column {column.status_key!r} cannot reach any terminal column")


def workflow_column_can_produce_code(column: WorkflowColumn) -> bool:
    policy = column.context_policy if isinstance(column.context_policy, dict) else {}
    return (
        column.output_contract == "code_change"
        or policy.get("output_contract") == "code_change"
        or policy.get("produces") == "code_change"
    )


def _none_if_blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _column_kind(raw: dict[str, Any], policy: dict[str, Any], terminal: bool) -> str:
    kind = canonical_workflow_key(raw.get("kind") or raw.get("column_type") or policy.get("kind"))
    if kind in {item.value for item in ColumnKind}:
        return kind
    if terminal:
        return ColumnKind.TERMINAL.value
    if raw.get("runtime") or policy.get("runtime"):
        return ColumnKind.RUNTIME.value
    if raw.get("job_template") or raw.get("agent") or policy.get("agent"):
        return ColumnKind.AGENT.value
    return ColumnKind.STATE.value


def _terminal_kind(raw: dict[str, Any], policy: dict[str, Any]) -> str | None:
    value = canonical_workflow_key(
        raw.get("terminal_kind")
        or raw.get("terminal_type")
        or raw.get("result")
        or policy.get("terminal_kind")
        or policy.get("terminal_type")
    )
    if value in {"ok", "passed"}:
        value = TerminalKind.SUCCESS.value
    if value in {"error", "blocked"}:
        value = TerminalKind.FAILURE.value
    if value in {TerminalKind.SUCCESS.value, TerminalKind.FAILURE.value}:
        return value
    return None


def _normalize_action_kind(action_key: str, rule: dict[str, Any]) -> str:
    raw = canonical_workflow_key(rule.get("kind") or rule.get("type") or rule.get("semantic"))
    if raw in {"transition", "move", "next"}:
        return ActionKind.ADVANCE.value
    if raw in {"ok", "passed", "pass", "complete", "completed", "done", "finish", "finished"}:
        return ActionKind.SUCCESS.value
    if raw in {"error", "failed", "fail", "blocked"}:
        return ActionKind.FAILURE.value
    if raw in {"rerun", "re_run", "rework"}:
        return ActionKind.RETRY.value
    if raw in {"abort", "abandon"}:
        return ActionKind.CANCEL.value
    if raw in {item.value for item in ActionKind}:
        return raw

    action = canonical_workflow_key(action_key)
    if action in {"success", "succeed", "succeeded", "complete", "completed", "done", "finish", "finished", "workflow_done"}:
        return ActionKind.SUCCESS.value
    if action in {"fail", "failed", "failure", "error"}:
        return ActionKind.FAILURE.value
    if action in {"abandon", "cancel", "abort"}:
        return ActionKind.CANCEL.value
    if action in {"retry", "rerun", "re_run", "rework"}:
        return ActionKind.RETRY.value
    return ActionKind.ADVANCE.value


def _reachable_from(definition: WorkflowDefinition, start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        status = stack.pop()
        if status in seen:
            continue
        seen.add(status)
        column = definition.column(status)
        if column is None:
            continue
        next_statuses = set(column.transition_to)
        for action in column.declared_actions():
            target = definition.action_target(action)
            if target:
                next_statuses.add(target)
        stack.extend(sorted(next_statuses - seen))
    return seen


def _can_reach_terminal(definition: WorkflowDefinition, start: str) -> bool:
    terminal = definition.terminal_statuses()
    return bool(_reachable_from(definition, start) & terminal)
