from __future__ import annotations

from tests.workflow_test_utils import configure_kanban


def test_project_conversation_reads_message_events_without_scanning_recent_debug_events(monkeypatch, tmp_path):
    kanban = configure_kanban(monkeypatch, tmp_path)
    project_id = "conversation-query-project"
    kanban.upsert_project(project_id=project_id, name="Conversation Query Project")
    kanban.add_project_event(
        project_id,
        "project_conversation_message",
        {"role": "user", "content": "Create the project workflow.", "kind": "message"},
    )

    debug_payload = {
        "debug": "x" * 10000,
        "llm_input": [{"role": "user", "content": "large prompt"}] * 20,
        "llm_output": {"reply": "large output" * 1000},
    }
    for _ in range(100):
        kanban.add_project_event(project_id, "project_workflow_design_debug", debug_payload)

    result = kanban.list_project_conversation_messages(project_id, limit=20)

    assert result["messages"] == [
        {
            "role": "user",
            "content": "Create the project workflow.",
            "kind": "message",
            "created_at": result["messages"][0]["created_at"],
            "task_id": None,
        }
    ]


def test_event_summary_mode_uses_compact_payload_without_losing_full_payload(monkeypatch, tmp_path):
    kanban = configure_kanban(monkeypatch, tmp_path)
    project_id = "event-summary-project"
    kanban.upsert_project(project_id=project_id, name="Event Summary Project")
    kanban.add_project_event(
        project_id,
        "project_workflow_design_debug",
        {"llm_input": "x" * 10000, "llm_output": {"reply": "y" * 10000}},
    )

    summary_event = kanban.list_events(project_id=project_id, payload_mode="summary")["events"][0]
    full_event = kanban.list_events(project_id=project_id, payload_mode="full")["events"][0]

    assert len(str(summary_event["payload"])) < 2000
    assert summary_event["payload"]["llm_input"]["_truncated"] is True
    assert len(full_event["payload"]["llm_input"]) == 10000
