from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _write_plugin(root: Path, plugin_id: str = "ui-observer") -> Path:
    plugin = root / plugin_id
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "commands").mkdir()
    (plugin / "agents").mkdir()
    (plugin / "skills" / "browser-eyes").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_id,
                "version": "0.1.0",
                "description": "Observe browser UI through CDP and Playwright evidence.",
                "author": "DevWerk",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (plugin / "commands" / "audit-ui.md").write_text("# Audit UI\n\nCapture browser evidence.", encoding="utf-8")
    (plugin / "agents" / "ui-observer.md").write_text("# UI Observer\n\nUse browser tools.", encoding="utf-8")
    (plugin / "skills" / "browser-eyes" / "SKILL.md").write_text(
        "# Browser Eyes\n\nUse browser.cdp and browser.playwright before making visual UI claims.",
        encoding="utf-8",
    )
    (plugin / "hooks" / "hooks.json").write_text('{"events":["workflow_column_started"]}', encoding="utf-8")
    (plugin / ".mcp.json").write_text('{"mcpServers":{"playwright":{"command":"npx","args":["@playwright/mcp"]}}}', encoding="utf-8")
    return plugin


def _patch_roots(monkeypatch, tmp_path):
    import app.services.plugin_manager as plugin_manager
    import app.services.skill_manager as skill_manager

    plugins_root = tmp_path / "plugins"
    imported_root = tmp_path / "imported"
    plugins_root.mkdir()
    imported_root.mkdir()
    monkeypatch.setattr(plugin_manager, "_global_plugins_root", lambda: plugins_root)
    monkeypatch.setattr(plugin_manager, "_plugin_import_root", lambda: imported_root)
    monkeypatch.setattr(skill_manager, "_plugin_manager_module", lambda: plugin_manager)
    return plugins_root, imported_root


