from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any


MANIFEST_PATH = ".claude-plugin/plugin.json"
STATE_PATH = ".devwerk-plugin.json"
SETTINGS_PATH = ".devwerk-plugin-settings.md"
SKILL_ENTRYPOINT = "SKILL.md"
MANIFEST_ALLOWED_FIELDS = {
    "name",
    "version",
    "description",
    "author",
    "license",
    "homepage",
    "repository",
    "keywords",
    "commands",
    "agents",
    "hooks",
    "mcpServers",
}


def list_global_plugins() -> list[dict[str, Any]]:
    root = _global_plugins_root()
    if not root.exists():
        return []
    plugins: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob(f"*/{MANIFEST_PATH}")):
        try:
            plugins.append(_plugin_summary(manifest_path.parent.parent))
        except ValueError:
            continue
    return plugins


def get_global_plugin(plugin_id: str) -> dict[str, Any]:
    path = _global_plugin_path(plugin_id)
    if not (path / MANIFEST_PATH).is_file():
        raise KeyError(f"global plugin not found: {_safe_plugin_id(plugin_id)}")
    return _plugin_detail(path)


def upsert_global_plugin(
    plugin_id: str,
    *,
    manifest: dict[str, Any] | None = None,
    skills: dict[str, str] | None = None,
    commands: dict[str, str] | None = None,
    agents: dict[str, str] | None = None,
    mcp: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    pid = _safe_plugin_id(plugin_id)
    path = _global_plugin_path(pid)
    path.mkdir(parents=True, exist_ok=True)
    (path / ".claude-plugin").mkdir(exist_ok=True)

    manifest_payload = dict(manifest or {})
    manifest_payload["name"] = pid
    manifest_payload.setdefault("version", "0.1.0")
    _require_valid_semver(str(manifest_payload.get("version") or "0.1.0"))
    manifest_payload.setdefault("description", "")
    _write_json(path / MANIFEST_PATH, manifest_payload)
    _write_json(path / STATE_PATH, {"enabled": bool(enabled)})

    for command_id, content in (commands or {}).items():
        _write_markdown(path / "commands" / f"{_safe_component_id(command_id)}.md", content)
    for agent_id, content in (agents or {}).items():
        _write_markdown(path / "agents" / f"{_safe_component_id(agent_id)}.md", content)
    for skill_id, content in (skills or {}).items():
        _write_markdown(path / "skills" / _safe_component_id(skill_id) / SKILL_ENTRYPOINT, content)
    if mcp is not None:
        _write_json(path / ".mcp.json", mcp)
    if hooks is not None:
        _write_json(path / "hooks" / "hooks.json", hooks)
    return get_global_plugin(pid)


def set_global_plugin_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    path = _global_plugin_path(plugin_id)
    if not (path / MANIFEST_PATH).is_file():
        raise KeyError(f"global plugin not found: {_safe_plugin_id(plugin_id)}")
    state = _read_json(path / STATE_PATH)
    state["enabled"] = bool(enabled)
    _write_json(path / STATE_PATH, state)
    return get_global_plugin(plugin_id)


def remove_global_plugin(plugin_id: str) -> dict[str, Any]:
    pid = _safe_plugin_id(plugin_id)
    root = _global_plugins_root().resolve()
    target = (root / pid).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"plugin path escapes global plugin root: {pid}") from exc
    if not target.exists():
        raise KeyError(f"global plugin not found: {pid}")
    shutil.rmtree(target)
    return {"ok": True, "id": pid, "removed": True}


def get_plugin_settings(plugin_id: str) -> dict[str, Any]:
    pid = _safe_plugin_id(plugin_id)
    path = _global_plugin_path(pid)
    if not (path / MANIFEST_PATH).is_file():
        raise KeyError(f"global plugin not found: {pid}")
    content = _read_text(path / SETTINGS_PATH)
    parsed = _parse_frontmatter_markdown(content)
    return {
        "plugin_id": pid,
        "path": str(path / SETTINGS_PATH),
        "exists": bool(content),
        "content": content,
        **parsed,
    }


