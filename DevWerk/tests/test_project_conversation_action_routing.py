from __future__ import annotations

import pytest

from tests.workflow_test_utils import configure_kanban, noncoding_workflow


def test_project_agent_prompt_prevents_premature_save_design(monkeypatch):
    import app.routes.kanban as kanban_routes

    captured = {}

    class FakeClient:
        def chat_json(self, messages):
            captured["system"] = messages[0]["content"]
            return {"action": "reply", "reply": "Tell me whether you want a workflow now or just notes."}

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeClient())

    decision = kanban_routes._ask_project_conversation_agent(
        project_id="routing-prompt",
        messages=[{"role": "user", "content": "我准备做一个 AI Agent 技术内容项目，主要面向程序员。"}],
        current_workflow={},
        current_agents={},
        active_task=None,
    )

    assert decision["action"] == "reply"
    assert "Do not choose design/save_design just because the user describes a project goal" in captured["system"]


@pytest.mark.parametrize(
    ("case_id", "decision", "expected_kind"),
    [
        ("PCR-001-explicit-workflow", {"action": "save_design", "reply": "Designing workflow.", "save": True}, "workflow_design"),
        ("PCR-002-goal-description", {"action": "reply", "reply": "I can help refine the goal first."}, "reply"),
        ("PCR-003-new-work-item", {"action": "start_task", "reply": "Starting task.", "task_request": "Write first article."}, "task_started"),
        ("PCR-004-active-follow-up", {"action": "continue_task", "reply": "Continuing task.", "task_request": "Adjust title tone."}, "task_continued"),
        ("PCR-005-plain-question", {"action": "reply", "reply": "This project has a default project agent."}, "reply"),
    ],
)
def test_project_conversation_action_routing(monkeypatch, tmp_path, case_id, decision, expected_kind):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes
    import app.services.workflow_designer as workflow_designer

    project_id = case_id
    kanban_service.upsert_project(project_id=project_id, name=case_id)
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))

    task = None
    if expected_kind == "task_continued":
        task = kanban_service.create_task(project_id=project_id, title="Active writing task", description="Active")

    monkeypatch.setattr(kanban_routes, "_ask_project_conversation_agent", lambda **kwargs: decision)
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {"reply": "Workflow saved.", "workflow": noncoding_workflow(domain="writing"), "agents": {}},
    )
    monkeypatch.setattr(
        workflow_routes,
        "start_workflow_payload",
        lambda body: {"ok": True, "task_id": "started-task", "status_key": "intake", "poll_url": "/poll", "events_url": "/events"},
    )
    monkeypatch.setattr(
        workflow_routes,
        "continue_workflow_payload",
        lambda task_id, incoming: {"ok": True, "task_id": task_id, "status_key": "prepared", "poll_url": "/poll", "events_url": "/events"},
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="请根据上下文处理这个项目请求。",
            metadata={"active_task_id": (task or {}).get("task", {}).get("id")},
        ),
    )

    assert response["kind"] == expected_kind
    if expected_kind == "workflow_design":
        assert kanban_service.get_project_workflow(project_id)["workflow"]["columns"]


def test_project_conversation_workflow_design_failure_returns_chat_response(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    project_id = "conversation-design-failed"
    kanban_service.upsert_project(project_id=project_id, name="Conversation Design Failed")
    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {"action": "save_design", "reply": "Designing.", "save": True},
    )
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {"reply": "ok", "workflow": {"columns": []}, "agents": {}},
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(action="message", message="帮我搭一个流程。"),
    )

    assert response["ok"] is False
    assert response["kind"] == "workflow_design_failed"
    assert response["error_code"] == "WORKFLOW_EMPTY_COLUMNS"
    assert response["debug_event_recorded"] is True
    events = kanban_service.list_events(project_id=project_id, limit=20)["events"]
    assert any(event["event_type"] == "project_workflow_design_failed" for event in events)
