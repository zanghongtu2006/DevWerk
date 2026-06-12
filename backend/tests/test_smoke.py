from app.models.ide import IdeChatResponse
from app.services.kanban import DEFAULT_COLUMNS


def test_ide_error_response_can_omit_reply():
    response = IdeChatResponse(ok=False, done=True, error_code="BAD_REQUEST")

    assert response.reply == ""
    assert response.ok is False


def test_default_kanban_flow_contains_required_control_points():
    statuses = [column["status_key"] for column in DEFAULT_COLUMNS]

    for required in ("draft", "planned", "snapshot_ready", "coding", "verification", "done", "failed"):
        assert required in statuses