def test_claude_style_plugin_catalog_discovers_components(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    from app.services.plugin_manager import get_global_plugin, list_global_plugins

    catalog = {item["id"]: item for item in list_global_plugins()}
    assert "ui-observer" in catalog
    assert catalog["ui-observer"]["enabled"] is True
    assert catalog["ui-observer"]["skills_count"] == 1
    assert catalog["ui-observer"]["commands_count"] == 1
    assert catalog["ui-observer"]["agents_count"] == 1
    assert catalog["ui-observer"]["mcp_servers_count"] == 1

    detail = get_global_plugin("ui-observer")
    assert detail["manifest"]["name"] == "ui-observer"
    assert detail["skills"][0]["id"] == "browser-eyes"
    assert detail["skills"][0]["skill_id"] == "ui-observer.browser-eyes"
    assert "browser.playwright" in detail["skills"][0]["content"]


def test_plugin_manifest_custom_paths_supplement_default_discovery(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    plugin = _write_plugin(plugins_root, "custom-paths")
    (plugin / "extra-commands").mkdir()
    (plugin / "specialized-agents").mkdir()
    (plugin / "config").mkdir()
    (plugin / "extra-commands" / "trace-ui.md").write_text("# Trace UI\n\nUse CDP tracing.", encoding="utf-8")
    (plugin / "specialized-agents" / "trace-agent.md").write_text("# Trace Agent\n\nTrace browser state.", encoding="utf-8")
    (plugin / "config" / "hooks.json").write_text('{"SessionStart":[]}', encoding="utf-8")
    (plugin / "config" / "mcp.json").write_text(
        '{"mcpServers":{"cdp-proxy":{"command":"node","args":["${CLAUDE_PLUGIN_ROOT}/servers/cdp.js"]}}}',
        encoding="utf-8",
    )
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "custom-paths",
                "version": "0.2.0",
                "commands": ["./extra-commands"],
                "agents": "./specialized-agents",
                "hooks": "./config/hooks.json",
                "mcpServers": "./config/mcp.json",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    from app.services.plugin_manager import get_global_plugin

    detail = get_global_plugin("custom-paths")
    command_ids = {item["id"] for item in detail["commands"]}
    agent_ids = {item["id"] for item in detail["agents"]}
    mcp_ids = {item["id"] for item in detail["mcp_servers"]}
    hook_ids = {item["id"] for item in detail["hooks"]}

    assert {"audit-ui", "trace-ui"}.issubset(command_ids)
    assert {"ui-observer", "trace-agent"}.issubset(agent_ids)
    assert {"playwright", "cdp-proxy"}.issubset(mcp_ids)
    assert {"hooks", "manifest-path"}.issubset(hook_ids)


def test_plugin_markdown_components_parse_frontmatter(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    plugin = _write_plugin(plugins_root, "frontmatter-plugin")
    (plugin / "commands" / "audit-ui.md").write_text(
        "---\ndescription: Audit UI with browser evidence\nallowed-tools: [Read, Bash]\nmodel: sonnet\nargument-hint: [url]\n---\n\nInspect the UI with screenshots.\n",
        encoding="utf-8",
    )
    (plugin / "agents" / "ui-observer.md").write_text(
        "---\nname: ui-observer\ndescription: Browser evidence observer\nmodel: inherit\ntools: [Read, Bash]\n---\n\nUse browser tools before making visual claims.\n",
        encoding="utf-8",
    )
    (plugin / "skills" / "browser-eyes" / "SKILL.md").write_text(
        "---\nname: browser-eyes\ndescription: Require CDP or Playwright evidence\n---\n\n# Browser Eyes\n\nUse browser.cdp.\n",
        encoding="utf-8",
    )

    from app.services.plugin_manager import get_global_plugin

    detail = get_global_plugin("frontmatter-plugin")
    command = detail["commands"][0]
    agent = detail["agents"][0]
    skill = detail["skills"][0]

    assert command["summary"] == "Audit UI with browser evidence"
    assert command["frontmatter"]["model"] == "sonnet"
    assert command["frontmatter"]["allowed-tools"] == ["Read", "Bash"]
    assert "Inspect the UI" in command["body"]
    assert "---" not in command["body"]
    assert agent["summary"] == "Browser evidence observer"
    assert agent["frontmatter"]["name"] == "ui-observer"
    assert agent["frontmatter"]["tools"] == ["Read", "Bash"]
    assert skill["summary"] == "Require CDP or Playwright evidence"
    assert skill["frontmatter"]["name"] == "browser-eyes"


def test_plugin_command_frontmatter_normalizes_allowed_tools_and_argument_hint(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    plugin = _write_plugin(plugins_root, "command-tools")
    (plugin / "commands" / "review.md").write_text(
        "---\ndescription: Review with browser evidence\nallowed-tools: Read, Bash(git:*), browser.playwright\nmodel: sonnet\nargument-hint: [target]\n---\n\nReview the requested target.\n",
        encoding="utf-8",
    )

    from app.services.plugin_manager import get_plugin_command, list_enabled_plugin_commands

    command = get_plugin_command("command-tools:review")
    listed = {item["command_id"]: item for item in list_enabled_plugin_commands()}["command-tools:review"]

    assert command["allowed_tools"] == ["Read", "Bash(git:*)", "browser.playwright"]
    assert command["argument_hint"] == "[target]"
    assert command["model"] == "sonnet"
    assert listed["allowed_tools"] == command["allowed_tools"]
    assert listed["argument_hint"] == "[target]"


def test_plugin_api_round_trip_and_enable_toggle(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        created = client.put(
            "/v1/plugins/ui-observer",
            json={
                "manifest": {
                    "name": "ui-observer",
                    "version": "0.1.0",
                    "description": "Browser evidence plugin",
                },
                "skills": {
                    "browser-eyes": "# Browser Eyes\n\nUse browser.cdp and browser.playwright.",
                },
                "commands": {"audit-ui": "# Audit UI\n\nCheck the browser."},
                "agents": {"ui-observer": "# UI Observer\n\nInspect UI."},
                "mcp": {"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp"]}}},
                "hooks": {"events": ["workflow_column_started"]},
            },
        )
        disabled = client.patch("/v1/plugins/ui-observer", json={"enabled": False})
        detail = client.get("/v1/plugins/ui-observer")
        listed = client.get("/v1/plugins")

    assert created.status_code == 200
    assert disabled.status_code == 200
    assert disabled.json()["plugin"]["enabled"] is False
    assert detail.json()["plugin"]["enabled"] is False
    assert any(item["id"] == "ui-observer" for item in listed.json()["plugins"])


def test_plugin_api_rejects_invalid_semver(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.put(
            "/v1/plugins/bad-version",
            json={"manifest": {"version": "release-one"}},
        )

    assert response.status_code == 400
    assert "invalid plugin version" in response.text


def test_plugin_validation_and_uninstall_api(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    source = _write_plugin(tmp_path / "source", "validated-plugin")

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        validation = client.post("/v1/plugins/validate", json={"source_path": str(source)})
        imported = client.post("/v1/plugins/import", json={"source_path": str(source)})
        listed_before = client.get("/v1/plugins")
        removed = client.delete("/v1/plugins/validated-plugin")
        listed_after = client.get("/v1/plugins")

    assert validation.status_code == 200
    assert validation.json()["validation"]["ok"] is True
    assert validation.json()["validation"]["components"]["skills"] == 1
    assert imported.status_code == 200
    assert listed_before.status_code == 200
    assert any(item["id"] == "validated-plugin" for item in listed_before.json()["plugins"])
    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    assert not (plugins_root / "validated-plugin").exists()
    assert not any(item["id"] == "validated-plugin" for item in listed_after.json()["plugins"])


def test_plugin_settings_markdown_round_trip_and_frontmatter(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root, "settings-plugin")

    from app.services.plugin_manager import get_plugin_settings, update_plugin_settings

    updated = update_plugin_settings(
        "settings-plugin",
        "---\nenabled: true\nstrict_mode: false\nmax_retries: 3\n---\n\n# Settings\nUse browser evidence.\n",
    )
    loaded = get_plugin_settings("settings-plugin")

    assert updated["plugin_id"] == "settings-plugin"
    assert updated["frontmatter"]["enabled"] is True
    assert updated["frontmatter"]["strict_mode"] is False
    assert updated["frontmatter"]["max_retries"] == 3
    assert "Use browser evidence" in loaded["body"]
    assert (plugins_root / "settings-plugin" / ".devwerk-plugin-settings.md").is_file()


def test_plugin_settings_api(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root, "settings-api")

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        put = client.put(
            "/v1/plugins/settings-api/settings",
            json={"content": "---\nenabled: true\n---\n\n# API Settings\n"},
        )
        got = client.get("/v1/plugins/settings-api/settings")

    assert put.status_code == 200
    assert got.status_code == 200
    assert got.json()["settings"]["frontmatter"]["enabled"] is True
    assert "# API Settings" in got.json()["settings"]["content"]


def test_plugin_validation_reports_invalid_manifest(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)
    broken = tmp_path / "broken-plugin"
    (broken / ".claude-plugin").mkdir(parents=True)
    (broken / ".claude-plugin" / "plugin.json").write_text('{"name":"Bad Plugin Name","commands":"commands"}', encoding="utf-8")

    from app.services.plugin_manager import validate_plugin_source

    validation = validate_plugin_source(str(broken))

    assert validation["ok"] is False
    assert any("invalid plugin name" in issue for issue in validation["issues"])
    assert any("must start with './'" in issue for issue in validation["issues"])


def test_plugin_validation_and_import_reject_invalid_semver(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)
    broken = tmp_path / "bad-version"
    (broken / ".claude-plugin").mkdir(parents=True)
    (broken / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"bad-version","version":"release-one"}',
        encoding="utf-8",
    )

    from app.services.plugin_manager import import_global_plugin, validate_plugin_source

    validation = validate_plugin_source(str(broken))

    assert validation["ok"] is False
    assert any("invalid plugin version" in issue for issue in validation["issues"])
    try:
        import_global_plugin(str(broken))
    except ValueError as exc:
        assert "invalid plugin version" in str(exc)
    else:
        raise AssertionError("invalid semver plugin import should fail")


def test_plugin_validation_rejects_duplicate_component_ids(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)
    source = _write_plugin(tmp_path / "source", "duplicate-components")
    (source / "more-commands").mkdir()
    (source / "more-agents").mkdir()
    (source / "commands" / "build.md").write_text("# Build\n\nDefault build command.", encoding="utf-8")
    (source / "more-commands" / "build.md").write_text("# Build\n\nDuplicate build command.", encoding="utf-8")
    (source / "agents" / "reviewer.md").write_text("# Reviewer\n\nDefault reviewer.", encoding="utf-8")
    (source / "more-agents" / "reviewer.md").write_text("# Reviewer\n\nDuplicate reviewer.", encoding="utf-8")
    (source / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "duplicate-components",
                "version": "0.1.0",
                "commands": "./more-commands",
                "agents": "./more-agents",
            }
        ),
        encoding="utf-8",
    )

    from app.services.plugin_manager import import_global_plugin, validate_plugin_source

    validation = validate_plugin_source(str(source))

    assert validation["ok"] is False
    assert any("duplicate commands id: build" in issue for issue in validation["issues"])
    assert any("duplicate agents id: reviewer" in issue for issue in validation["issues"])
    try:
        import_global_plugin(str(source))
    except ValueError as exc:
        assert "duplicate commands id: build" in str(exc)
    else:
        raise AssertionError("duplicate component plugin import should fail")


def test_plugin_import_copies_claude_plugin_directory(monkeypatch, tmp_path):
    plugins_root, imported_root = _patch_roots(monkeypatch, tmp_path)
    source = _write_plugin(tmp_path / "source")

    from app.services.plugin_manager import import_global_plugin, list_global_plugins

    imported = import_global_plugin(str(source))

    assert imported["id"] == "ui-observer"
    assert (plugins_root / "ui-observer" / ".claude-plugin" / "plugin.json").is_file()
    assert imported_root.exists()
    assert "ui-observer" in {item["id"] for item in list_global_plugins()}


def test_marketplace_catalog_and_import_by_name(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    marketplace_root = tmp_path / "marketplace"
    source = _write_plugin(marketplace_root / "plugins", "market-ui")
    marketplace_path = marketplace_root / ".claude-plugin" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "devwerk-marketplace",
                "plugins": [
                    {
                        "name": "market-ui",
                        "description": "Marketplace UI observer",
                        "source": "./plugins/market-ui",
                        "category": "development",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    from app.services.plugin_manager import import_marketplace_plugin, list_marketplace_plugins

    catalog = list_marketplace_plugins(str(marketplace_path))
    assert catalog["marketplace"]["name"] == "devwerk-marketplace"
    assert catalog["plugins"][0]["name"] == "market-ui"
    assert catalog["plugins"][0]["source_path"] == str(source.resolve())
    assert catalog["plugins"][0]["installed"] is False

    imported = import_marketplace_plugin(str(marketplace_path), "market-ui")

    assert imported["id"] == "market-ui"
    assert (plugins_root / "market-ui" / ".claude-plugin" / "plugin.json").is_file()
    assert list_marketplace_plugins(str(marketplace_path))["plugins"][0]["installed"] is True


def test_plugin_marketplace_api(monkeypatch, tmp_path):
    _patch_roots(monkeypatch, tmp_path)
    marketplace_root = tmp_path / "marketplace"
    _write_plugin(marketplace_root / "plugins", "market-ui")
    marketplace_path = marketplace_root / ".claude-plugin" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps({"name": "devwerk-marketplace", "plugins": [{"name": "market-ui", "source": "./plugins/market-ui"}]}),
        encoding="utf-8",
    )

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        catalog = client.get("/v1/plugins/marketplace", params={"marketplace_path": str(marketplace_path)})
        imported = client.post(
            "/v1/plugins/import-marketplace",
            json={"marketplace_path": str(marketplace_path), "plugin_name": "market-ui"},
        )

    assert catalog.status_code == 200
    assert catalog.json()["plugins"][0]["name"] == "market-ui"
    assert imported.status_code == 200
    assert imported.json()["plugin"]["id"] == "market-ui"


def test_plugin_skills_are_available_to_agent_skill_resolution(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    from app.services.skill_manager import get_global_skill, list_global_skills, resolve_agent_skills

    skills = {item["id"]: item for item in list_global_skills()}
    assert "ui-observer.browser-eyes" in skills
    assert skills["ui-observer.browser-eyes"]["scope"] == "plugin"

    detail = get_global_skill("ui-observer.browser-eyes")
    assert detail["plugin_id"] == "ui-observer"
    assert "browser.cdp" in detail["content"]

    resolved = resolve_agent_skills("plugin-project", ["ui-observer.browser-eyes"])
    assert resolved[0]["id"] == "ui-observer.browser-eyes"
    assert resolved[0]["scope"] == "plugin"


def test_plugin_commands_are_available_as_slash_commands(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    from app.services.plugin_manager import get_plugin_command, list_enabled_plugin_commands

    commands = {item["command_id"]: item for item in list_enabled_plugin_commands()}
    assert "ui-observer:audit-ui" in commands
    assert commands["ui-observer:audit-ui"]["slash"] == "/ui-observer:audit-ui"

    command = get_plugin_command("ui-observer:audit-ui")
    assert command["plugin_id"] == "ui-observer"
    assert "Capture browser evidence" in command["content"]


def test_plugin_agents_are_available_as_runtime_catalog(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    from app.services.plugin_manager import get_plugin_agent, list_enabled_plugin_agents

    agents = {item["agent_id"]: item for item in list_enabled_plugin_agents()}
    assert "ui-observer:ui-observer" in agents
    assert agents["ui-observer:ui-observer"]["plugin_id"] == "ui-observer"
    assert agents["ui-observer:ui-observer"]["scope"] == "plugin"
    assert "Use browser tools" in agents["ui-observer:ui-observer"]["content"]

    agent = get_plugin_agent("ui-observer:ui-observer")
    assert agent["agent_id"] == "ui-observer:ui-observer"
    assert agent["summary"] == "UI Observer"


def test_plugin_agents_api(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        listed = client.get("/v1/plugins/agents")
        detail = client.get("/v1/plugins/agents/ui-observer:ui-observer")

    assert listed.status_code == 200
    assert any(item["agent_id"] == "ui-observer:ui-observer" for item in listed.json()["agents"])
    assert detail.status_code == 200
    assert detail.json()["agent"]["plugin_id"] == "ui-observer"


def test_plugin_hooks_and_mcp_servers_are_available_as_runtime_catalog(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    from app.services.plugin_manager import list_enabled_plugin_hooks, list_enabled_plugin_mcp_servers

    hooks = {item["hook_id"]: item for item in list_enabled_plugin_hooks()}
    servers = {item["server_ref"]: item for item in list_enabled_plugin_mcp_servers()}

    assert "ui-observer:hooks" in hooks
    assert hooks["ui-observer:hooks"]["plugin_id"] == "ui-observer"
    assert hooks["ui-observer:hooks"]["payload"]["events"] == ["workflow_column_started"]
    assert "ui-observer:playwright" in servers
    assert servers["ui-observer:playwright"]["config"]["command"] == "npx"
    assert servers["ui-observer:playwright"]["scope"] == "plugin"


def test_plugin_mcp_server_runtime_config_resolves_plugin_root(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    plugin = _write_plugin(plugins_root, "runtime-mcp")
    (plugin / "servers").mkdir()
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "browser": {
                        "command": "node",
                        "args": ["${CLAUDE_PLUGIN_ROOT}/servers/browser.js"],
                        "env": {"PLUGIN_HOME": "${DEVWERK_PLUGIN_ROOT}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    from app.services.plugin_manager import get_plugin_agent, list_enabled_plugin_mcp_servers

    server = {item["server_ref"]: item for item in list_enabled_plugin_mcp_servers()}["runtime-mcp:browser"]
    assert server["plugin_root"] == str(plugin)
    assert server["resolved_config"]["args"][0] == str(plugin / "servers" / "browser.js")
    assert server["resolved_config"]["env"]["PLUGIN_HOME"] == str(plugin)

    agent = get_plugin_agent("runtime-mcp:ui-observer")
    assert agent["mcp_servers"][0]["server_ref"] == "runtime-mcp:browser"
    assert agent["mcp_servers"][0]["resolved_config"]["command"] == "node"


def test_plugin_hooks_and_mcp_servers_api(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as client:
        hooks = client.get("/v1/plugins/hooks")
        servers = client.get("/v1/plugins/mcp-servers")

    assert hooks.status_code == 200
    assert any(item["hook_id"] == "ui-observer:hooks" for item in hooks.json()["hooks"])
    assert servers.status_code == 200
    assert any(item["server_ref"] == "ui-observer:playwright" for item in servers.json()["mcp_servers"])


def test_plugin_agent_is_injected_into_workflow_agent_context(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    class FakeSettings:
        def __init__(self, db_path, session_dir):
            self.devwerk_db_path = str(db_path)
            self.devwerk_usage_tracking = False
            self.devwerk_session_dir = str(session_dir)

    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    from app.services.workflow_engine import _build_agent_context

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False

    kanban_service.update_project_workflow(
        "plugin-agent-project",
        {
            "name": "plugin-agent-flow",
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
    task = kanban_service.create_task(project_id="plugin-agent-project", title="Inspect UI")["task"]
    context = _build_agent_context(
        task["id"],
        "inspect",
        "ui-observer:ui-observer",
        {
            "project_id": "plugin-agent-project",
            "messages": [{"role": "user", "content": "Inspect the dashboard."}],
            "_workflow_agent_id": "ui-observer:ui-observer",
            "_workflow_agent_skills": ["ui-observer.browser-eyes"],
        },
        {"name": "custom"},
        [],
    )

    assert context["plugin_agent"]["agent_id"] == "ui-observer:ui-observer"
    assert "Use browser tools" in context["plugin_agent"]["content"]
    assert context["skills"][0]["id"] == "ui-observer.browser-eyes"


def test_project_conversation_executes_plugin_slash_command(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    _write_plugin(plugins_root)

    class FakeSettings:
        def __init__(self, db_path, session_dir):
            self.devwerk_db_path = str(db_path)
            self.devwerk_usage_tracking = False
            self.devwerk_session_dir = str(session_dir)

    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.main as main_module

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False

    app = main_module.create_app()
    with TestClient(app) as client:
        result = client.post(
            "/v1/kanban/projects/plugin-command-project/conversation",
            json={"message": "/ui-observer:audit-ui inspect dashboard"},
        )
        events = client.get("/v1/kanban/events", params={"project_id": "plugin-command-project"}).json()["events"]

    assert result.status_code == 200
    payload = result.json()["payload"]
    assert payload["command"] == "ui-observer:audit-ui"
    assert payload["argument"] == "inspect dashboard"
    assert "Capture browser evidence" in payload["content"]
    assert any(event["event_type"] == "project_plugin_command" for event in events)


def test_project_conversation_plugin_slash_command_uses_body_not_frontmatter(monkeypatch, tmp_path):
    plugins_root, _ = _patch_roots(monkeypatch, tmp_path)
    plugin = _write_plugin(plugins_root)
    (plugin / "commands" / "audit-ui.md").write_text(
        "---\ndescription: Audit UI with browser evidence\nallowed-tools: [Read, Bash]\n---\n\nCapture browser evidence with screenshots.\n",
        encoding="utf-8",
    )

    class FakeSettings:
        def __init__(self, db_path, session_dir):
            self.devwerk_db_path = str(db_path)
            self.devwerk_usage_tracking = False
            self.devwerk_session_dir = str(session_dir)

    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.main as main_module

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False

    app = main_module.create_app()
    with TestClient(app) as client:
        result = client.post(
            "/v1/kanban/projects/plugin-command-frontmatter/conversation",
            json={"message": "/ui-observer:audit-ui inspect dashboard"},
        )
        events = client.get("/v1/kanban/events", params={"project_id": "plugin-command-frontmatter"}).json()["events"]

    assert result.status_code == 200
    payload = result.json()["payload"]
    assert payload["summary"] == "Audit UI with browser evidence"
    assert payload["frontmatter"]["allowed-tools"] == ["Read", "Bash"]
    assert payload["instructions"].strip() == "Capture browser evidence with screenshots."
    assert "---" not in payload["instructions"]
    assert payload["content"].startswith("---")
    plugin_event = next(event for event in events if event["event_type"] == "project_plugin_command")
    assert plugin_event["payload"]["instructions"].strip() == "Capture browser evidence with screenshots."


def test_backend_web_ui_exposes_plugin_management_controls():
    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "loadGlobalPlugins" in js
    assert "Global Plugins" in js
    assert "pluginImportPath" in js
    assert "importGlobalPlugin" in js
    assert "pluginMarketplacePath" in js
    assert "loadPluginMarketplace" in js
    assert "importMarketplacePlugin" in js
    assert "removeGlobalPlugin" in js
    assert "validateGlobalPlugin" in js
    assert "pluginCommands" in js
    assert "pluginAgents" in js
    assert "loadPluginAgents" in js
    assert "pluginHooks" in js
    assert "pluginMcpServers" in js
    assert "loadPluginHooks" in js
    assert "loadPluginMcpServers" in js
    assert "loadPluginSettings" in js
    assert "Plugin Settings: " in js
    assert "togglePluginEnabled" in js
    assert "/plugins" in js