def update_plugin_settings(plugin_id: str, content: str) -> dict[str, Any]:
    pid = _safe_plugin_id(plugin_id)
    path = _global_plugin_path(pid)
    if not (path / MANIFEST_PATH).is_file():
        raise KeyError(f"global plugin not found: {pid}")
    _write_markdown(path / SETTINGS_PATH, content)
    return get_plugin_settings(pid)


def import_global_plugin(source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"plugin source directory not found: {source}")
    validation = validate_plugin_source(str(source))
    if not validation.get("ok"):
        raise ValueError("; ".join(str(item) for item in validation.get("issues") or []) or "plugin validation failed")
    manifest = _read_json(source / MANIFEST_PATH)
    plugin_id = _safe_plugin_id(str(manifest.get("name") or source.name))
    target = _global_plugin_path(plugin_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target.resolve():
        shutil.copytree(source, target, dirs_exist_ok=True)
    if not (target / MANIFEST_PATH).is_file():
        raise ValueError(f"plugin manifest is required at {MANIFEST_PATH}")
    state = _read_json(target / STATE_PATH)
    state.setdefault("enabled", True)
    _write_json(target / STATE_PATH, state)
    return get_global_plugin(plugin_id)


def validate_plugin_source(source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    issues: list[str] = []
    warnings: list[str] = []
    if not source.is_dir():
        return {
            "ok": False,
            "source_path": str(source),
            "issues": [f"plugin source directory not found: {source}"],
            "warnings": [],
            "manifest": {},
            "components": _empty_component_counts(),
        }

    manifest_path = source / MANIFEST_PATH
    manifest = _read_json(manifest_path)
    if not manifest_path.is_file():
        issues.append(f"plugin manifest is required at {MANIFEST_PATH}")
    elif not manifest:
        issues.append(f"plugin manifest is invalid JSON or not an object: {MANIFEST_PATH}")

    if manifest_path.is_file() and "name" not in manifest:
        issues.append("plugin manifest requires name")
    for field in sorted(set(manifest) - MANIFEST_ALLOWED_FIELDS):
        warnings.append(f"unknown manifest field: {field}")

    raw_name = str(manifest.get("name") or source.name)
    try:
        plugin_id = _safe_plugin_id(raw_name)
    except ValueError:
        plugin_id = ""
        issues.append(f"invalid plugin name: {raw_name}")
    if raw_name and not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", raw_name):
        issues.append(f"invalid plugin name: {raw_name}")
    version = str(manifest.get("version") or "0.1.0")
    version_issue = _validate_semver(version)
    if version_issue:
        issues.append(version_issue)

    for field in ("commands", "agents", "hooks", "mcpServers"):
        for raw_path in _manifest_path_values(manifest.get(field)):
            path_issue = _validate_manifest_path(raw_path)
            if path_issue:
                issues.append(f"{field} path {raw_path!r} {path_issue}")

    commands = _discover_markdown(source, "commands", manifest.get("commands"))
    agents = _discover_markdown(source, "agents", manifest.get("agents"))
    skills = _discover_skills(source, plugin_id or "invalid-plugin")
    hooks = _discover_hooks(source, manifest)
    mcp_servers = _discover_mcp_servers(source, manifest)
    issues.extend(_duplicate_component_issues("commands", commands))
    issues.extend(_duplicate_component_issues("agents", agents))
    issues.extend(_duplicate_component_issues("skills", skills))
    issues.extend(_duplicate_component_issues("mcpServers", mcp_servers))
    issues.extend(_mcp_server_config_issues(mcp_servers))

    components = {
        "commands": len(commands),
        "agents": len(agents),
        "skills": len(skills),
        "hooks": len(hooks),
        "mcp_servers": len(mcp_servers),
    }
    if not any(components.values()):
        warnings.append("plugin has no discovered commands, agents, skills, hooks, or MCP servers")

    return {
        "ok": not issues,
        "source_path": str(source),
        "plugin_id": plugin_id,
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "components": components,
        "issues": issues,
        "warnings": warnings,
    }


def list_marketplace_plugins(marketplace_path: str) -> dict[str, Any]:
    path = _marketplace_path(marketplace_path)
    payload = _read_json(path)
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), list) else []
    items = []
    installed = {item["id"] for item in list_global_plugins()}
    for item in plugins:
        if not isinstance(item, dict):
            continue
        name = _safe_plugin_id(str(item.get("name") or ""))
        source_path = _marketplace_source_path(path, item.get("source"))
        items.append(
            {
                "name": name,
                "description": str(item.get("description") or ""),
                "version": str(item.get("version") or ""),
                "category": str(item.get("category") or ""),
                "author": item.get("author") if isinstance(item.get("author"), (dict, str)) else "",
                "source": str(item.get("source") or ""),
                "source_path": str(source_path) if source_path else "",
                "installed": name in installed,
            }
        )
    return {
        "ok": True,
        "marketplace": {
            "name": str(payload.get("name") or path.stem),
            "version": str(payload.get("version") or ""),
            "description": str(payload.get("description") or ""),
            "path": str(path),
        },
        "plugins": items,
    }


