from __future__ import annotations

import json

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


def test_project_conversation_agent_can_request_local_file_tool(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes

    project_id = "conversation-agent-tools"
    kanban_service.upsert_project(project_id=project_id, name="Conversation Agent Tools")
    log_path = tmp_path / "devwerk-smoke.log"
    log_path.write_text("alpha\nROOT_CAUSE_MARKER: status stopped at ready_to_apply\n", encoding="utf-8")
    captured = {"calls": 0}

    class FakeClient:
        def chat_json(self, messages):
            captured["calls"] += 1
            if captured["calls"] == 1:
                return {
                    "action": "reply",
                    "reply": "I will inspect the log first.",
                    "tool_requests": [
                        {
                            "id": "read-log",
                            "tool": "workspace.read",
                            "args": {"path": str(log_path), "start_line": 1, "end_line": 20},
                        }
                    ],
                }
            payload = json.loads(messages[-1]["content"])
            captured["tool_results"] = payload["tool_results"]
            return {"action": "reply", "reply": f"The log contains ROOT_CAUSE_MARKER and tool id {payload['tool_results'][0]['id']}."}

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeClient())

    decision = kanban_routes._ask_project_conversation_agent(
        project_id=project_id,
        messages=[{"role": "user", "content": f"请阅读这个日志文件并分析：{log_path}"}],
        current_workflow={},
        current_agents={},
        active_task=None,
    )

    assert decision["action"] == "reply"
    assert captured["calls"] == 2
    assert "ROOT_CAUSE_MARKER" in captured["tool_results"][0]["content"]
    events = kanban_service.list_events(project_id=project_id, limit=20)["events"]
    assert any(event["event_type"] == "project_conversation_tool_requested" for event in events)
    assert any(event["event_type"] == "project_conversation_tool_executed" for event in events)


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
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm_repair",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("repair disabled in failure test")),
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


def test_project_conversation_design_auto_saves_and_returns_visible_reply(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    project_id = "conversation-design-auto-save"
    kanban_service.upsert_project(project_id=project_id, name="Conversation Design Auto Save")
    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {"action": "design", "reply": "Designing the requested coding workflow."},
    )
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "Coding workflow with review is ready.",
            "workflow": noncoding_workflow(domain="coding"),
            "agents": {"review-agent": {"model_route": "default"}},
        },
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(action="message", message="coding+review, please build the workflow."),
    )

    assert response["ok"] is True
    assert response["kind"] == "workflow_design"
    assert response["saved"] is True
    assert "Kanban workflow has been saved" in response["reply"]
    assert kanban_service.get_project_workflow(project_id)["workflow"]["columns"]
    conversation = kanban_routes.kanban_project_conversation(project_id)["messages"]
    assert conversation[-1]["role"] == "assistant"
    assert "Kanban workflow has been saved" in conversation[-1]["content"]
    assert kanban_service.get_project_settings(project_id)["settings"]["agents"]["review-agent"]["model_route"] == "default"
