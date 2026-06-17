import json

from app.models.ide import IdeChatResponse
from app.services.kanban import DEFAULT_COLUMNS


def test_ide_error_response_can_omit_reply():
    response = IdeChatResponse(ok=False, done=True, error_code="BAD_REQUEST")

    assert response.reply == ""
    assert response.ok is False


def test_default_kanban_flow_contains_required_control_points():
    statuses = [column["status_key"] for column in DEFAULT_COLUMNS]

    for required in (
        "draft",
        "context_indexed",
        "planned",
        "coding",
        "reviewed",
        "ready_to_apply",
        "applied",
        "verified",
        "done",
        "failed",
    ):
        assert required in statuses


def test_dashboard_contains_task_detail_surface():
    from app.routes.kanban import DASHBOARD_HTML

    assert 'data-view="details"' in DASHBOARD_HTML
    assert 'id="view-details"' in DASHBOARD_HTML
    assert "loadTaskDetail" in DASHBOARD_HTML
    assert "workflow_phase_output" in DASHBOARD_HTML
    assert "code_context_summary" in DASHBOARD_HTML
    assert "review_bundle" in DASHBOARD_HTML


def test_workflow_action_protocol_drives_kanban_state(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "workflow.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="workflow-smoke",
        title="Implement feature",
        description="Smoke",
        status_key="ready_to_apply",
    )["task"]

    applied = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": True,
            "snapshot_id": "20260614-0001-test",
            "changed_paths": ["src/main/java/App.java"],
            "verification": {},
        },
    )

    assert applied["task"]["status_key"] == "done"
    state = workflow_service.current_workflow_state(task["id"])
    assert state["status_key"] == "done"
    events = kanban_service.list_events(project_id="workflow-smoke", task_id=task["id"], limit=50)["events"]
    event_types = [event["event_type"] for event in events]
    assert "task_moved" in event_types
    assert "apply_result_received" in event_types
    assert events[0]["task_title"] == "Implement feature"
    task_log = tmp_path / "sessions" / "workflow-smoke" / task["id"] / "events.jsonl"
    latest_memory = tmp_path / "sessions" / "workflow-smoke" / task["id"] / "latest_memory.json"
    project_memory_path = tmp_path / "sessions" / "workflow-smoke" / "project_memory.json"
    project_memory_log = tmp_path / "sessions" / "workflow-smoke" / "project_memory.jsonl"
    assert task_log.is_file()
    assert latest_memory.is_file()
    assert project_memory_path.is_file()
    assert project_memory_log.is_file()
    task_events = [json.loads(line) for line in task_log.read_text(encoding="utf-8").splitlines()]
    assert any(event["event_type"] == "task_moved" for event in task_events)
    memory = json.loads(latest_memory.read_text(encoding="utf-8"))
    assert memory["task_id"] == task["id"]
    assert memory["phase"] == "apply"
    assert memory["status_key"] == "done"
    session_events = (
        tmp_path
        / "sessions"
        / "workflow-smoke"
        / task["id"]
        / "sessions"
        / memory["session_id"]
        / "events.jsonl"
    )
    session_memory = session_events.parent / "memory.json"
    assert session_events.is_file()
    assert session_memory.is_file()
    project_memory = session_store.read_project_memory("workflow-smoke")
    assert project_memory["project_id"] == "workflow-smoke"
    assert task["id"] in project_memory["tasks_seen"]
    assert "src/main/java/App.java" in project_memory["paths"]
    assert any(item["phase"] == "apply" and item["status_key"] == "done" for item in project_memory["phase_summaries"])

    abandoned = workflow_service.apply_workflow_action(task["id"], "abandon", {"reason": "test"})
    assert abandoned["task"]["status_key"] == "failed"

    retried = workflow_service.apply_workflow_action(task["id"], "retry", {"reason": "test"})
    assert retried["task"]["status_key"] == "draft"


def test_workflow_semantic_rework_actions(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "workflow-rework.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="workflow-rework",
        title="Review coding output",
        description="Smoke",
        status_key="reviewed",
    )["task"]

    recoding = workflow_service.apply_workflow_action(
        task["id"],
        "request_recoding",
        {"phase": "reviewed", "reason": "Generated change missed an approved file."},
    )
    assert recoding["task"]["status_key"] == "coding"

    reviewed = kanban_service.move_task(task["id"], "reviewed", force=True, payload={"reason": "retry review"})
    assert reviewed["task"]["status_key"] == "reviewed"

    approved = workflow_service.apply_workflow_action(
        task["id"],
        "approve",
        {"phase": "reviewed", "reason": "Review passed."},
    )
    assert approved["task"]["status_key"] == "ready_to_apply"

    events = kanban_service.list_events(project_id="workflow-rework", task_id=task["id"], limit=50)["events"]
    event_types = [event["event_type"] for event in events]
    assert "workflow_transition_decided" in event_types
    assert "workflow_rework_requested" in event_types
