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
        "ready_to_apply",
        "applied",
        "verified",
        "done",
        "failed",
    ):
        assert required in statuses


def test_workflow_action_protocol_drives_kanban_state(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "workflow.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
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

    assert applied["task"]["status_key"] == "applied"
    state = workflow_service.current_workflow_state(task["id"])
    assert state["status_key"] == "applied"

    abandoned = workflow_service.apply_workflow_action(task["id"], "abandon", {"reason": "test"})
    assert abandoned["task"]["status_key"] == "failed"

    retried = workflow_service.apply_workflow_action(task["id"], "retry", {"reason": "test"})
    assert retried["task"]["status_key"] == "draft"
