from __future__ import annotations

from fastapi.testclient import TestClient


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


def test_global_skill_catalog_exposes_skill_md_entrypoints():
    from app.services.skill_manager import get_global_skill, list_global_skills

    skills = {item["id"]: item for item in list_global_skills()}

    assert "browser-automation" in skills
    assert "network-access" in skills
    assert skills["browser-automation"]["entrypoint"] == "SKILL.md"
    browser = get_global_skill("browser-automation")
    assert "browser.cdp" in browser["content"]
    assert "browser.playwright" in browser["content"]


def test_project_skill_api_round_trip(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        created = client.put(
            "/v1/kanban/projects/skill-project/skills/ui-review",
            json={
                "skill_md": "# UI Review\n\nUse browser evidence before making UI claims.",
                "enabled": True,
            },
        )
        listed = client.get("/v1/kanban/projects/skill-project/skills")
        detail = client.get("/v1/kanban/projects/skill-project/skills/ui-review")

    assert created.status_code == 200
    assert created.json()["skill"]["entrypoint"] == "SKILL.md"
    assert "UI Review" in detail.json()["skill"]["content"]
    assert any(item["id"] == "ui-review" for item in listed.json()["skills"])


def test_workflow_agent_context_resolves_global_and_project_skills(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    from app.services.skill_manager import upsert_project_skill
    from app.services.workflow_engine import _build_agent_context

    kanban_service.update_project_workflow(
        "context-skills",
        {
            "name": "context-skills-flow",
            "columns": [
                {"status_key": "inspect", "title": "Inspect", "position": 10, "transition_to": ["done", "failed"]},
                {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["inspect"]},
            ],
            "actions": {
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "retry": {"to": "inspect"},
                "abandon": {"to": "failed"},
            },
        },
    )
    task = kanban_service.create_task(project_id="context-skills", title="Task")["task"]
    upsert_project_skill(
        "context-skills",
        "local-browser-rule",
        "# Local Browser Rule\n\nAlways capture a screenshot for UI changes.",
    )

    context = _build_agent_context(
        task["id"],
        "inspect",
        "project-agent",
        {
            "project_id": "context-skills",
            "messages": [{"role": "user", "content": "Check the dashboard."}],
            "_workflow_agent_skills": ["browser-automation", "local-browser-rule"],
        },
        {"name": "dynamic"},
        [],
    )

    skills = {skill["id"]: skill for skill in context["skills"]}
    assert "browser-automation" in skills
    assert "browser.cdp" in skills["browser-automation"]["content"]
    assert skills["local-browser-rule"]["scope"] == "project"
    assert "capture a screenshot" in skills["local-browser-rule"]["content"]


def test_project_conversation_slash_commands_update_project_md_and_memory(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        goal = client.post(
            "/v1/kanban/projects/slash-project/conversation",
            json={"message": "/goal Build browser-capable workflow agents."},
        )
        learn = client.post(
            "/v1/kanban/projects/slash-project/conversation",
            json={"message": "/learn Browser claims require CDP or Playwright evidence."},
        )
        distill = client.post(
            "/v1/kanban/projects/slash-project/conversation",
            json={"message": "/distill Keep the project context compact."},
        )
        project_md = client.get("/v1/kanban/projects/slash-project/project-md").json()["content"]
        memory = client.get("/v1/kanban/projects/slash-project/memory").json()["memory"]
        conversation = client.get("/v1/kanban/projects/slash-project/conversation").json()["messages"]

    assert goal.status_code == 200
    assert goal.json()["command"] == "goal"
    assert learn.status_code == 200
    assert distill.status_code == 200
    assert "## Project Goal" in project_md
    assert "Build browser-capable workflow agents." in project_md
    assert "## Learned Notes" in project_md
    assert "Browser claims require CDP or Playwright evidence." in project_md
    assert "## Distilled Context" in project_md
    assert "Keep the project context compact." in project_md
    assert any("Browser claims require" in str(rule) for rule in memory["rules"])
    assert any(message["kind"] == "slash_goal" for message in conversation)
    assert any(message["kind"] == "slash_learn" for message in conversation)
    assert any(message["kind"] == "slash_distill" for message in conversation)


def test_backend_web_ui_exposes_skill_management_and_slash_commands():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert "loadGlobalSkills" in js
    assert "loadProjectSkills" in js
    assert "Global Skill:" in js
    assert "Project Skill:" in js
    assert "/goal project objective" in js
    assert "/learn reusable rule" in js
    assert "/distill compact this project context" in js
    assert "parseSlashCommand" in js
    assert ".slash-hint" in css