def import_marketplace_plugin(marketplace_path: str, plugin_name: str) -> dict[str, Any]:
    path = _marketplace_path(marketplace_path)
    payload = _read_json(path)
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), list) else []
    target_name = _safe_plugin_id(plugin_name)
    for item in plugins:
        if not isinstance(item, dict):
            continue
        if _safe_plugin_id(str(item.get("name") or "")) != target_name:
            continue
        source_path = _marketplace_source_path(path, item.get("source"))
        if source_path is None:
            raise ValueError(f"marketplace plugin source is invalid: {target_name}")
        return import_global_plugin(str(source_path))
    raise KeyError(f"marketplace plugin not found: {target_name}")


def list_enabled_plugin_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for plugin in list_global_plugins():
        if not plugin.get("enabled", True):
            continue
        try:
            detail = get_global_plugin(str(plugin["id"]))
        except KeyError:
            continue
        skills.extend(detail.get("skills") or [])
    return skills


def list_enabled_plugin_commands() -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for plugin in list_global_plugins():
        if not plugin.get("enabled", True):
            continue
        try:
            detail = get_global_plugin(str(plugin["id"]))
        except KeyError:
            continue
        for command in detail.get("commands") or []:
            if not isinstance(command, dict):
                continue
            command_id = f"{plugin['id']}:{command.get('id')}"
            commands.append(
                {
                    **command,
                    "plugin_id": plugin["id"],
                    "command_id": command_id,
                    "scope": "plugin",
                    "slash": f"/{command_id}",
                }
            )
    return commands


def list_enabled_plugin_agents() -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for plugin in list_global_plugins():
        if not plugin.get("enabled", True):
            continue
        try:
            detail = get_global_plugin(str(plugin["id"]))
        except KeyError:
            continue
        mcp_servers = detail.get("mcp_servers") or []
        for agent in detail.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            local_id = str(agent.get("id") or "").strip()
            if not local_id:
                continue
            agent_id = f"{plugin['id']}:{local_id}"
            agents.append(
                {
                    **agent,
                    "plugin_id": plugin["id"],
                    "agent_id": agent_id,
                    "scope": "plugin",
                    "mcp_servers": [
                        {"id": item.get("id"), "path": item.get("path")}
                        for item in mcp_servers
                        if isinstance(item, dict)
                    ],
                }
            )
    return agents


def list_enabled_plugin_hooks() -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    for plugin in list_global_plugins():
        if not plugin.get("enabled", True):
            continue
        try:
            detail = get_global_plugin(str(plugin["id"]))
        except KeyError:
            continue
        for hook in detail.get("hooks") or []:
            if not isinstance(hook, dict):
                continue
            local_id = str(hook.get("id") or "").strip()
            if not local_id:
                continue
            hooks.append(
                {
                    **hook,
                    "plugin_id": plugin["id"],
                    "hook_id": f"{plugin['id']}:{local_id}",
                    "scope": "plugin",
                }
            )
    return hooks


