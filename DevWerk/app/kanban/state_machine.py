from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.kanban.definition import (
    ActionKind,
    TerminalKind,
    WorkflowColumn,
    WorkflowDefinition,
    canonical_workflow_key,
)


class TaskRunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CLIENT = "waiting_client"
    WAITING_USER = "waiting_user"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class TransitionDecision:
    action: str
    from_status: str
    to_status: str
    action_kind: str
    terminal: bool
    terminal_kind: str | None
    reason: str = ""


class WorkflowStateMachine:
    """Definition-driven workflow state machine.

    The state machine never gives special meaning to column names. Completion,
    failure, retry, waiting, and runtime handling must be declared in the
    workflow definition through column metadata and actions.
    """

    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition

    def terminal_kind(self, status_key: str) -> str | None:
        column = self.definition.column(status_key)
        if column is None or not column.terminal:
            return None
        return column.terminal_kind

    def is_terminal(self, status_key: str) -> bool:
        return self.terminal_kind(status_key) is not None

    def is_success_terminal(self, status_key: str) -> bool:
        return self.terminal_kind(status_key) == TerminalKind.SUCCESS.value

    def is_failure_terminal(self, status_key: str) -> bool:
        return self.terminal_kind(status_key) == TerminalKind.FAILURE.value

    def decide(self, current_status: str, action: str, payload: dict[str, Any] | None = None) -> TransitionDecision:
        from_status = canonical_workflow_key(current_status)
        action_key = canonical_workflow_key(action)
        if not from_status:
            raise ValueError("workflow transition requires current status")
        if not action_key:
            raise ValueError("workflow transition requires action")
        current = self.definition.column(from_status)
        if current is None:
            raise ValueError(f"workflow current status {from_status!r} is not defined")
        rule = self.definition.action(action_key)
        if rule is None:
            raise ValueError(f"unknown workflow action: {action_key}")
        kind = self.definition.action_kind(action_key)
        if current.terminal and kind != ActionKind.RETRY.value:
            raise ValueError(f"workflow task is already terminal at {from_status!r}")
        to_status = canonical_workflow_key(rule.get("to"))
        if not to_status:
            raise ValueError(f"workflow action {action_key!r} has no target status")
        target = self.definition.column(to_status)
        if target is None:
            raise ValueError(f"workflow action {action_key!r} targets unknown status {to_status!r}")
        if kind == ActionKind.RETRY.value and to_status == from_status:
            allowed = True
        else:
            allowed = self._target_allowed(current, to_status, action_key, rule)
        if not allowed:
            raise ValueError(
                f"workflow action {action_key!r} cannot move from {from_status!r} to {to_status!r}"
            )
        return TransitionDecision(
            action=action_key,
            from_status=from_status,
            to_status=to_status,
            action_kind=kind,
            terminal=target.terminal,
            terminal_kind=target.terminal_kind,
            reason=str((payload or {}).get("reason") or rule.get("reason") or ""),
        )

    def next_executable_column(self, status_key: str) -> WorkflowColumn | None:
        anchor = self.definition.column(status_key)
        if anchor is None or anchor.terminal:
            return None
        candidates = [self.definition.column(item) for item in anchor.transition_to]
        for column in [item for item in candidates if item is not None]:
            if column.terminal:
                continue
            if column.executable:
                return column
            nested = self.next_executable_column(column.status_key)
            if nested is not None:
                return nested
        return None

    def failure_action_for(self, column: WorkflowColumn | None = None) -> str:
        if column and column.failure_actions:
            for action in column.failure_actions:
                if self.definition.action_kind(action) == ActionKind.FAILURE.value:
                    return action
            return column.failure_actions[0]
        for action, rule in self.definition.actions.items():
            target = self.definition.column(canonical_workflow_key(rule.get("to")))
            if target and target.is_terminal_failure:
                return action
        raise ValueError("workflow has no declared failure action")

    def retry_action(self, current_status: str = "") -> str | None:
        current = self.definition.column(canonical_workflow_key(current_status)) if current_status else None
        for action in self.definition.actions:
            if self.definition.action_kind(action) != ActionKind.RETRY.value:
                continue
            rule = self.definition.action(action) or {}
            if current is None or self._target_allowed(current, canonical_workflow_key(rule.get("to")), action, rule):
                return action
        return None

    def success_action_for(self, column: WorkflowColumn) -> str:
        if column.success_action:
            return column.success_action
        for action, rule in self.definition.actions.items():
            if canonical_workflow_key(rule.get("from")) == column.status_key:
                return action
        raise ValueError(f"workflow column {column.status_key!r} has no declared success action")

    def _target_allowed(
        self,
        current: WorkflowColumn,
        target_status: str,
        action: str,
        rule: dict[str, Any],
    ) -> bool:
        if target_status in set(current.transition_to):
            return True
        if action in current.declared_actions():
            return True
        from_rule = canonical_workflow_key(rule.get("from") or rule.get("from_status"))
        if from_rule and from_rule == current.status_key:
            return True
        return False
