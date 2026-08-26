from __future__ import annotations

import hashlib
import json
from typing import Any

from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import LinearTaskDependencyContract, TaskPlan, WorkflowPlan
from app.v1.repositories.base import StoreHost
from app.v1.storage_support import new_id, utcnow


class PlanningRepository:
    """Immutable Workflow-method and concrete Task-plan persistence."""

    def __init__(self, store: StoreHost):
        self.store = store

    def create_workflow_plan(self, project_id: str, plan: WorkflowPlan) -> dict[str, Any]:
        self.store.get_project(project_id)
        payload = plan.model_dump_json()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT id FROM v1_workflow_plans WHERE project_id=? AND plan_hash=?",
                (project_id, digest),
            ).fetchone()
            plan_id = str(existing[0]) if existing else new_id("wfplan")
            if not existing:
                db.execute(
                    "INSERT INTO v1_workflow_plans(id,project_id,schema_version,plan_json,plan_hash,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (plan_id, project_id, plan.schema_version, payload, digest, now),
                )
                self.store._event(
                    db,
                    project_id,
                    None,
                    None,
                    "workflow.plan_created",
                    {"workflow_plan_id": plan_id, "plan_hash": digest},
                )
        return self.get_workflow_plan(project_id, plan_id)

    def get_workflow_plan(self, project_id: str, plan_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = self.store._dict(db.execute(
                "SELECT * FROM v1_workflow_plans WHERE id=? AND project_id=?",
                (plan_id, project_id),
            ).fetchone())
        if not row:
            raise KeyError(plan_id)
        row["plan"] = json.loads(row.pop("plan_json"))
        return row

    def list_workflow_plans(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_workflow_plans WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, min(max(limit, 1), self.store.policy.service_limits.max_page_size)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for item in rows:
            row = dict(item)
            row["plan"] = json.loads(row.pop("plan_json"))
            result.append(row)
        return result

    def create_task_plan(self, project_id: str, plan: TaskPlan) -> dict[str, Any]:
        self.store.get_project(project_id)
        revision = self.store.get_workflow_revision(project_id, plan.workflow_revision_id)
        workflow = self.store.workflow_by_id(project_id, plan.workflow_revision_id)
        method = WorkflowPlan.model_validate(
            self.get_workflow_plan(project_id, str(revision["workflow_plan_id"]))["plan"]
        )
        plan = plan.model_copy(deep=True)
        for item in plan.tasks:
            item.input = canonicalize_contract_value(
                item.input,
                method.task_contract.input_schema,
            )
            item.validate_agent_execution_workflow(workflow)
            item.validate_exact_input_workflow(workflow)
            validate_contract(
                item.input,
                method.task_contract.input_schema,
                label=f"Task Plan {item.proposed_task_ref} input",
            )
        _validate_task_dependency_contract(plan, method.task_contract.dependency_contract)
        payload = plan.model_dump_json()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT id FROM v1_task_plans WHERE project_id=? AND plan_hash=?",
                (project_id, digest),
            ).fetchone()
            plan_id = str(existing[0]) if existing else new_id("tplan")
            if not existing:
                db.execute(
                    "INSERT INTO v1_task_plans(id,project_id,workflow_revision_id,schema_version,objective,plan_json,plan_hash,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        plan_id,
                        project_id,
                        plan.workflow_revision_id,
                        plan.schema_version,
                        plan.objective,
                        payload,
                        digest,
                        now,
                    ),
                )
                self.store._event(
                    db,
                    project_id,
                    None,
                    None,
                    "task.plan_created",
                    {
                        "task_plan_id": plan_id,
                        "workflow_revision_id": plan.workflow_revision_id,
                        "task_refs": [item.proposed_task_ref for item in plan.tasks],
                    },
                )
        return self.get_task_plan(project_id, plan_id)

    def get_task_plan(self, project_id: str, plan_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = self.store._dict(db.execute(
                "SELECT * FROM v1_task_plans WHERE id=? AND project_id=?",
                (plan_id, project_id),
            ).fetchone())
        if not row:
            raise KeyError(plan_id)
        row["plan"] = json.loads(row.pop("plan_json"))
        return row

    def list_task_plans(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_task_plans WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, min(max(limit, 1), self.store.policy.service_limits.max_page_size)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for item in rows:
            row = dict(item)
            row["plan"] = json.loads(row.pop("plan_json"))
            result.append(row)
        return result


def _validate_task_dependency_contract(
    plan: TaskPlan,
    contract: LinearTaskDependencyContract | None,
) -> None:
    if contract is None:
        return
    ordered: list[tuple[int, str, set[str]]] = []
    for task in plan.tasks:
        value: Any = task.input
        for raw_token in contract.order_pointer[1:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if not isinstance(value, dict) or token not in value:
                raise ValueError(
                    f"task {task.proposed_task_ref!r} cannot resolve dependency order "
                    f"pointer {contract.order_pointer!r}"
                )
            value = value[token]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"task {task.proposed_task_ref!r} dependency order value must be an integer"
            )
        ordered.append((value, task.proposed_task_ref, set(task.dependencies)))
    ordered.sort(key=lambda item: item[0])
    values = [item[0] for item in ordered]
    expected_values = list(range(contract.first_value, contract.first_value + len(ordered)))
    if values != expected_values:
        raise ValueError(
            "linear Task dependency order must be contiguous from "
            f"{contract.first_value}: expected {expected_values}, got {values}"
        )
    for index, (_, task_ref, actual_dependencies) in enumerate(ordered):
        expected_dependencies = set() if index == 0 else {ordered[index - 1][1]}
        if actual_dependencies != expected_dependencies:
            raise ValueError(
                f"task {task_ref!r} must depend exactly on its linear predecessor: "
                f"expected {sorted(expected_dependencies)}, got {sorted(actual_dependencies)}"
            )
