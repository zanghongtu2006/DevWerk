from __future__ import annotations


class FakeSettings:
    def __init__(self, db_path, session_dir):
        self.devwerk_db_path = str(db_path)
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(session_dir)


def _configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    return kanban_service


def test_workflow_designer_fallback_returns_managed_workflow(monkeypatch, tmp_path):
    from app.services import workflow_designer
    from app.services.workflow_definition import validate_managed_workflow_definition, workflow_from_dict

    def fail_llm(*args, **kwargs):
        raise RuntimeError("no llm configured")

    monkeypatch.setattr(workflow_designer, "_ask_llm", fail_llm)
    result = workflow_designer.design_project_workflow(
        project_id="designer-fallback",
        messages=[{"role": "user", "content": "Create a flow with planning, coding, review, compile and retry."}],
        current_workflow=None,
        current_agents=None,
    )

    assert result["ok"] is True
    assert result["source"] == "fallback"
    assert result["workflow"]["actions"]["fail"]["to"] == "failed"
    assert result["workflow"]["actions"]["abandon"]["to"] == "failed"
    assert result["workflow"]["actions"]["retry"]["to"] == "draft"
    validate_managed_workflow_definition(workflow_from_dict(result["workflow"]))


def test_workflow_designer_uses_project_agent_for_default_llm(monkeypatch):
    from app.services import workflow_designer

    captured = {}

    class FakeClient:
        def chat_json(self, messages):
            captured["messages"] = messages
            return {"reply": "ok", "workflow": {}, "agents": {}}

    def fake_get_llm_client(agent):
        captured["agent"] = agent
        return FakeClient()

    monkeypatch.setattr(workflow_designer, "get_llm_client", fake_get_llm_client)

    response = workflow_designer._ask_llm(
        project_id="project-default-agent",
        messages=[{"role": "user", "content": "Create a writing workflow."}],
        base_workflow={},
        agents={},
    )

    assert captured["agent"] == "project"
    assert response["reply"] == "ok"


def test_workflow_designer_endpoint_can_save_project_override(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    monkeypatch.setattr(workflow_designer, "_ask_llm", lambda **kwargs: {"reply": "ok", "workflow": kwargs["base_workflow"], "agents": {"coding-agent": {"model_route": "executor"}}})
    kanban_service.upsert_project(project_id="designer-save", name="Designer Save")

    response = kanban_routes.kanban_design_project_workflow(
        "designer-save",
        kanban_routes.WorkflowDesignRequest(
            messages=[{"role": "user", "content": "Use the default flow."}],
            save=True,
        ),
    )

    assert response["saved"] is True
    assert kanban_service.get_project_workflow("designer-save")["workflow"]["name"] == "default"
    assert (
        kanban_service.get_project_settings("designer-save")["settings"]["agents"]["coding-agent"]["model_route"]
        == "executor"
    )


def test_workbench_exposes_project_workflow_designer():
    from app.routes import kanban as kanban_routes
    from app.routes.kanban import WORKBENCH_HTML

    assert callable(kanban_routes.kanban_design_project_workflow)
    assert "/conversation" in WORKBENCH_HTML
    assert "Create Project" in WORKBENCH_HTML
    assert "Project Conversation" in WORKBENCH_HTML
    assert "Start Task" in WORKBENCH_HTML
    assert "project_id" in WORKBENCH_HTML
    assert "seedProjectDesignPrompt" in WORKBENCH_HTML
    assert "Workflow JSON" in WORKBENCH_HTML
    assert "Agent Overrides" in WORKBENCH_HTML


def test_dashboard_project_creation_opens_workbench():
    from app.routes.kanban import DASHBOARD_HTML

    assert "New Project" in DASHBOARD_HTML
    assert "Save Project</button>" not in DASHBOARD_HTML
    assert "openWorkbench(projectId, name, true)" in DASHBOARD_HTML
    assert "data-action=\"design-project\"" in DASHBOARD_HTML
    assert ".project-row footer { display: grid;" in DASHBOARD_HTML


def test_project_conversation_can_save_workflow_design(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    def fake_llm(**kwargs):
        return {
            "reply": "Writing workflow ready.",
            "workflow": kwargs["base_workflow"],
            "agents": {"default-agent": {"model_route": "default"}},
        }

    monkeypatch.setattr(workflow_designer, "_ask_llm", fake_llm)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            action="save_design",
            message="Create a writing workflow with topic, research, draft, review, revise, done.",
            save=True,
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "workflow_design"
    assert response["saved"] is True
    assert kanban_service.get_project_settings("writing-project")["settings"]["agents"]["default-agent"]["model_route"] == "default"
    conversation = kanban_routes.kanban_project_conversation("writing-project")["messages"]
    assert [message["role"] for message in conversation] == ["user", "assistant"]
    assert conversation[0]["kind"] == "save_design"


def test_project_conversation_start_task_uses_workflow_entrypoint(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    captured = {}

    def fake_start_workflow_payload(body):
        captured.update(body)
        return {
            "ok": True,
            "task_id": "task-123",
            "poll_url": "/v1/workflows/task-123",
            "events_url": "/v1/workflows/task-123/events",
        }

    monkeypatch.setattr(workflow_routes, "start_workflow_payload", fake_start_workflow_payload)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            action="start_task",
            message="Write a short release note using the project writing flow.",
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "task_started"
    assert response["task_id"] == "task-123"
    assert captured["project_id"] == "writing-project"
    assert captured["interaction_mode"] == "auto"
    assert captured["messages"] == [{"role": "user", "content": "Write a short release note using the project writing flow."}]
    assert captured["workspace"]["root_id"] == "writing-project"
    conversation = kanban_routes.kanban_project_conversation("writing-project")["messages"]
    assert conversation[-1]["task_id"] == "task-123"
