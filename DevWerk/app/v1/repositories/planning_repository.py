from __future__ import annotations

import hashlib
import json
from typing import Any

from app.v1.contracts import validate_contract
from app.v1.domain import TaskPlan, WorkflowPlan
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
        for item in plan.tasks:
            item.validate_agent_execution_workflow(workflow)
            item.validate_exact_input_workflow(workflow)
            validate_contract(
                item.input,
                method.task_contract.input_schema,
                label=f"Task Plan {item.proposed_task_ref} input",
            )
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
