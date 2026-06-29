from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from app.core.config import settings
from app.services.kanban import (
    add_artifact,
    add_event,
    get_latest_artifact_payload,
    list_managed_workflow_states,
    update_conversation,
)
from app.services.workflow import apply_workflow_action

_log = logging.getLogger("devwerk.workflow_supervisor")


class WorkflowSupervisor:
    """Reconcile persisted Kanban tasks with in-process workflow workers."""

    def __init__(
        self,
        *,
        start_workflow: Callable[[str, dict], bool],
        active_worker_age: Callable[[str], float | None],
        config: object | None = None,
    ) -> None:
        self._start_workflow = start_workflow
        self._active_worker_age = active_worker_age
        self._config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="devwerk-workflow-supervisor", daemon=True)
        self._thread.start()
        _log.info("workflow supervisor started")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None
        _log.info("workflow supervisor stopped")

    def reconcile_once(self, *, now: datetime | None = None) -> None:
        cfg = self._config or settings()
        current_time = now or datetime.now(timezone.utc)
        execution_timeout = int(getattr(cfg, "workflow_execution_timeout_seconds", 1800))
        client_timeout = int(getattr(cfg, "workflow_client_timeout_seconds", 1800))
        user_timeout = int(getattr(cfg, "workflow_user_timeout_seconds", 86400))
        queued_recovery = int(getattr(cfg, "workflow_queued_recovery_seconds", 15))

        for runtime in list_managed_workflow_states():
            task_id = str(runtime["task_id"])
            state = str(runtime.get("state") or "")
            waiting_for = str(runtime.get("waiting_for") or "")
            age = _age_seconds(runtime.get("conversation_updated_at"), current_time)
            worker_age = self._active_worker_age(task_id)

            if worker_age is not None:
                if worker_age > execution_timeout:
                    self._fail(task_id, "worker_execution_timeout", worker_age, execution_timeout, state, waiting_for)
                continue

            if state in {"queued", "running", "active"} and age >= queued_recovery:
                body = (
                    get_latest_artifact_payload(task_id, "workflow_run_request")
                    or get_latest_artifact_payload(task_id, "workflow_request_body")
                )
                if body:
                    payload = dict(body)
                    payload["task_id"] = task_id
                    add_event(
                        task_id,
                        "workflow_worker_recovery_requested",
                        {"state": state, "status_key": runtime.get("status_key"), "idle_seconds": int(age)},
                    )
                    update_conversation(task_id, state="queued", waiting_for=None)
                    self._start_workflow(task_id, payload)
                else:
                    self._fail(task_id, "missing_recovery_payload", age, queued_recovery, state, waiting_for)
                continue

            if state == "waiting_client" and age >= client_timeout:
                self._fail(task_id, "client_boundary_timeout", age, client_timeout, state, waiting_for)
                continue

            if state == "waiting_user" and age >= user_timeout:
                self._fail(task_id, "user_guidance_timeout", age, user_timeout, state, waiting_for)

    def _run(self) -> None:
        while not self._stop.is_set():
            cfg = self._config or settings()
            interval = max(float(getattr(cfg, "workflow_supervisor_interval_seconds", 5.0)), 0.25)
            if self._stop.wait(interval):
                break
            try:
                self.reconcile_once()
            except Exception:  # noqa: BLE001
                _log.exception("workflow supervisor reconciliation failed")

    @staticmethod
    def _fail(
        task_id: str,
        reason: str,
        age: float,
        timeout: int,
        state: str,
        waiting_for: str,
    ) -> None:
        payload = {
            "phase": "workflow_supervisor",
            "reason": reason,
            "state": state,
            "waiting_for": waiting_for or None,
            "idle_seconds": int(age),
            "timeout_seconds": timeout,
        }
        add_event(task_id, "workflow_supervisor_timeout", payload)
        try:
            result = apply_workflow_action(task_id, "fail", payload)
            status_key = str((result.get("task") or {}).get("status_key") or "")
            update_conversation(task_id, state="failed", waiting_for=None, active_column=status_key or None)
            add_event(task_id, "workflow_finished", {**payload, "ok": False, "status_key": status_key, "terminal": True})
            add_artifact(
                task_id,
                artifact_type="workflow_result",
                payload={
                    "ok": False,
                    "done": True,
                    "task_id": task_id,
                    "status_key": status_key,
                    "error_code": "WORKFLOW_SUPERVISOR_TIMEOUT",
                    "error_message": reason,
                    "retryable": True,
                },
            )
        except Exception:  # noqa: BLE001
            _log.exception("workflow supervisor failed to terminate task_id=%s reason=%s", task_id, reason)


def _age_seconds(value: object, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max((now - parsed.astimezone(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return float("inf")
