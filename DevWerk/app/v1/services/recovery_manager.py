from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.v1.files import ProjectFiles
from app.v1.repositories.base import StoreHost
from app.v1.states import TASK_STATE_MACHINE, TaskStatus
from app.v1.storage_support import new_id, utcnow


class RecoveryManager:
    def __init__(self, store: StoreHost):
        self.store = store

    def retry_task(self, task_id: str, column_key: str | None = None, *, clear_context: bool = False) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable; use task.rerun to create a successor")
        if task["status"] == "running":
            raise ValueError("a running Task cannot be retried until its active Attempt stops")
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.PENDING)
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        target = column_key or workflow.entry
        if workflow.terminal_kind(target):
            raise ValueError("retry target must be a non-terminal Column")
        workflow.column(target)
        now = utcnow()
        context = {} if clear_context else task["context"]
        with self.store.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='active',current_column=?,attempt=0,context_json=?,error=NULL,lease_owner=NULL,lease_until=NULL,finished_at=NULL,state_version=state_version+1,updated_at=? WHERE id=?",
                (target, json.dumps(context, ensure_ascii=False), now, task_id),
            )
            data = {"target": target, "clear_context": clear_context}
            self.store._event(db, task["project_id"], task_id, None, "task.retry_requested", data)
            self.store._mailbox(db, task["project_id"], "task.retry_requested", task_id, None, data)
        return self.store.get_task(task_id)

    def reopen_task(self, task_id: str, column_key: str | None = None, *, clear_context: bool = False) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] != "failed":
            raise ValueError("task.reopen requires a failed Task; review rejection follows Workflow transitions")
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.PENDING)
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        target = column_key or workflow.entry
        if workflow.terminal_kind(target):
            raise ValueError("task.reopen target must be a non-terminal Column")
        workflow.column(target)
        now = utcnow()
        context = {} if clear_context else task["context"]
        with self.store.tx(immediate=True) as db:
            sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?",
                (task_id,),
            ).fetchone()[0])
            run_id = new_id("run")
            db.execute(
                "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at) "
                "VALUES(?,?,?,?,?,'pending',0,'{}',?)",
                (run_id, task["project_id"], task_id, target, sequence, now),
            )
            changed = db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='active',current_column=?,attempt=0,"
                "context_json=?,error=NULL,lease_owner=NULL,lease_until=NULL,finished_at=NULL,"
                "terminal_artifact_id=NULL,terminal_event_id=NULL,notified_at=NULL,observed_at=NULL,"
                "supervision_action='reopened',state_version=state_version+1,updated_at=? "
                "WHERE id=? AND status='failed'",
                (target, json.dumps(context, ensure_ascii=False), now, task_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while reopening")
            dependencies = json.loads(db.execute(
                "SELECT dependencies_json FROM v1_scheduling_entries WHERE task_id=?",
                (task_id,),
            ).fetchone()[0] or "[]")
            eligible = all(
                isinstance(dependency, str)
                and not dependency.startswith("task-plan:")
                and (
                    (row := db.execute(
                        "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
                        (dependency, task["project_id"]),
                    ).fetchone())
                    and row[0] == "done"
                )
                for dependency in dependencies
            )
            schedule_state = "admitted" if eligible else "queued"
            db.execute(
                "UPDATE v1_scheduling_entries SET state=?,auto_admit=?,updated_at=? WHERE task_id=?",
                (schedule_state, int(not eligible), now, task_id),
            )
            data = {
                "target": target,
                "clear_context": clear_context,
                "column_run_id": run_id,
                "schedule_state": schedule_state,
                "previous_error": task.get("error"),
            }
            self.store._event(db, task["project_id"], task_id, run_id, "task.reopened", data)
            self.store._mailbox(db, task["project_id"], "task.reopened", task_id, run_id, data)
            self.store._refresh_projection(db, task["project_id"])
        return self.store.get_task(task_id)

    def route_task_to_failed(self, task_id: str, reason: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        if task["status"] == "running":
            raise ValueError(
                "a running Task with an active Attempt cannot be failed or cancelled; "
                "request task.pause and wait for the Task to leave running first"
            )
        return self._fail_task_now(task, reason, "cancelled")

    def rerun_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] not in {"done", "failed"}:
            raise ValueError("task.rerun requires an immutable terminal Task")
        return self.store.create_task(
            task["project_id"],
            task_plan_id=task["task_plan_id"],
            proposed_task_ref=task["proposed_task_ref"],
            rerun_of_task_id=task["id"],
        )

    def pause_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        now = utcnow()
        target_state = "pause_requested" if task["status"] == "running" else "paused"
        with self.store.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_tasks SET control_state=?,state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (target_state, now, task_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while pausing")
            self.store._event(db, task["project_id"], task_id, None, "task.pause_requested", {})
            self.store._mailbox(db, task["project_id"], "task.pause_requested", task_id, None, {})
            self.store._refresh_projection(db, task["project_id"])
        return self.store.get_task(task_id)

    def resume_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        if task.get("control_state") not in {"paused", "pause_requested"}:
            raise ValueError("Task is not paused")
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_tasks SET control_state='active',state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (now, task_id),
            )
            self.store._event(db, task["project_id"], task_id, None, "task.resumed", {})
            self.store._mailbox(db, task["project_id"], "task.resumed", task_id, None, {})
            self.store._refresh_projection(db, task["project_id"])
        return self.store.get_task(task_id)

    def fail_task_from_exception(self, task: dict[str, Any], run_id: str, error: str, terminal_artifact: dict[str, Any], *, checkpoint: dict[str, Any] | None = None) -> None:
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.FAILED)
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            db.execute("UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE id=?", (error, now, run_id))
            db.execute("UPDATE v1_column_attempts SET status='failed',error=?,checkpoint_json=?,finished_at=? WHERE column_run_id=? AND status IN ('running','waiting')", (error, self.store._pack_json(checkpoint or {}), now, run_id))
            db.execute(
                "UPDATE v1_tasks SET status='failed',error=?,lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=?",
                (error, now, now, task["id"]),
            )
            artifact_id = new_id("art")
            db.execute("INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at", (artifact_id, task["project_id"], task["id"], run_id, terminal_artifact["kind"], terminal_artifact["path"], terminal_artifact["sha256"], terminal_artifact["size"], json.dumps(terminal_artifact["meta"]), now))
            data = {"error": error, "reason": "runtime_definition_unavailable", "artifact_id": artifact_id}
            self.store._event(db, task["project_id"], task["id"], run_id, "task.failed", data)
            terminal_event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.store._mailbox(db, task["project_id"], "task.failed", task["id"], run_id, data)
            db.execute("UPDATE v1_tasks SET terminal_artifact_id=?,terminal_event_id=?,notified_at=?,state_version=state_version+1 WHERE id=?", (artifact_id, terminal_event_id, now, task["id"]))
            self.store._refresh_projection(db, task["project_id"])

    def recover_task_from_exception(
        self,
        task: dict[str, Any],
        run_id: str,
        error: str,
        *,
        error_code: str,
        error_category: str,
        checkpoint: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> None:
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.RECOVERING)
        now = utcnow()
        next_retry_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self.store.policy.scheduling.recovery_retry_delay_seconds)
        ).isoformat(timespec="milliseconds")
        with self.store.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,error_category=?,finished_at=? WHERE id=?",
                (error, error_category, now, run_id),
            )
            db.execute(
                "UPDATE v1_column_attempts SET status='failed',error=?,error_code=?,error_category=?,"
                "checkpoint_json=?,agent_run_id=COALESCE(?,agent_run_id),finished_at=? "
                "WHERE column_run_id=? AND status IN ('running','waiting')",
                (error, error_code, error_category, self.store._pack_json(checkpoint or {}), agent_run_id, now, run_id),
            )
            changed = db.execute(
                "UPDATE v1_tasks SET status='recovering',error=?,lease_owner=NULL,lease_until=NULL,next_retry_at=?,"
                "finished_at=NULL,supervision_action='recovering',state_version=state_version+1,updated_at=? "
                "WHERE id=? AND state_version=? AND status='running'",
                (error, next_retry_at, now, task["id"], task["state_version"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("stale Task state_version while entering recovery")
            self.store._event(
                db,
                task["project_id"],
                task["id"],
                run_id,
                "task.recovering",
                {
                    "column": task["current_column"],
                    "failed_run_id": run_id,
                    "error": error,
                    "error_code": error_code,
                    "error_category": error_category,
                    "retry_after_seconds": self.store.policy.scheduling.recovery_retry_delay_seconds,
                    "next_retry_at": next_retry_at,
                },
            )
            self.store._refresh_projection(db, task["project_id"])

    def _fail_task_now(self, task: dict[str, Any], reason: str, failure_code: str) -> dict[str, Any]:
        now = utcnow()
        workflow = self.store.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        failed_column = workflow.terminal_key("failed")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT id FROM v1_column_runs WHERE task_id=? ORDER BY sequence DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
        run_id = row[0] if row else None
        payload = {
            "schema": "devwerk.task-terminal.v1",
            "project_id": task["project_id"],
            "task_id": task["id"],
            "column_run_id": run_id,
            "terminal": "failed",
            "failure_code": failure_code,
            "error": reason,
            "recorded_at": now,
        }
        info = ProjectFiles(self.store.get_project(task["project_id"])["base_dir"], self.store.policy).write_text(
            f".devwerk/terminal/{task['id']}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        with self.store.tx(immediate=True) as db:
            current = db.execute("SELECT status FROM v1_tasks WHERE id=?", (task["id"],)).fetchone()
            if not current:
                raise KeyError(task["id"])
            if current[0] in {"done", "failed"}:
                return self.store.get_task(task["id"])
            TASK_STATE_MACHINE.require(current[0], TaskStatus.FAILED)
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE task_id=? AND status IN ('pending','running','waiting')",
                (reason, now, task["id"]),
            )
            db.execute(
                "UPDATE v1_column_attempts SET status='failed',error=?,finished_at=? WHERE task_id=? AND status IN ('running','waiting')",
                (reason, now, task["id"]),
            )
            db.execute("UPDATE v1_await_handles SET status='cancelled',updated_at=? WHERE task_id=? AND status='pending'", (now, task["id"]))
            changed = db.execute(
                "UPDATE v1_tasks SET status='failed',control_state='active',current_column=?,error=?,lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (failed_column, reason, now, now, task["id"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while applying terminal failure")
            artifact_id = new_id("art")
            db.execute(
                "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                (artifact_id, task["project_id"], task["id"], run_id, "task_terminal", info["path"], info["sha256"], info["size"], json.dumps({"terminal": "failed", "failure_code": failure_code}, ensure_ascii=False), now),
            )
            data = {"error": reason, "failure_code": failure_code, "artifact_id": artifact_id}
            self.store._event(db, task["project_id"], task["id"], run_id, "task.failed", data)
            terminal_event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.store._mailbox(db, task["project_id"], "task.failed", task["id"], run_id, data)
            db.execute(
                "UPDATE v1_tasks SET terminal_artifact_id=?,terminal_event_id=?,notified_at=? WHERE id=?",
                (artifact_id, terminal_event_id, now, task["id"]),
            )
            self.store._refresh_projection(db, task["project_id"])
        return self.store.get_task(task["id"])