def list_enabled_plugin_mcp_servers() -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for plugin in list_global_plugins():
        if not plugin.get("enabled", True):
            continue
        try:
            detail = get_global_plugin(str(plugin["id"]))
        except KeyError:
            continue
        for server in detail.get("mcp_servers") or []:
            if not isinstance(server, dict):
                continue
            local_id = str(server.get("id") or "").strip()
            if not local_id:
                continue
            servers.append(
                {
                    **server,
                    "plugin_id": plugin["id"],
                    "server_ref": f"{plugin['id']}:{local_id}",
                    "scope": "plugin",
                    "plugin_root": plugin.get("path"),
                    "resolved_config": _resolve_plugin_runtime_value(server.get("config") or {}, plugin.get("path")),
                }
            )
    return servers


def get_plugin_command(command_id: str) -> dict[str, Any]:
    cid = _safe_command_ref(command_id)
    plugin_id, _, local_id = cid.partition(":")
    if not plugin_id or not local_id:
        plugin_id, _, local_id = cid.partition(".")
    if not plugin_id or not local_id:
        raise KeyError(f"plugin command id must be <plugin>:<command>: {cid}")
    plugin = get_global_plugin(plugin_id)
    if not plugin.get("enabled", True):
        raise KeyError(f"plugin is disabled: {plugin_id}")
    for command in plugin.get("commands") or []:
        if command.get("id") == local_id:
            command_id_value = f"{plugin_id}:{local_id}"
            return {
                **command,
                "plugin_id": plugin_id,
                "command_id": command_id_value,
                "scope": "plugin",
                "slash": f"/{command_id_value}",
            }
    raise KeyError(f"plugin command not found: {cid}")


def get_plugin_agent(agent_id: str) -> dict[str, Any]:
    aid = _safe_agent_ref(agent_id)
    plugin_id, _, local_id = aid.partition(":")
    if not plugin_id or not local_id:
        plugin_id, _, local_id = aid.partition(".")
    if not plugin_id or not local_id:
        raise KeyError(f"plugin agent id must be <plugin>:<agent>: {aid}")
    plugin = get_global_plugin(plugin_id)
    if not plugin.get("enabled", True):
        raise KeyError(f"plugin is disabled: {plugin_id}")
    mcp_servers = [
        {
            **item,
            "plugin_id": plugin_id,
            "server_ref": f"{plugin_id}:{item.get('id')}",
            "scope": "plugin",
            "plugin_root": plugin.get("path"),
            "resolved_config": _resolve_plugin_runtime_value(item.get("config") or {}, plugin.get("path")),
        }
        for item in plugin.get("mcp_servers") or []
        if isinstance(item, dict)
    ]
    for agent in plugin.get("agents") or []:
        if agent.get("id") == local_id:
            agent_id_value = f"{plugin_id}:{local_id}"
            return {
                **agent,
                "plugin_id": plugin_id,
                "agent_id": agent_id_value,
                "scope": "plugin",
                "mcp_servers": mcp_servers,
            }
    raise KeyError(f"plugin agent not found: {aid}")


def get_plugin_skill(skill_id: str) -> dict[str, Any]:
    sid = _safe_skill_ref(skill_id)
    if "." not in sid:
        raise KeyError(f"plugin skill id must be <plugin>.<skill>: {sid}")
    plugin_id, local_skill_id = sid.split(".", 1)
    plugin = get_global_plugin(plugin_id)
    if not plugin.get("enabled", True):
        raise KeyError(f"plugin is disabled: {plugin_id}")
    for skill in plugin.get("skills") or []:
        if skill.get("id") == local_skill_id or skill.get("skill_id") == sid:
            return skill
    raise KeyError(f"plugin skill not found: {sid}")


