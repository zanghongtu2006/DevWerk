from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.states import (
    ATTEMPT_STATE_MACHINE,
    COLUMN_RUN_STATE_MACHINE,
    TASK_STATE_MACHINE,
    AttemptStatus,
    ColumnRunStatus,
    TaskStatus,
)
from app.v1.storage_support import utcnow

def _resource_domains_overlap(left: list[Any], right: list[Any]) -> bool:
    for first in left:
        for second in right:
            if not isinstance(first, str) or not isinstance(second, str) or ":" not in first or ":" not in second:
                return True
            first_kind, first_identity = first.split(":", 1)
            second_kind, second_identity = second.split(":", 1)
            if first_kind != second_kind:
                continue
            if not first_identity or not second_identity:
                return True
            if first_kind == "workspace_path":
                a, b = first_identity.rstrip("/"), second_identity.rstrip("/")
                if a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/"):
                    return True
            elif first_identity == second_identity:
                return True
    return False


class SchedulerService:
    def __init__(self, store: StoreHost):
        self.store = store

    def prepare_startup(self, auto_resume_previous_tasks: bool) -> dict[str, Any]:
        if auto_resume_previous_tasks:
            return {
                "auto_resume_previous_tasks": True,
                "paused_tasks": 0,
                "dependency_tasks_released": 0,
                "projects": [],
            }

        now = utcnow()
        interruption = "DevWerk startup paused unfinished Task because automatic resume is disabled"
        with self.store.tx(immediate=True) as db:
            dependency_rows = db.execute(
                "SELECT t.id,t.project_id,t.task_plan_id FROM v1_tasks t "
                "JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.status='pending' AND t.control_state='paused' "
                "AND t.supervision_action='startup_hold' AND s.state='queued'"
            ).fetchall()
            if dependency_rows:
                dependency_ids = [str(row[0]) for row in dependency_rows]
                placeholders = ",".join("?" for _ in dependency_ids)
                db.execute(
                    f"UPDATE v1_tasks SET control_state='active',supervision_action=NULL,"
                    f"state_version=state_version+1,updated_at=? WHERE id IN ({placeholders})",
                    (now, *dependency_ids),
                )
                for row in dependency_rows:
                    self.store._event(
                        db,
                        str(row[1]),
                        str(row[0]),
                        None,
                        "task.startup_hold_released",
                        {
                            "task_plan_id": str(row[2]),
                            "reason": "dependency_queued_tasks_are_workflow_driven",
                        },
                    )

            rows = db.execute(
                "SELECT t.id,t.project_id,t.status,t.current_column,t.task_plan_id FROM v1_tasks t "
                "JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.status IN ('pending','running','waiting','recovering') AND ("
                "(t.control_state='active' AND (t.status!='pending' OR s.state='admitted')) OR "
                "(t.control_state='paused' AND t.supervision_action='startup_hold' AND s.state='admitted')"
                ") ORDER BY t.created_at"
            ).fetchall()
            if not rows:
                projects = sorted({str(row[1]) for row in dependency_rows})
                for project_id in projects:
                    self.store._refresh_projection(db, project_id)
                return {
                    "auto_resume_previous_tasks": False,
                    "paused_tasks": 0,
                    "dependency_tasks_released": len(dependency_rows),
                    "projects": projects,
                }

            for row in rows:
                TASK_STATE_MACHINE.require(row[2], TaskStatus.PENDING)
            task_ids = [str(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in task_ids)
            run_rows = db.execute(
                "SELECT id,status FROM v1_column_runs WHERE status IN ('running','waiting') "
                f"AND task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
            for row in run_rows:
                COLUMN_RUN_STATE_MACHINE.require(row[1], ColumnRunStatus.INTERRUPTED)
            attempt_rows = db.execute(
                "SELECT id,status FROM v1_column_attempts WHERE status IN ('running','waiting') "
                f"AND task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
            for row in attempt_rows:
                ATTEMPT_STATE_MACHINE.require(row[1], AttemptStatus.INTERRUPTED)

            db.execute(
                "UPDATE v1_column_attempts SET status='interrupted',error=COALESCE(error,?),finished_at=COALESCE(finished_at,?) "
                f"WHERE status IN ('running','waiting') AND task_id IN ({placeholders})",
                (interruption, now, *task_ids),
            )
            db.execute(
                "UPDATE v1_column_runs SET status='interrupted',error=COALESCE(error,?),finished_at=COALESCE(finished_at,?) "
                f"WHERE status IN ('running','waiting') AND task_id IN ({placeholders})",
                (interruption, now, *task_ids),
            )
            db.execute(
                "UPDATE v1_await_handles SET status='interrupted',result_json=?,updated_at=? "
                f"WHERE status='pending' AND task_id IN ({placeholders})",
                (json.dumps({"reason": "startup_auto_resume_disabled"}), now, *task_ids),
            )
            db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='paused',supervision_action='startup_hold',"
                "error=NULL,lease_owner=NULL,lease_until=NULL,next_retry_at=NULL,finished_at=NULL,"
                "state_version=state_version+1,updated_at=? "
                f"WHERE id IN ({placeholders})",
                (now, *task_ids),
            )

            projects = sorted({str(row[1]) for row in [*dependency_rows, *rows]})
            for row in rows:
                self.store._event(
                    db,
                    str(row[1]),
                    str(row[0]),
                    None,
                    "task.startup_paused",
                    {
                        "previous_status": str(row[2]),
                        "current_column": str(row[3]),
                        "setting": "workflow.auto_resume_previous_tasks",
                    },
                )
            for project_id in projects:
                self.store._refresh_projection(db, project_id)

        return {
            "auto_resume_previous_tasks": False,
            "paused_tasks": len(rows),
            "dependency_tasks_released": len(dependency_rows),
            "projects": projects,
        }

    def schedule_task(
        self,
        project_id: str,
        task_id: str,
        state: str,
        priority: int,
        wip_group: str | None,
        wip_limit: int | None,
        dependencies: list[str] | None,
        resources: list[str] | None,
    ) -> dict[str, Any]:
        task = self.store.get_project_task(project_id, task_id)
        if state not in {"admitted", "queued", "hold", "cancelled"}:
            raise ValueError("invalid scheduling state")
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT dependencies_json,resources_json,wip_group,wip_limit,auto_admit FROM v1_scheduling_entries WHERE task_id=? AND project_id=?",
                (task_id, project_id),
            ).fetchone()
            if not existing:
                raise ValueError("Task has no scheduling entry")
            if wip_group is None:
                wip_group = str(existing[2])
            if wip_limit is None:
                wip_limit = int(existing[3])
            original_dependencies = list(json.loads(existing[0] or "[]"))
            canonical_dependencies: list[str] = []
            dependencies_changed = False
            for dependency in original_dependencies:
                if not isinstance(dependency, str) or not dependency.startswith("task-plan:"):
                    canonical_dependencies.append(dependency)
                    continue
                _, plan_id, task_ref = dependency.split(":", 2)
                match = db.execute(
                    "SELECT id FROM v1_tasks WHERE project_id=? AND task_plan_id=? "
                    "AND proposed_task_ref=? AND status='done' "
                    "ORDER BY finished_at DESC,created_at DESC LIMIT 1",
                    (project_id, plan_id, task_ref),
                ).fetchone()
                if not match:
                    match = db.execute(
                        "SELECT successor.id FROM v1_tasks predecessor "
                        "JOIN v1_tasks successor ON successor.id=predecessor.resolved_by_task_id "
                        "WHERE predecessor.project_id=? AND predecessor.task_plan_id=? "
                        "AND predecessor.proposed_task_ref=? AND predecessor.status='failed' "
                        "AND successor.project_id=predecessor.project_id AND successor.status='done' "
                        "ORDER BY successor.finished_at DESC,successor.created_at DESC LIMIT 1",
                        (project_id, plan_id, task_ref),
                    ).fetchone()
                if not match:
                    canonical_dependencies.append(dependency)
                    continue
                dependency_id = str(match[0])
                canonical_dependencies.append(dependency_id)
                db.execute(
                    "INSERT OR IGNORE INTO v1_task_dependencies(task_id,depends_on_task_id,project_id,required_terminal,created_at) VALUES(?,?,?,'done',?)",
                    (task_id, dependency_id, project_id, now),
                )
                dependencies_changed = True
            if dependencies_changed:
                db.execute(
                    "UPDATE v1_scheduling_entries SET dependencies_json=?,updated_at=? WHERE task_id=? AND project_id=?",
                    (json.dumps(canonical_dependencies), now, task_id, project_id),
                )
        canonical_resources = list(task["conflict_domains"])
        if (
            dependencies is not None
            and set(dependencies) != set(original_dependencies)
            and set(dependencies) != set(canonical_dependencies)
        ):
            raise ValueError("scheduling dependencies must preserve the Task Plan dependencies")
        if resources is not None and set(resources) != set(canonical_resources):
            raise ValueError("scheduling resources must preserve the Task Plan conflict domains")
        dependencies = canonical_dependencies
        resources = canonical_resources
        auto_admit = int(bool(existing[4]) and state == "queued")
        if state == "admitted":
            with self.store.connect() as db:
                unsatisfied = [
                    dependency
                    for dependency in dependencies
                    if not isinstance(dependency, str)
                    or dependency.startswith("task-plan:")
                    or not (
                        (row := db.execute(
                            "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
                            (dependency, project_id),
                        ).fetchone())
                        and row[0] == "done"
                    )
                ]
            if unsatisfied:
                raise ValueError("dependency_unsatisfied: Task cannot be admitted until all Task Plan dependencies are done")
        with self.store.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_scheduling_entries(task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,created_at,updated_at,auto_admit) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,priority=excluded.priority,wip_group=excluded.wip_group,wip_limit=excluded.wip_limit,dependencies_json=excluded.dependencies_json,resources_json=excluded.resources_json,auto_admit=excluded.auto_admit,updated_at=excluded.updated_at",
                (task_id, project_id, state, priority, wip_group, wip_limit, json.dumps(dependencies), json.dumps(resources), now, now, auto_admit),
            )
            db.execute("UPDATE v1_tasks SET updated_at=? WHERE id=?", (now, task_id))
            self.store._event(db, project_id, task_id, None, "scheduling.decided", {"state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources})
        return {"project_id": project_id, "task_id": task_id, "state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources, "updated_at": now}


    def task_scheduling(self, project_id: str, task_id: str) -> dict[str, Any]:
        task = self.store.get_project_task(project_id, task_id)
        now = utcnow()
        with self.store.connect() as db:
            row = self.store._dict(db.execute(
                "SELECT task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,"
                "auto_admit,created_at,updated_at FROM v1_scheduling_entries WHERE task_id=? AND project_id=?",
                (task_id, project_id),
            ).fetchone())
            if not row:
                raise KeyError(f"no scheduling entry for {task_id}")
            dependencies = json.loads(row.pop("dependencies_json") or "[]")
            dependency_facts: list[dict[str, Any]] = []
            for dependency in dependencies:
                if isinstance(dependency, str) and dependency.startswith("task-plan:"):
                    _, plan_id, task_ref = dependency.split(":", 2)
                    latest = db.execute(
                        "SELECT id,status,resolved_by_task_id FROM v1_tasks "
                        "WHERE project_id=? AND task_plan_id=? AND proposed_task_ref=? "
                        "ORDER BY created_at DESC LIMIT 1",
                        (project_id, plan_id, task_ref),
                    ).fetchone()
                    dependency_facts.append({
                        "reference": dependency,
                        "plan_id": plan_id,
                        "task_ref": task_ref,
                        "resolved_task_id": None,
                        "required_terminal": "done",
                        "latest_task_id": latest[0] if latest else None,
                        "status": latest[1] if latest else "unmaterialized",
                        "resolved_by_task_id": latest[2] if latest else None,
                        "satisfied": False,
                    })
                    continue
                dependency_row = db.execute(
                    "SELECT id,status,proposed_task_ref FROM v1_tasks WHERE id=? AND project_id=?",
                    (dependency, project_id),
                ).fetchone()
                dependency_facts.append({
                    "reference": dependency,
                    "task_ref": dependency_row[2] if dependency_row else None,
                    "resolved_task_id": dependency_row[0] if dependency_row else None,
                    "required_terminal": "done",
                    "status": dependency_row[1] if dependency_row else "missing",
                    "satisfied": bool(dependency_row and dependency_row[1] == "done"),
                })
            eligible = self._dispatch_eligible(db, task_id, now)
        row["dependencies"] = dependency_facts
        row["resources"] = json.loads(row.pop("resources_json") or "[]")
        row["auto_admit"] = bool(row["auto_admit"])
        row["dispatch_eligible"] = eligible
        if task["status"] in {"done", "failed"}:
            pending_reason = "terminal"
        elif task["status"] == "running":
            pending_reason = "running"
        elif task["status"] == "waiting":
            pending_reason = "external_wait"
        elif task.get("control_state") != "active":
            pending_reason = task.get("supervision_action") or task.get("control_state") or "paused"
        elif any(not item["satisfied"] for item in dependency_facts):
            pending_reason = "waiting_dependency"
        elif row["state"] == "queued":
            pending_reason = "explicit_queue"
        elif row["state"] == "hold":
            pending_reason = "hold"
        elif eligible:
            pending_reason = "ready"
        else:
            pending_reason = "waiting_wip_or_resource"
        row["pending_reason"] = pending_reason
        row["blocked_by"] = [
            item for item in dependency_facts if not item["satisfied"]
        ]
        return row


    def task_dependency_context(self, project_id: str, task_id: str) -> list[dict[str, Any]]:
        self.store.get_project_task(project_id, task_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT dep.id,dep.proposed_task_ref,dep.title,dep.status,dep.finished_at,"
                "dep.terminal_artifact_id,a.kind,a.path,a.sha256,a.size "
                "FROM v1_task_dependencies d "
                "JOIN v1_tasks dep ON dep.id=d.depends_on_task_id AND dep.project_id=d.project_id "
                "LEFT JOIN v1_artifacts a ON a.id=dep.terminal_artifact_id AND a.project_id=dep.project_id "
                "WHERE d.task_id=? AND d.project_id=? ORDER BY d.created_at,dep.id",
                (task_id, project_id),
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "task_ref": row[1],
                "title": row[2],
                "status": row[3],
                "finished_at": row[4],
                "terminal_artifact": (
                    {
                        "id": row[5],
                        "kind": row[6],
                        "path": row[7],
                        "sha256": row[8],
                        "size": row[9],
                    }
                    if row[5]
                    else None
                ),
            }
            for row in rows
        ]


    def claim_task(self, task_id: str, owner: str, lease_seconds: int | None = None) -> dict[str, Any] | None:
        now = utcnow()
        lease_seconds = lease_seconds or self.store.policy.scheduling.task_lease_seconds
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.store.tx(immediate=True) as db:
            requested_row = db.execute(
                "SELECT t.project_id,s.resources_json,t.status,t.current_column,t.next_retry_at FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id WHERE t.id=?",
                (task_id,),
            ).fetchone()
            if not requested_row:
                return None
            TASK_STATE_MACHINE.parse(requested_row[2])
            if requested_row[2] in {TaskStatus.PENDING.value, TaskStatus.RECOVERING.value}:
                TASK_STATE_MACHINE.require(requested_row[2], TaskStatus.RUNNING)
            requested = json.loads(requested_row[1] or "[]")
            held_rows = db.execute(
                "SELECT s.resources_json FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.project_id=? AND t.id!=? AND t.status='running'",
                (requested_row[0], task_id),
            ).fetchall()
            if any(_resource_domains_overlap(requested, json.loads(row[0] or "[]")) for row in held_rows):
                return None
            cursor = db.execute(
                "UPDATE v1_tasks SET status='running',lease_owner=?,lease_until=?,next_retry_at=NULL,state_version=state_version+1,updated_at=? WHERE id=? AND status IN ('pending','recovering') AND control_state='active' "
                "AND (status!='recovering' OR next_retry_at IS NULL OR next_retry_at<=?) "
                "AND EXISTS (SELECT 1 FROM v1_scheduling_entries s WHERE s.task_id=v1_tasks.id AND s.project_id=v1_tasks.project_id AND s.state='admitted' "
                "AND NOT EXISTS (SELECT 1 FROM json_each(s.dependencies_json) d LEFT JOIN v1_tasks dep ON dep.id=d.value WHERE dep.id IS NULL OR dep.status!='done') "
                "AND (SELECT COUNT(*) FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id WHERE active.project_id=v1_tasks.project_id AND active.status='running' AND sa.wip_group=s.wip_group)<s.wip_limit)",
                (owner, lease, now, task_id, now),
            )
            if cursor.rowcount != 1:
                return None
            if requested_row[2] == "recovering":
                self.store._event(
                    db,
                    requested_row[0],
                    task_id,
                    None,
                    "task.recovery_started",
                    {"column": requested_row[3], "scheduled_retry_at": requested_row[4]},
                )
        return self.store.get_task(task_id)


    def renew_lease(self, task_id: str, owner: str, lease_seconds: int | None = None) -> bool:
        now = utcnow()
        lease_seconds = lease_seconds or self.store.policy.scheduling.task_lease_seconds
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.store.tx(immediate=True) as db:
            cursor = db.execute(
                "UPDATE v1_tasks SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
                (lease, now, task_id, owner),
            )
            if cursor.rowcount == 1:
                db.execute(
                    "UPDATE v1_column_runs SET heartbeat_at=? WHERE id=(SELECT id FROM v1_column_runs WHERE task_id=? AND status='running' ORDER BY sequence DESC LIMIT 1)",
                    (now, task_id),
                )
        return cursor.rowcount == 1


    def runnable_task_ids(self, limit: int | None = None) -> list[str]:
        limit = limit or self.store.policy.scheduling.runnable_batch_size
        self.store.recovery_manager.recover_expired_task_leases(limit)
        now = utcnow()
        self._resolve_planned_dependencies()
        runnable: list[str] = []
        with self.store.tx(immediate=True) as db:
            rows = db.execute(
                "SELECT t.id,t.status FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.control_state='active' AND t.status IN ('pending','recovering') "
                "AND (t.status!='recovering' OR t.next_retry_at IS NULL OR t.next_retry_at<=?) "
                "ORDER BY s.priority DESC,t.updated_at LIMIT ?",
                (now, max(limit * 10, limit)),
            ).fetchall()
            for row in rows:
                task_id, status = row[0], row[1]
                if self._dispatch_eligible(db, task_id, now):
                    runnable.append(task_id)
                if len(runnable) >= limit:
                    break
        return runnable


    def _dispatch_eligible(self, db: sqlite3.Connection, task_id: str, now: str) -> bool:
        row = db.execute(
            "SELECT t.project_id,t.status,t.control_state,s.state,s.wip_group,s.wip_limit,"
            "s.dependencies_json,s.resources_json,t.next_retry_at "
            "FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
            "WHERE t.id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return False
        if row[1] not in {"pending", "recovering"} or row[2] != "active" or row[3] != "admitted":
            return False
        if row[1] == "recovering" and row[8] and row[8] > now:
            return False
        dependencies = json.loads(row[6] or "[]")
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency.startswith("task-plan:"):
                return False
            dependency_row = db.execute(
                "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
            (dependency, row[0]),
            ).fetchone()
            if not dependency_row or dependency_row[0] != "done":
                return False
        active_count = db.execute(
            "SELECT COUNT(*) FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id "
            "WHERE active.project_id=? AND active.status='running' AND sa.wip_group=?",
            (row[0], row[4]),
        ).fetchone()[0]
        if active_count >= int(row[5]):
            return False
        requested = json.loads(row[7] or "[]")
        held_rows = db.execute(
            "SELECT sa.resources_json FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id "
            "WHERE active.project_id=? AND active.status='running' AND active.id!=?",
            (row[0], task_id),
        ).fetchall()
        return not any(_resource_domains_overlap(requested, json.loads(held[0] or "[]")) for held in held_rows)


    def _resolve_planned_dependencies(self) -> None:
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            rows = db.execute(
                "SELECT s.task_id,s.project_id,s.dependencies_json FROM v1_scheduling_entries s "
                "JOIN v1_tasks t ON t.id=s.task_id AND t.project_id=s.project_id "
                "WHERE s.state='queued' AND s.auto_admit=1 AND t.status='pending'"
            ).fetchall()
            for row in rows:
                dependencies = json.loads(row[2] or "[]")
                resolved: list[str] = []
                changed = False
                for value in dependencies:
                    if not isinstance(value, str) or not value.startswith("task-plan:"):
                        resolved.append(value)
                        continue
                    _, plan_id, task_ref = value.split(":", 2)
                    match = db.execute(
                        "SELECT id FROM v1_tasks WHERE project_id=? AND task_plan_id=? AND proposed_task_ref=? AND status='done' "
                        "ORDER BY finished_at DESC,created_at DESC LIMIT 1",
                        (row[1], plan_id, task_ref),
                    ).fetchone()
                    if not match:
                        match = db.execute(
                            "SELECT successor.id FROM v1_tasks predecessor "
                            "JOIN v1_tasks successor ON successor.id=predecessor.resolved_by_task_id "
                            "WHERE predecessor.project_id=? AND predecessor.task_plan_id=? "
                            "AND predecessor.proposed_task_ref=? AND predecessor.status='failed' "
                            "AND successor.project_id=predecessor.project_id AND successor.status='done' "
                            "ORDER BY successor.finished_at DESC,successor.created_at DESC LIMIT 1",
                            (row[1], plan_id, task_ref),
                        ).fetchone()
                    if not match:
                        resolved.append(value)
                        continue
                    dependency_id = match[0]
                    resolved.append(dependency_id)
                    db.execute(
                        "INSERT OR IGNORE INTO v1_task_dependencies(task_id,depends_on_task_id,project_id,required_terminal,created_at) VALUES(?,?,?,'done',?)",
                        (row[0], dependency_id, row[1], now),
                    )
                    changed = True
                if changed:
                    db.execute(
                        "UPDATE v1_scheduling_entries SET dependencies_json=?,updated_at=? WHERE task_id=?",
                        (json.dumps(resolved), now, row[0]),
                    )
                if resolved and all(not value.startswith("task-plan:") for value in resolved):
                    admitted = db.execute(
                        "UPDATE v1_scheduling_entries SET state='admitted',auto_admit=0,updated_at=? "
                        "WHERE task_id=? AND state='queued' AND auto_admit=1",
                        (now, row[0]),
                    ).rowcount
                    if admitted:
                        db.execute("UPDATE v1_tasks SET updated_at=? WHERE id=? AND status='pending'", (now, row[0]))
                        db.execute(
                            "UPDATE v1_backlog_items SET state='dispatched',updated_at=? WHERE task_id=?",
                            (now, row[0]),
                        )
                        self.store._event(
                            db,
                            row[1],
                            row[0],
                            None,
                            "task.dependencies_satisfied",
                            {
                                "dependencies": resolved,
                                "schedule_state": "admitted",
                            },
                        )
