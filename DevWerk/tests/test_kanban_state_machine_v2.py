from __future__ import annotations

import pytest


class FakeSettings:
    def __init__(self, db_path, session_dir):
        self.devwerk_db_path = str(db_path)
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(session_dir)


@pytest.fixture()
def kanban_runtime(monkeypatch, tmp_path):
    import app.kanban.store as store
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(store, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    store._initialized = False
    return store


def arbitrary_workflow() -> dict:
    return {
        "name": "arbitrary-delivery-flow",
        "version": 1,
        "workflow_type": "coding",
        "requires_apply": True,
        "columns": [
            {
                "status_key": "idea",
                "title": "Idea",
                "position": 10,
                "transition_to": ["make_it", "blocked"],
                "kind": "agent",
                "agent": "project-default",
                "job_template": "turn_goal_into_plan",
                "success_action": "plan_is_enough",
                "failure_actions": ["give_up"],
            },
            {
                "status_key": "make_it",
                "title": "Make It",
                "position": 20,
                "transition_to": ["ship_local", "blocked"],
                "kind": "agent",
                "agent": "builder",
                "job_template": "produce_code_change",
                "output_contract": "code_change",
                "success_action": "source_ready",
                "failure_actions": ["give_up"],
            },
            {
                "status_key": "ship_local",
                "title": "Ship Local",
                "position": 30,
                "transition_to": ["quality_check", "blocked"],
                "kind": "runtime",
                "runtime": "backend_apply",
                "success_action": "files_written",
                "failure_actions": ["give_up"],
            },
            {
                "status_key": "quality_check",
                "title": "Quality Check",
                "position": 40,
                "transition_to": ["released", "blocked"],
                "kind": "agent",
                "agent": "checker",
                "job_template": "verify_result",
                "success_action": "looks_good",
                "failure_actions": ["needs_restart", "give_up"],
            },
            {
                "status_key": "released",
                "title": "Released",
                "position": 90,
                "transition_to": [],
                "terminal": True,
                "terminal_kind": "success",
            },
            {
                "status_key": "blocked",
                "title": "Blocked",
                "position": 99,
                "transition_to": ["make_it"],
                "terminal": True,
                "terminal_kind": "failure",
            },
        ],
        "actions": {
            "plan_is_enough": {"to": "make_it", "kind": "advance"},
            "source_ready": {"to": "ship_local", "kind": "advance"},
            "files_written": {"to": "quality_check", "kind": "apply"},
            "looks_good": {"to": "released", "kind": "success"},
            "needs_restart": {"to": "make_it", "kind": "retry"},
            "give_up": {"to": "blocked", "kind": "failure"},
        },
    }


def test_dynamic_workflow_has_explicit_terminals_and_no_reserved_names():
    from app.kanban.definition import TerminalKind, validate_managed_workflow_definition, workflow_from_dict
    from app.kanban.state_machine import WorkflowStateMachine

    definition = workflow_from_dict(arbitrary_workflow())
    validate_managed_workflow_definition(definition)

    assert definition.terminal_statuses(TerminalKind.SUCCESS) == {"released"}
    assert definition.terminal_statuses(TerminalKind.FAILURE) == {"blocked"}

    machine = WorkflowStateMachine(definition)
    decision = machine.decide("make_it", "source_ready")
    assert decision.from_status == "make_it"
    assert decision.to_status == "ship_local"
    assert decision.terminal is False

    final = machine.decide("quality_check", "looks_good")
    assert final.to_status == "released"
    assert final.terminal is True
    assert final.terminal_kind == "success"


def test_workflow_actions_move_task_without_name_fallbacks(kanban_runtime):
    from app.kanban.definition import workflow_from_dict
    from app.services.workflow import apply_workflow_action, current_workflow_state

    project_id = "dynamic-project"
    kanban_runtime.update_project_workflow(project_id, arbitrary_workflow())
    kanban_runtime.ensure_project(project_id)
    task = kanban_runtime.create_task(project_id=project_id, title="Build anything")["task"]

    assert task["status_key"] == "idea"
    apply_workflow_action(task["id"], "plan_is_enough", {"reason": "plan ok"})
    apply_workflow_action(task["id"], "source_ready", {"reason": "code ready"})
    apply_workflow_action(task["id"], "files_written", {"reason": "applied"})
    result = apply_workflow_action(task["id"], "looks_good", {"reason": "verified"})

    assert result["task"]["status_key"] == "released"
    state = current_workflow_state(task["id"])
    assert state["terminal"] is True
    assert state["terminal_kind"] == "success"
    definition = workflow_from_dict(kanban_runtime.get_project_workflow(project_id)["workflow"])
    assert definition.column("released").terminal_kind == "success"


def test_control_fail_resolves_to_workflow_failure_action(kanban_runtime):
    from app.services.workflow import apply_workflow_action, current_workflow_state

    project_id = "dynamic-failure-project"
    kanban_runtime.update_project_workflow(project_id, arbitrary_workflow())
    task = kanban_runtime.create_task(project_id=project_id, title="Fail safely")["task"]

    failed = apply_workflow_action(task["id"], "fail", {"reason": "provider error"})

    assert failed["task"]["status_key"] == "blocked"
    state = current_workflow_state(task["id"])
    assert state["terminal"] is True
    assert state["terminal_kind"] == "failure"
    events = kanban_runtime.list_events(project_id=project_id, task_id=task["id"])["events"]
    assert any(item["event_type"] == "failure_bundle_created" for item in events)


def test_supervisor_terminates_unrecoverable_queued_task_with_workflow_failure_action(kanban_runtime):
    from app.services.workflow_supervisor import WorkflowSupervisor

    class SupervisorConfig:
        workflow_execution_timeout_seconds = 1
        workflow_client_timeout_seconds = 1
        workflow_user_timeout_seconds = 1
        workflow_queued_recovery_seconds = 0
        workflow_supervisor_interval_seconds = 1

    project_id = "dynamic-supervisor-project"
    kanban_runtime.update_project_workflow(project_id, arbitrary_workflow())
    task = kanban_runtime.create_task(project_id=project_id, title="Recover me")["task"]
    kanban_runtime.update_conversation(task["id"], state="queued", waiting_for=None)

    supervisor = WorkflowSupervisor(
        start_workflow=lambda task_id, payload: False,
        active_worker_age=lambda task_id: None,
        config=SupervisorConfig(),
    )
    supervisor.reconcile_once()

    detail = kanban_runtime.get_task(task["id"])
    assert detail["task"]["status_key"] == "blocked"
    events = kanban_runtime.list_events(project_id=project_id, task_id=task["id"])["events"]
    assert any(item["event_type"] == "workflow_supervisor_timeout" for item in events)
    assert any(item["event_type"] == "workflow_terminal_reached" for item in events)