def _plugin_summary(path: Path) -> dict[str, Any]:
    manifest = _read_json(path / MANIFEST_PATH)
    plugin_id = _safe_plugin_id(str(manifest.get("name") or path.name))
    state = _read_json(path / STATE_PATH)
    enabled = bool(state.get("enabled", True))
    commands = _discover_markdown(path, "commands", manifest.get("commands"))
    agents = _discover_markdown(path, "agents", manifest.get("agents"))
    skills = _discover_skills(path, plugin_id)
    hooks = _discover_hooks(path, manifest)
    mcp_servers = _discover_mcp_servers(path, manifest)
    return {
        "id": plugin_id,
        "name": str(manifest.get("name") or plugin_id),
        "version": str(manifest.get("version") or ""),
        "description": str(manifest.get("description") or ""),
        "author": str(manifest.get("author") or ""),
        "enabled": enabled,
        "path": str(path),
        "manifest_path": str(path / MANIFEST_PATH),
        "commands_count": len(commands),
        "agents_count": len(agents),
        "skills_count": len(skills),
        "hooks_count": len(hooks),
        "mcp_servers_count": len(mcp_servers),
    }


def _plugin_detail(path: Path) -> dict[str, Any]:
    manifest = _read_json(path / MANIFEST_PATH)
    plugin_id = _safe_plugin_id(str(manifest.get("name") or path.name))
    return {
        **_plugin_summary(path),
        "manifest": manifest,
        "commands": _discover_markdown(path, "commands", manifest.get("commands")),
        "agents": _discover_markdown(path, "agents", manifest.get("agents")),
        "skills": _discover_skills(path, plugin_id),
        "hooks": _discover_hooks(path, manifest),
        "mcp_servers": _discover_mcp_servers(path, manifest),
        "readme": _read_text(path / "README.md"),
    }


def _discover_markdown(plugin_root: Path, default_dir: str, manifest_value: object = None) -> list[dict[str, Any]]:
    items = []
    seen: set[Path] = set()
    paths: list[Path] = []
    for candidate in _component_paths(plugin_root, default_dir, manifest_value):
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            paths.append(candidate)
        elif candidate.is_dir():
            paths.extend(sorted(candidate.glob("*.md")))
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        content = _read_text(path)
        parsed = _parse_frontmatter_markdown(content)
        frontmatter = parsed["frontmatter"]
        body = parsed["body"]
        items.append(
            {
                "id": path.stem,
                "entrypoint": path.name,
                "path": str(path),
                "summary": _component_summary(frontmatter, body, content),
                "allowed_tools": _frontmatter_list(frontmatter, "allowed-tools"),
                "argument_hint": _argument_hint(frontmatter),
                "model": str(frontmatter.get("model") or ""),
                "chars": len(content),
                "frontmatter": frontmatter,
                "body": body,
                "content": content,
            }
        )
    return items


def _discover_skills(path: Path, plugin_id: str) -> list[dict[str, Any]]:
    skills_root = path / "skills"
    if not skills_root.exists():
        return []
    out = []
    for skill_path in sorted(skills_root.glob(f"*/{SKILL_ENTRYPOINT}")):
        local_id = _safe_component_id(skill_path.parent.name)
        content = _read_text(skill_path)
        parsed = _parse_frontmatter_markdown(content)
        frontmatter = parsed["frontmatter"]
        body = parsed["body"]
        out.append(
            {
                "id": local_id,
                "skill_id": f"{plugin_id}.{local_id}",
                "plugin_id": plugin_id,
                "scope": "plugin",
                "enabled": True,
                "entrypoint": SKILL_ENTRYPOINT,
                "path": str(skill_path),
                "summary": _component_summary(frontmatter, body, content),
                "chars": len(content),
                "frontmatter": frontmatter,
                "body": body,
                "content": content,
            }
        )
    return out


