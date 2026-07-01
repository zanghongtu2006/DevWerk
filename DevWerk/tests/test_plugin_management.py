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


def test_backend_web_ui_exposes_plugin_management_controls():
    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "loadGlobalPlugins" in js
    assert "Global Plugins" in js
    assert "pluginImportPath" in js
    assert "importGlobalPlugin" in js
    assert "pluginMarketplacePath" in js
    assert "loadPluginMarketplace" in js
    assert "importMarketplacePlugin" in js
    assert "togglePluginEnabled" in js
    assert "/plugins" in js
