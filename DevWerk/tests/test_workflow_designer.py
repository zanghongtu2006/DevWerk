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
    from app.routes.kanban import WORKBENCH_HTML

    assert "/workflow/design" in WORKBENCH_HTML
    assert "Create Project" in WORKBENCH_HTML
    assert "project_id" in WORKBENCH_HTML
    assert "seedProjectDesignPrompt" in WORKBENCH_HTML
    assert "Workflow JSON" in WORKBENCH_HTML
    assert "Agent Overrides" in WORKBENCH_HTML


def test_dashboard_project_creation_opens_workbench():
    from app.routes.kanban import DASHBOARD_HTML

    assert "New Project" in DASHBOARD_HTML
    assert "Save Project</button>" not in DASHBOARD_HTML
    assert "openWorkbench(projectId, name, true)" in DASHBOARD_HTML
    assert "Design Workflow" in DASHBOARD_HTML
