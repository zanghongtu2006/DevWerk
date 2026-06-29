from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.protocol import IdeChatResponse


class FakeSettings:
    def __init__(self, tmp_path):
        self.devwerk_db_path = str(tmp_path / "workflow.db")
        self.devwerk_session_dir = str(tmp_path / "sessions")
        self.devwerk_usage_tracking = False
        self.app_env = "test"
        self.llm_provider_name = "test"
        self.workflow_supervisor_enabled = False

    @property
    def is_production(self):
        return False

    def validate_provider(self, agent="default"):
        return None

    def get_llm_config(self, agent="default"):
        return {"agent": agent, "protocol": "ollama", "api_key": None, "model": "test"}


def configure(monkeypatch, tmp_path):
    import app.main as main_module
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.usage as usage_service

    fake = FakeSettings(tmp_path)
    monkeypatch.setattr(main_module, "settings", lambda: fake)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    monkeypatch.setattr(usage_service, "settings", lambda: fake)
    kanban_service._initialized = False
    usage_service._initialized = False
    return fake, kanban_service


def code_flow() -> dict:
    return {
        "name": "apply-check-flow",
        "columns": [
            {"status_key": "implement", "title": "Implement", "position": 10, "transition_to": ["apply_gate", "blocked"]},
            {"status_key": "apply_gate", "title": "Apply Gate", "position": 20, "transition_to": ["verified", "repair", "blocked"]},
            {"status_key": "repair", "title": "Repair", "position": 30, "transition_to": ["apply_gate", "blocked"]},
            {"status_key": "verified", "title": "Verified", "position": 40, "transition_to": ["complete", "repair", "blocked"]},
            {"status_key": "complete", "title": "Complete", "position": 90, "transition_to": []},
            {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["implement"]},
        ],
        "actions": {
            "ready_for_apply": {"to": "apply_gate"},
            "apply_succeeded": {"to": "verified"},
            "request_rework": {"to": "repair"},
            "verification_failed": {"to": "repair"},
            "workflow_done": {"to": "complete"},
            "fail": {"to": "blocked"},
            "abandon": {"to": "blocked"},
            "retry": {"to": "implement"},
        },
    }


def test_ide_error_response_can_omit_reply():
    response = IdeChatResponse(ok=False, done=True, error_code="BAD_REQUEST")

    assert response.reply == ""
    assert response.ok is False


def test_web_ui_uses_external_static_assets():
    from app.routes.web_ui import render_web_ui

    html = render_web_ui("projects")
    assert '<link rel="stylesheet" href="/web/static/dashboard.css"' in html
    assert '<script src="/web/static/dashboard.js" defer>' in html
    assert "<style>" not in html
    assert "const API" not in html
    assert Path("app/web/static/dashboard.css").is_file()
    assert Path("app/web/static/dashboard.js").is_file()


def test_dashboard_route_serves_shell_and_static_assets(monkeypatch, tmp_path):
    import app.main as main_module

    configure(monkeypatch, tmp_path)
    app = main_module.create_app()
    with TestClient(app) as client:
        page = client.get("/dashboard")
        css = client.get("/web/static/dashboard.css")
        js = client.get("/web/static/dashboard.js")

    assert page.status_code == 200
    assert "DevWerk" in page.text
    assert css.status_code == 200
    assert ".app-shell" in css.text
    assert js.status_code == 200
    assert "refreshAll" in js.text


def test_project_requires_explicit_workflow_before_task(monkeypatch, tmp_path):
    _, kanban_service = configure(monkeypatch, tmp_path)
    project_id = "no-default-columns"
    kanban_service.upsert_project(project_id=project_id, name="No defaults")

    assert kanban_service.list_columns(project_id) == []
    try:
        kanban_service.create_task(project_id=project_id, title="Should fail")
    except ValueError as exc:
        assert "project workflow is not configured" in str(exc)
    else:
        raise AssertionError("task creation must fail without project workflow")


def test_workflow_action_protocol_uses_project_defined_transitions(monkeypatch, tmp_path):
    _, kanban_service = configure(monkeypatch, tmp_path)
    import app.services.workflow as workflow_service

    project_id = "action-smoke"
    kanban_service.update_project_workflow(project_id, code_flow())
    task = kanban_service.create_task(
        project_id=project_id,
        title="Apply generated change",
        status_key="apply_gate",
    )["task"]

    result = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": True,
            "snapshot_id": "20260629-apply",
            "changed_paths": ["src/App.java"],
            "verification": {},
        },
    )

    assert result["task"]["status_key"] == "complete"
    events = kanban_service.list_events(project_id=project_id, task_id=task["id"], limit=50)["events"]
    assert "workflow_transition_decided" in [event["event_type"] for event in events]
    memory = __import__("app.services.session_store", fromlist=["read_project_memory"]).read_project_memory(project_id)
    assert "src/App.java" in memory["paths"]


def test_failed_verification_returns_to_project_repair_column(monkeypatch, tmp_path):
    _, kanban_service = configure(monkeypatch, tmp_path)
    import app.services.workflow as workflow_service

    project_id = "verification-smoke"
    kanban_service.update_project_workflow(project_id, code_flow())
    task = kanban_service.create_task(
        project_id=project_id,
        title="Compile checked change",
        status_key="apply_gate",
    )["task"]

    result = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": True,
            "changed_paths": ["src/main/java/org/example/dto/TenantCreateRequest.java"],
            "verification": {
                "required": ["compile"],
                "results": {"compile": "failed"},
                "tool_results": [{"id": "compile", "tool": "project.compile", "ok": False, "error": "illegal escape"}],
            },
        },
    )

    assert result["task"]["status_key"] == "repair"
    task_detail = kanban_service.get_task(task["id"])["task"]
    assert "verification_failed" in [event["event_type"] for event in task_detail["events"]]


def test_packaging_scripts_cover_devwerk_and_intellij_plugin():
    root = Path(__file__).resolve().parents[2]

    scripts = {
        "scripts/package-all.ps1": ["package-devwerk.ps1", "package-idea-plugin.ps1"],
        "scripts/package-all.sh": ["package-devwerk.sh", "package-idea-plugin.sh"],
        "scripts/package-all.bat": ["package-all.ps1"],
        "scripts/package-devwerk.ps1": ["config/llm.json", "Compress-Archive", "install.bat", "start.sh"],
        "scripts/package-devwerk.sh": ["config/llm.json", "install.sh", "start.bat"],
        "scripts/package-idea-plugin.ps1": ["buildPlugin", "build\\distributions"],
        "scripts/package-idea-plugin.sh": ["buildPlugin", "build/distributions"],
    }
    for relative, expected in scripts.items():
        text = (root / relative).read_text(encoding="utf-8")
        for needle in expected:
            assert needle in text


def test_project_memory_does_not_treat_source_map_symbol_kinds_as_frameworks():
    from app.services.session_store import _normalize_project_memory

    memory = _normalize_project_memory(
        "memory-smoke",
        {"frameworks": ["class", "method", "source", "Spring Boot"]},
    )

    assert memory["frameworks"] == ["Spring Boot"]