def _discover_hooks(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    for hook_file in _component_paths(path, "hooks/hooks.json", manifest.get("hooks")):
        if hook_file.is_file():
            hook_id = "hooks" if hook_file == path / "hooks" / "hooks.json" else "manifest-path"
            hooks.append({"id": hook_id, "path": str(hook_file), "payload": _read_json(hook_file)})
    manifest_hooks = manifest.get("hooks")
    if isinstance(manifest_hooks, (dict, list)):
        hooks.append({"id": "manifest", "path": str(path / MANIFEST_PATH), "payload": manifest_hooks})
    return hooks


def _discover_mcp_servers(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for mcp_file in _component_paths(path, ".mcp.json", manifest.get("mcpServers")):
        if not mcp_file.is_file():
            continue
        payload = _read_json(mcp_file)
        servers = payload.get("mcpServers") if isinstance(payload, dict) else {}
        if isinstance(servers, dict):
            for server_id, config in servers.items():
                discovered[str(server_id)] = {"config": config if isinstance(config, dict) else {}, "path": str(mcp_file)}
    inline = manifest.get("mcpServers")
    if isinstance(inline, dict):
        for server_id, config in inline.items():
            discovered[str(server_id)] = {"config": config if isinstance(config, dict) else {}, "path": str(path / MANIFEST_PATH)}
    return [{"id": server_id, **payload} for server_id, payload in sorted(discovered.items())]


def _resolve_plugin_runtime_value(value: Any, plugin_root: object) -> Any:
    root = str(plugin_root or "")
    if isinstance(value, dict):
        return {key: _resolve_plugin_runtime_value(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_plugin_runtime_value(item, root) for item in value]
    if not isinstance(value, str):
        return value
    resolved = (
        value.replace("${CLAUDE_PLUGIN_ROOT}", root)
        .replace("${DEVWERK_PLUGIN_ROOT}", root)
        .replace("$CLAUDE_PLUGIN_ROOT", root)
        .replace("$DEVWERK_PLUGIN_ROOT", root)
    )
    if root and resolved.startswith(root) and ("/" in resolved or "\\" in resolved):
        return str(Path(resolved))
    return resolved


def _component_paths(plugin_root: Path, default_path: str, manifest_value: object = None) -> list[Path]:
    paths = [plugin_root / default_path]
    for raw_path in _manifest_path_values(manifest_value):
        path = _safe_manifest_path(plugin_root, raw_path)
        if path is not None:
            paths.append(path)
    return paths


def _marketplace_path(value: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if path.is_dir():
        path = path / ".claude-plugin" / "marketplace.json"
    if not path.is_file():
        raise ValueError(f"marketplace file not found: {path}")
    return path


def _marketplace_source_path(marketplace_path: Path, source: object) -> Path | None:
    text = str(source or "").strip()
    if not text:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return None
    normalized = text.replace("\\", "/")
    if normalized.startswith("./"):
        return (marketplace_path.parent.parent / normalized[2:]).resolve()
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (marketplace_path.parent.parent / path).resolve()


def _manifest_path_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _safe_manifest_path(plugin_root: Path, raw_path: str) -> Path | None:
    text = str(raw_path or "").strip().replace("\\", "/")
    if _validate_manifest_path(text):
        return None
    candidate = (plugin_root / text[2:]).resolve()
    try:
        candidate.relative_to(plugin_root.resolve())
    except ValueError:
        return None
    return candidate


def _validate_manifest_path(raw_path: str) -> str:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text.startswith("./"):
        return "must start with './'"
    if "../" in text or text in {"./", "."}:
        return "must stay inside the plugin root"
    if Path(text).is_absolute():
        return "must be relative"
    return ""


def _require_valid_semver(version: str) -> None:
    issue = _validate_semver(version)
    if issue:
        raise ValueError(issue)


def _validate_semver(version: str) -> str:
    text = str(version or "").strip()
    if not text:
        return "invalid plugin version: version is required"
    if not re.match(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", text):
        return f"invalid plugin version: {text} must use semantic versioning"
    return ""


def _duplicate_component_issues(kind: str, items: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("id") or "").strip()
        if not component_id:
            continue
        counts[component_id] = counts.get(component_id, 0) + 1
    return [f"duplicate {kind} id: {component_id}" for component_id, count in sorted(counts.items()) if count > 1]


def _mcp_server_config_issues(items: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("id") or "").strip() or "<unknown>"
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        server_type = str(config.get("type") or "stdio").strip().lower()
        if server_type not in {"stdio", "http", "sse"}:
            issues.append(f"mcpServers {server_id} unsupported type: {server_type}")
            continue
        if server_type == "stdio":
            command = str(config.get("command") or "").strip()
            if not command:
                issues.append(f"mcpServers {server_id} requires command for stdio server")
            continue
        url = str(config.get("url") or "").strip()
        if not url:
            issues.append(f"mcpServers {server_id} requires url for {server_type} server")
        elif not re.match(r"^https?://", url):
            issues.append(f"mcpServers {server_id} url must be http(s)")
    return issues


def _empty_component_counts() -> dict[str, int]:
    return {"commands": 0, "agents": 0, "skills": 0, "hooks": 0, "mcp_servers": 0}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _parse_frontmatter_markdown(content: str) -> dict[str, Any]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {"frontmatter": {}, "body": text}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {"frontmatter": {}, "body": text}
    raw_frontmatter = text[4:end]
    body = text[end + len("\n---\n") :]
    return {"frontmatter": _parse_simple_yaml(raw_frontmatter), "body": body}


def _parse_simple_yaml(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = _coerce_yaml_scalar(value.strip())
    return out


def _coerce_yaml_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.lower() in {"null", "none", "~"}:
        return None
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if re.match(r"^-?\d+$", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.match(r"^-?\d+\.\d+$", text):
        try:
            return float(text)
        except ValueError:
            return text
    if text.startswith("[") and text.endswith("]"):
        try:
            value = json.loads(text.replace("'", '"'))
            return value if isinstance(value, list) else text
        except json.JSONDecodeError:
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
    return text


def _component_summary(frontmatter: dict[str, Any], body: str, content: str) -> str:
    for key in ("description", "name", "title"):
        value = str(frontmatter.get(key) or "").strip()
        if value:
            return value[:160]
    return _first_heading_or_line(body or content)


def _frontmatter_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    raw = frontmatter.get(key)
    if raw is None:
        raw = frontmatter.get(key.replace("-", "_"))
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    if text == "*":
        return ["*"]
    return [item.strip() for item in text.split(",") if item.strip()]


def _argument_hint(frontmatter: dict[str, Any]) -> str:
    raw = frontmatter.get("argument-hint")
    if raw is None:
        raw = frontmatter.get("argument_hint")
    if isinstance(raw, list):
        return " ".join(f"[{str(item).strip('[]')}]" for item in raw if str(item).strip())
    return str(raw or "")


def _first_heading_or_line(content: str) -> str:
    for line in content.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:160]
    return ""


def _global_plugins_root() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "plugins"


def _plugin_import_root() -> Path:
    return _global_plugins_root()


def _global_plugin_path(plugin_id: str) -> Path:
    return _global_plugins_root() / _safe_plugin_id(plugin_id)


def _safe_plugin_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9-]+", "-", text).strip("-")
    if not text:
        raise ValueError("plugin id is required")
    if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", text):
        raise ValueError(f"invalid plugin id: {value}")
    return text[:100]


def _safe_component_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not text:
        raise ValueError("component id is required")
    if text in {".", ".."}:
        raise ValueError("invalid component id")
    return text[:100]


def _safe_skill_ref(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not text:
        raise ValueError("skill id is required")
    return text[:160]


def _safe_command_ref(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._:-]+", "-", text).strip("-._:")
    if not text:
        raise ValueError("command id is required")
    return text[:180]


def _safe_agent_ref(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._:-]+", "-", text).strip("-._:")
    if not text:
        raise ValueError("agent id is required")
    return text[:180]
