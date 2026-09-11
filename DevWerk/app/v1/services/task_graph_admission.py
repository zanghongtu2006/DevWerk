from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.v1.domain import (
    OrderedTaskDependencyContract,
    TaskAdmissionConstraint,
    TaskContractValueReference,
    TaskPlan,
)
from app.v1.task_identity import resolve_input_pointer


@dataclass(frozen=True)
class ExistingTaskOrder:
    task_id: str
    status: str
    value: int | str


def validate_task_graph_admission(
    plan: TaskPlan,
    dependency_contract: OrderedTaskDependencyContract | None,
    admission_constraints: list[TaskAdmissionConstraint],
    *,
    loop_bindings: dict[str, Any],
    existing_orders: list[ExistingTaskOrder],
) -> None:
    """Validate Project scope and ordered graph shape before any plan is stored."""
    for task in plan.tasks:
        validate_task_scope(
            task.proposed_task_ref,
            task.input,
            admission_constraints,
            loop_bindings=loop_bindings,
        )
    if dependency_contract is None:
        return

    proposed: list[tuple[int | str, str, set[str]]] = []
    for task in plan.tasks:
        try:
            value = resolve_input_pointer(task.input, dependency_contract.order_pointer)
        except ValueError as exc:
            raise ValueError(
                f"task {task.proposed_task_ref!r} cannot resolve dependency order "
                f"pointer {dependency_contract.order_pointer!r}"
            ) from exc
        _validate_order_value(value, dependency_contract, task.proposed_task_ref)
        proposed.append((value, task.proposed_task_ref, set(task.dependencies)))

    value_types = {type(item.value) for item in existing_orders} | {type(item[0]) for item in proposed}
    if len(value_types) > 1:
        raise ValueError("ordered Task dependency values must use one scalar type across the Project")
    proposed.sort(key=lambda item: item[0])
    existing_values = sorted({item.value for item in existing_orders})
    proposed_values = [item[0] for item in proposed]
    if len(proposed_values) != len(set(proposed_values)):
        raise ValueError("ordered Task dependency values must be unique within a Task Plan")
    overlap = sorted(set(proposed_values) & set(existing_values))
    if overlap:
        raise ValueError(f"ordered Task dependency values already exist in the Project: {overlap}")

    if dependency_contract.continuity == "contiguous_integer":
        first = dependency_contract.first_value
        if not isinstance(first, int) or isinstance(first, bool):
            raise ValueError("contiguous_integer Task dependency requires an integer first_value")
        next_value = first
        for value in existing_values:
            if value == next_value:
                next_value += 1
            elif value > next_value:
                break
        expected = list(range(next_value, next_value + len(proposed)))
        if proposed_values != expected:
            raise ValueError(
                "ordered Task dependency must continue the Project Task graph from "
                f"{next_value}: expected {expected}, got {proposed_values}"
            )
    elif existing_values and proposed_values and proposed_values[0] <= existing_values[-1]:
        raise ValueError(
            "ordered Task dependency values must be greater than the current Project graph tail "
            f"{existing_values[-1]!r}"
        )

    for index, (_, task_ref, actual_dependencies) in enumerate(proposed):
        expected_dependencies = set() if index == 0 else {proposed[index - 1][1]}
        if actual_dependencies != expected_dependencies:
            raise ValueError(
                f"task {task_ref!r} must depend exactly on its ordered predecessor: "
                f"expected {sorted(expected_dependencies)}, got {sorted(actual_dependencies)}"
            )


def validate_task_scope(
    task_ref: str,
    task_input: dict[str, Any],
    admission_constraints: list[TaskAdmissionConstraint],
    *,
    loop_bindings: dict[str, Any],
) -> None:
    """Recheck immutable Project scope when a planned Task is materialized."""
    for constraint in admission_constraints:
        left = _resolve_reference(constraint.left, task_input, loop_bindings)
        right = _resolve_reference(constraint.right, task_input, loop_bindings)
        if not _compare(left, constraint.operator, right):
            raise ValueError(
                f"task {task_ref!r} violates Project Task admission: "
                f"{constraint.message} (left={left!r}, operator={constraint.operator!r}, right={right!r})"
            )
def existing_task_orders(store: Any, project_id: str, contract: OrderedTaskDependencyContract | None) -> list[ExistingTaskOrder]:
    if contract is None:
        return []
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,status,input_json FROM v1_tasks WHERE project_id=? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    by_value: dict[int | str, ExistingTaskOrder] = {}
    for row in rows:
        try:
            value = resolve_input_pointer(json.loads(row[2] or "{}"), contract.order_pointer)
            if (
                contract.continuity == "contiguous_integer"
                and isinstance(value, str)
                and value.strip().lstrip("-").isdigit()
            ):
                value = int(value.strip())
            _validate_order_value(value, contract, str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        by_value[value] = ExistingTaskOrder(task_id=str(row[0]), status=str(row[1]), value=value)
    return list(by_value.values())


def immediate_predecessor(orders: list[ExistingTaskOrder], value: int | str) -> ExistingTaskOrder | None:
    candidates = [item for item in orders if type(item.value) is type(value) and item.value < value]
    return max(candidates, key=lambda item: item.value) if candidates else None


def _resolve_reference(reference: TaskContractValueReference, task_input: dict[str, Any], loop_bindings: dict[str, Any]) -> Any:
    if reference.source == "literal":
        return reference.value
    source = task_input if reference.source == "task_input" else loop_bindings
    try:
        return resolve_input_pointer(source, str(reference.pointer))
    except ValueError as exc:
        raise ValueError(
            f"Task admission cannot resolve {reference.source} pointer {reference.pointer!r}"
        ) from exc


def _compare(left: Any, operator: str, right: Any) -> bool:
    try:
        if operator == "eq":
            return left == right
        if operator == "ne":
            return left != right
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "in":
            return left in right
    except TypeError as exc:
        raise ValueError(
            f"Task admission values cannot be compared with {operator!r}: {left!r}, {right!r}"
        ) from exc
    raise ValueError(f"unknown Task admission operator: {operator!r}")


def _validate_order_value(value: Any, contract: OrderedTaskDependencyContract, label: str) -> None:
    allowed = isinstance(value, (int, str)) and not isinstance(value, bool)
    if not allowed:
        raise ValueError(f"task {label!r} dependency order value must be an integer or string")
    if contract.continuity == "contiguous_integer" and not isinstance(value, int):
        raise ValueError(f"task {label!r} dependency order value must be an integer")
