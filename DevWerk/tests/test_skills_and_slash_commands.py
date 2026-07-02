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


def test_workflow_agent_context_includes_capability_catalog_and_client_offers(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    from app.services.workflow_engine import _build_agent_context

    kanban_service.update_project_workflow(
        "context-capabilities",
        {
            "name": "context-capabilities-flow",
            "columns": [
                {"status_key": "inspect", "title": "Inspect", "position": 10, "transition_to": ["done", "failed"]},
                {"status_key": "done", "title": "Done", "position": 90, "terminal": "success", "transition_to": []},
                {"status_key": "failed", "title": "Failed", "position": 99, "terminal": "failure", "transition_to": ["inspect"]},
            ],
            "actions": {
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "retry": {"to": "inspect"},
                "abandon": {"to": "failed"},
            },
        },
    )
    task = kanban_service.create_task(project_id="context-capabilities", title="Task")["task"]

    context = _build_agent_context(
        task["id"],
        "inspect",
        "browser-agent",
        {
            "project_id": "context-capabilities",
            "messages": [{"role": "user", "content": "Inspect the dashboard with browser evidence."}],
            "_workflow_agent_capabilities": ["browser.cdp", "browser.playwright", "network.web"],
            "client_capabilities": {
                "provider": "vscode",
                "capabilities": [
                    {"capability": "browser.playwright", "implementation": "mcp__playwright__browser_snapshot"},
                    {"capability": "network.web", "implementation": "web.search"},
                ],
            },
        },
        {"name": "dynamic"},
        [],
    )

    capabilities = context["capabilities"]
    catalog_by_name = {item["capability"]: item for item in capabilities["catalog"]}
    client_by_name = {item["capability"]: item for item in capabilities["client_offers"]}

    assert capabilities["agent"] == ["browser.cdp", "browser.playwright", "network.web"]
    assert catalog_by_name["browser.cdp"]["category"] == "browser"
    assert catalog_by_name["browser.playwright"]["category"] == "browser"
    assert client_by_name["browser.playwright"]["implementation"] == "mcp__playwright__browser_snapshot"
    assert client_by_name["network.web"]["provider"] == "vscode"


def test_browser_and_network_capabilities_are_first_class_tool_requests():
    from app.services.tool_protocol import ALL_CAPABILITIES, normalize_tool_requests
    from app.services.validation import validate_model_response

    for capability in ("browser.cdp", "browser.playwright", "network.http", "network.web"):
        assert capability in ALL_CAPABILITIES

    requests = normalize_tool_requests(
        [
            {"id": "cdp-1", "tool": "browser.cdp", "args": {"method": "capture_console"}},
            {"id": "pw-1", "tool": "browser.playwright", "args": {"command": "screenshot", "url": "http://127.0.0.1:8000"}},
            {"id": "http-1", "tool": "network.http", "args": {"uri": "https://example.com"}},
            {"id": "web-1", "tool": "network.web", "args": {"q": "Playwright documentation"}},
        ]
    )

    assert requests[0]["args"]["action"] == "capture_console"
    assert requests[1]["args"]["action"] == "screenshot"
    assert requests[2]["args"]["url"] == "https://example.com"
    assert requests[2]["args"]["method"] == "GET"
    assert requests[3]["args"]["query"] == "Playwright documentation"

    response = {
        "reply": "Patch plus browser verification request.",
        "ops": [{"op": "create_file", "path": "README.md", "language": "markdown", "content": "# Demo\n"}],
        "tool_requests": [{"id": "pw-verify", "tool": "browser.playwright", "args": {"action": "screenshot"}}],
        "patch_ops": [],
        "done": False,
    }
    validate_model_response(response)
    assert response["tool_requests"][0]["tool"] == "browser.playwright"


def test_builtin_browser_toolkit_plugin_exposes_mcp_servers():
    from app.services.plugin_manager import get_global_plugin, list_enabled_plugin_agents, list_enabled_plugin_mcp_servers

    plugin = get_global_plugin("browser-toolkit")
    servers = {server["id"]: server for server in plugin["mcp_servers"]}
    agents = {agent["agent_id"]: agent for agent in list_enabled_plugin_agents()}
    runtime = {server["server_ref"]: server for server in list_enabled_plugin_mcp_servers()}

    assert servers["playwright"]["config"]["command"] == "npx"
    assert "@playwright/mcp@latest" in servers["playwright"]["config"]["args"]
    assert servers["chrome-devtools"]["config"]["command"] == "npx"
    assert "chrome-devtools-mcp@latest" in servers["chrome-devtools"]["config"]["args"]
    assert "browser-toolkit:browser-agent" in agents
    assert "browser.playwright" in agents["browser-toolkit:browser-agent"]["content"]
    assert {server["id"] for server in agents["browser-toolkit:browser-agent"]["mcp_servers"]} == {
        "playwright",
        "chrome-devtools",
    }
    assert runtime["browser-toolkit:playwright"]["resolved_config"]["command"] == "npx"
    assert runtime["browser-toolkit:chrome-devtools"]["resolved_config"]["command"] == "npx"


def test_capability_catalog_api_exposes_browser_network_and_plugin_runtime(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.get("/v1/capabilities")

    assert response.status_code == 200
    payload = response.json()
    capabilities = {item["capability"]: item for item in payload["capabilities"]}
    assert capabilities["browser.cdp"]["category"] == "browser"
    assert capabilities["browser.playwright"]["category"] == "browser"
    assert capabilities["network.http"]["category"] == "network"
    assert capabilities["network.web"]["category"] == "network"
    assert "browser-toolkit:playwright" in capabilities["browser.playwright"]["plugin_mcp_servers"]
    assert "browser-toolkit:chrome-devtools" in capabilities["browser.cdp"]["plugin_mcp_servers"]
    assert "browser-toolkit:browser-agent" in payload["browser_agents"]


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


def test_project_slash_command_catalog_includes_builtin_and_plugin_commands(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    import app.services.plugin_manager as plugin_manager
    import app.main as main_module

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    monkeypatch.setattr(plugin_manager, "_global_plugins_root", lambda: plugins_root)
    plugin = plugins_root / "ui-observer"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "commands").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"ui-observer","version":"0.1.0","description":"UI observer"}',
        encoding="utf-8",
    )
    (plugin / "commands" / "audit-ui.md").write_text(
        "---\ndescription: Audit UI with browser evidence\nallowed-tools: Read, browser.playwright\nmodel: sonnet\nargument-hint: [url]\n---\n\nUse browser evidence.",
        encoding="utf-8",
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.get("/v1/kanban/projects/slash-catalog/slash-commands")

    assert response.status_code == 200
    commands = {item["command"]: item for item in response.json()["commands"]}
    assert "/goal" in commands
    assert commands["/goal"]["source"] == "builtin"
    assert "/learn" in commands
    assert "/distill" in commands
    assert "/ui-observer:audit-ui" in commands
    assert commands["/ui-observer:audit-ui"]["source"] == "plugin"
    assert commands["/ui-observer:audit-ui"]["summary"] == "Audit UI with browser evidence"
    assert commands["/ui-observer:audit-ui"]["allowed_tools"] == ["Read", "browser.playwright"]
    assert commands["/ui-observer:audit-ui"]["model"] == "sonnet"
    assert commands["/ui-observer:audit-ui"]["argument_hint"] == "[url]"


def test_backend_web_ui_exposes_skill_management_and_slash_commands():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert "loadGlobalSkills" in js
    assert "loadProjectSkills" in js
    assert "Global Skill:" in js
    assert "Project Skill:" in js
    assert "createGlobalSkill" in js
    assert "globalSkillId" in js
    assert "globalSkillMd" in js
    assert "loadSlashCommands" in js
    assert "loadCapabilities" in js
    assert "/capabilities" in js
    assert "capabilityCatalogCard" in js
    assert 'command:"/goal", argument_hint:"project objective"' in js
    assert 'command:"/learn", argument_hint:"reusable rule"' in js
    assert 'command:"/distill", argument_hint:"compact this project context"' in js
    assert "data-slash-command" in js
    assert "parseSlashCommand" in js
    assert ".slash-hint" in css


def test_backend_web_ui_uses_standard_project_skill_api_and_create_form():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "/skills/projects/" in js
    assert "/kanban/projects/${encodeURIComponent(state.projectId)}/skills" not in js
    assert "createProjectSkill" in js
    assert "projectSkillId" in js
    assert "projectSkillMd" in js
