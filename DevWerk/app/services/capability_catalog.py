from __future__ import annotations

from typing import Any

from app.services.plugin_manager import list_enabled_plugin_agents, list_enabled_plugin_mcp_servers
from app.services.tool_protocol import ALL_CAPABILITIES


CAPABILITY_METADATA: dict[str, dict[str, str]] = {
    "workspace.list": {
        "category": "workspace",
        "summary": "List project files through a connected workspace provider.",
    },
    "workspace.read": {
        "category": "workspace",
        "summary": "Read project-root relative files through a connected workspace provider.",
    },
    "workspace.search": {
        "category": "workspace",
        "summary": "Search project files through a connected workspace provider.",
    },
    "process.run": {
        "category": "runtime",
        "summary": "Run a command in a connected local, IDE, CI, or sandbox provider.",
    },
    "project.compile": {
        "category": "verification",
        "summary": "Ask the connected provider to compile or type-check the project.",
    },
    "source.diagnostics": {
        "category": "verification",
        "summary": "Collect syntax, compiler, or IDE diagnostics without assuming an IDE implementation.",
    },
    "browser.cdp": {
        "category": "browser",
        "summary": "Inspect browser runtime evidence through CDP-compatible tooling.",
    },
    "browser.playwright": {
        "category": "browser",
        "summary": "Drive repeatable browser navigation, interaction, assertions, and screenshots.",
    },
    "network.http": {
        "category": "network",
        "summary": "Fetch explicit HTTP URLs through a connected network provider.",
    },
    "network.web": {
        "category": "network",
        "summary": "Search or browse web sources through a connected network provider.",
    },
}


def capability_catalog() -> dict[str, Any]:
    """Return DevWerk's semantic capability contracts and plugin runtime providers."""

    mcp_servers = list_enabled_plugin_mcp_servers()
    agents = list_enabled_plugin_agents()
    by_capability = _plugin_mcp_by_capability(mcp_servers)
    browser_agents = [
        str(agent.get("agent_id") or "")
        for agent in agents
        if _agent_has_browser_capability(agent)
    ]

    capabilities = []
    for name in sorted(ALL_CAPABILITIES):
        meta = CAPABILITY_METADATA.get(name, {})
        capabilities.append(
            {
                "capability": name,
                "category": meta.get("category") or "custom",
                "summary": meta.get("summary") or "Custom capability contract.",
                "plugin_mcp_servers": sorted(by_capability.get(name, set())),
            }
        )

    return {
        "ok": True,
        "capabilities": capabilities,
        "browser_agents": sorted(item for item in browser_agents if item),
        "plugin_mcp_servers": mcp_servers,
        "plugin_agents": agents,
    }


def _plugin_mcp_by_capability(mcp_servers: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for server in mcp_servers:
        server_ref = str(server.get("server_ref") or "")
        text = " ".join(
            [
                str(server.get("id") or ""),
                str(server.get("path") or ""),
                str(server.get("config") or ""),
                str(server.get("resolved_config") or ""),
            ]
        ).lower()
        if "playwright" in text:
            out.setdefault("browser.playwright", set()).add(server_ref)
        if "chrome-devtools" in text or "cdp" in text:
            out.setdefault("browser.cdp", set()).add(server_ref)
        if "http" in text or "fetch" in text or "web" in text:
            out.setdefault("network.http", set()).add(server_ref)
    return out


def _agent_has_browser_capability(agent: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(agent.get("agent_id") or ""),
            str(agent.get("summary") or ""),
            str(agent.get("content") or ""),
            str(agent.get("mcp_servers") or ""),
        ]
    ).lower()
    return "browser" in text or "playwright" in text or "cdp" in text or "chrome-devtools" in text
