from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time

import pytest


class SupervisorSettings:
    workflow_execution_timeout_seconds = 120
    workflow_client_timeout_seconds = 30
    workflow_user_timeout_seconds = 60
    workflow_queued_recovery_seconds = 10
    workflow_supervisor_interval_seconds = 60.0

    def __init__(self, db_path: Path):
        self.devwerk_db_path = str(db_path)


def _configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow_supervisor as supervisor_service

    configured = SupervisorSettings(tmp_path / "workflow-supervisor.db")
    monkeypatch.setattr(kanban_service, "settings", lambda: configured)
    monkeypatch.setattr(session_store, "settings", lambda: configured)
    monkeypatch.setattr(supervisor_service, "settings", lambda: configured)
    kanban_service._initialized = False
    return kanban_service, supervisor_service


def test_waiting_client_timeout_terminates_task_with_auditable_result(monkeypatch, tmp_path):
    kanban, supervisor_module = _configure(monkeypatch, tmp_path)
    task = kanban.create_task(
        project_id="supervisor-timeout",
        title="Compile project",
        description="Wait for an IDE compiler result.",
        status_key="draft",
    )["task"]
    kanban.ensure_conversation(task["id"])
    kanban.update_conversation(task["id"], state="waiting_client", waiting_for="client_tool")
    conversation = kanban.get_conversation(task["id"], include_messages=False)
    observed_at = datetime.fromisoformat(conversation["updated_at"]) + timedelta(seconds=31)

    supervisor = supervisor_module.WorkflowSupervisor(
        start_workflow=lambda task_id, body: True,
        active_worker_age=lambda task_id: None,
        config=SupervisorSettings(tmp_path / "unused-timeout.db"),
    )
    supervisor.reconcile_once(now=observed_at)

    detail = kanban.get_task(task["id"])["task"]
    assert detail["status_key"] == "failed"
    assert kanban.get_conversation(task["id"], include_messages=False)["state"] == "failed"
    assert "workflow_supervisor_timeout" in {event["event_type"] for event in detail["events"]}
    result = [item for item in detail["artifacts"] if item["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["error_code"] == "WORKFLOW_SUPERVISOR_TIMEOUT"
    assert result["payload"]["done"] is True


def test_supervisor_recovers_queued_task_from_persisted_request(monkeypatch, tmp_path):
    kanban, supervisor_module = _configure(monkeypatch, tmp_path)
    task = kanban.create_task(
        project_id="supervisor-recovery",
        title="Recover worker",
        description="Resume a lost workflow worker.",
        status_key="draft",
    )["task"]
    kanban.ensure_conversation(task["id"])
    kanban.add_artifact(
        task["id"],
        artifact_type="workflow_request_body",
        payload={"project_id": "supervisor-recovery", "messages": [{"role": "user", "content": "Original."}]},
    )
    kanban.add_artifact(
        task["id"],
        artifact_type="workflow_run_request",
        payload={
            "project_id": "supervisor-recovery",
            "messages": [{"role": "user", "content": "Recover."}],
            "tool_results": [{"id": "compile", "ok": False, "error": "compile failed"}],
        },
    )
    kanban.update_conversation(task["id"], state="queued", waiting_for=None)
    conversation = kanban.get_conversation(task["id"], include_messages=False)
    observed_at = datetime.fromisoformat(conversation["updated_at"]) + timedelta(seconds=11)
    dispatched: list[tuple[str, dict]] = []

    supervisor = supervisor_module.WorkflowSupervisor(
        start_workflow=lambda task_id, body: dispatched.append((task_id, body)) or True,
        active_worker_age=lambda task_id: None,
        config=SupervisorSettings(tmp_path / "unused-recovery.db"),
    )
    supervisor.reconcile_once(now=observed_at)

    assert len(dispatched) == 1
    assert dispatched[0][0] == task["id"]
    assert dispatched[0][1]["task_id"] == task["id"]
    assert dispatched[0][1]["tool_results"][0]["id"] == "compile"
    detail = kanban.get_task(task["id"])["task"]
    assert "workflow_worker_recovery_requested" in {event["event_type"] for event in detail["events"]}


def test_retry_action_is_idempotent_and_dispatches_once(monkeypatch, tmp_path):
    kanban, _ = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    task = kanban.create_task(
        project_id="retry-idempotency",
        title="Retry once",
        description="A failed task should have one active retry.",
        status_key="failed",
    )["task"]
    kanban.ensure_conversation(task["id"])
    kanban.add_artifact(
        task["id"],
        artifact_type="workflow_request_body",
        payload={"project_id": "retry-idempotency", "messages": [{"role": "user", "content": "Retry."}]},
    )
    kanban.add_artifact(
        task["id"],
        artifact_type="workflow_result",
        payload={"ok": False, "done": True, "status_key": "failed"},
    )
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        workflow_routes,
        "_start_workflow_thread",
        lambda task_id, body: dispatched.append((task_id, body)) or True,
    )

    request = kanban_routes.WorkflowActionRequest(action="retry", payload={})
    first = kanban_routes.kanban_task_action(task["id"], request)
    second = kanban_routes.kanban_task_action(task["id"], request)

    assert first["workflow_resume"]["reason"] == "retry"
    assert second["action_ignored"] is True
    assert second["ignored_action"] == "retry"
    assert len(dispatched) == 1
    assert kanban.get_task(task["id"])["task"]["status_key"] == "draft"
    event_types = {event["event_type"] for event in kanban.get_task(task["id"])["task"]["events"]}
    assert "workflow_retry_queued" in event_types
    assert "workflow_retry_deduplicated" in event_types


def test_custom_workflow_lifecycle_actions_do_not_require_column_edges(monkeypatch, tmp_path):
    kanban, _ = _configure(monkeypatch, tmp_path)
    from app.services.workflow import apply_workflow_action

    project_id = "custom-lifecycle"
    kanban.update_project_workflow(
        project_id,
        {
            "name": "custom-lifecycle",
            "columns": [
                {"status_key": "draft", "title": "Draft", "position": 10, "transition_to": ["work"]},
                {"status_key": "work", "title": "Work", "position": 20, "transition_to": ["done"]},
                {"status_key": "done", "title": "Done", "position": 30, "transition_to": []},
                {"status_key": "failed", "title": "Failed", "position": 40, "transition_to": []},
            ],
            "actions": {
                "start": {"to": "work"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "failed"},
                "retry": {"to": "draft"},
            },
        },
    )
    task = kanban.create_task(
        project_id=project_id,
        title="Lifecycle",
        description="System lifecycle actions remain available.",
        status_key="work",
    )["task"]

    failed = apply_workflow_action(task["id"], "fail", {"reason": "timeout"})
    retried = apply_workflow_action(task["id"], "retry", {"reason": "operator retry"})

    assert failed["task"]["status_key"] == "failed"
    assert retried["task"]["status_key"] == "draft"


def test_invalid_custom_workflow_is_rejected_before_persistence(monkeypatch, tmp_path):
    kanban, _ = _configure(monkeypatch, tmp_path)
    project_id = "invalid-custom-workflow"

    with pytest.raises(ValueError, match="unknown transitions"):
        kanban.update_project_workflow(
            project_id,
            {
                "name": "invalid",
                "columns": [
                    {"status_key": "draft", "transition_to": ["missing"]},
                    {"status_key": "done", "transition_to": []},
                    {"status_key": "failed", "transition_to": []},
                ],
                "actions": {
                    "fail": {"to": "failed"},
                    "abandon": {"to": "failed"},
                    "retry": {"to": "draft"},
                },
            },
        )

    assert kanban.get_project_workflow(project_id)["workflow"]["name"] == "default"


def test_worker_dispatch_deduplicates_same_task_and_payload(monkeypatch, tmp_path):
    kanban, _ = _configure(monkeypatch, tmp_path)
    import app.routes.workflows as workflow_routes

    task = kanban.create_task(
        project_id="worker-deduplication",
        title="One worker",
        description="Duplicate dispatch must not create concurrent workers.",
        status_key="draft",
    )["task"]
    kanban.ensure_conversation(task["id"])
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_runner(task_id: str, body: dict) -> None:
        calls.append(task_id)
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(workflow_routes, "_run_workflow_thread", blocking_runner)
    workflow_routes._active_workflows.clear()
    workflow_routes._pending_workflows.clear()
    body = {"task_id": task["id"], "project_id": "worker-deduplication", "messages": []}

    assert workflow_routes._start_workflow_thread(task["id"], body) is True
    assert entered.wait(timeout=2)
    assert workflow_routes._start_workflow_thread(task["id"], body) is False
    release.set()
    deadline = time.monotonic() + 3
    while workflow_routes.workflow_worker_age(task["id"]) is not None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert calls == [task["id"]]
    assert workflow_routes.workflow_worker_age(task["id"]) is None
    event_types = {event["event_type"] for event in kanban.get_task(task["id"])["task"]["events"]}
    assert "workflow_dispatch_deduplicated" in event_types
