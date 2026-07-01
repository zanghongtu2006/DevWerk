from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any


MANIFEST_PATH = ".claude-plugin/plugin.json"
STATE_PATH = ".devwerk-plugin.json"
SKILL_ENTRYPOINT = "SKILL.md"


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


def import_global_plugin(source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"plugin source directory not found: {source}")
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
    commands = _discover_markdown(path / "commands")
    agents = _discover_markdown(path / "agents")
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
        "commands": _discover_markdown(path / "commands"),
        "agents": _discover_markdown(path / "agents"),
        "skills": _discover_skills(path, plugin_id),
        "hooks": _discover_hooks(path, manifest),
        "mcp_servers": _discover_mcp_servers(path, manifest),
        "readme": _read_text(path / "README.md"),
    }


def _discover_markdown(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.glob("*.md")):
        content = _read_text(path)
        items.append(
            {
                "id": path.stem,
                "entrypoint": path.name,
                "path": str(path),
                "summary": _first_heading_or_line(content),
                "chars": len(content),
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
        out.append(
            {
                "id": local_id,
                "skill_id": f"{plugin_id}.{local_id}",
                "plugin_id": plugin_id,
                "scope": "plugin",
                "enabled": True,
                "entrypoint": SKILL_ENTRYPOINT,
                "path": str(skill_path),
                "summary": _first_heading_or_line(content),
                "chars": len(content),
                "content": content,
            }
        )
    return out


def _discover_hooks(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    hook_file = path / "hooks" / "hooks.json"
    if hook_file.is_file():
        hooks.append({"id": "hooks", "path": str(hook_file), "payload": _read_json(hook_file)})
    manifest_hooks = manifest.get("hooks")
    if isinstance(manifest_hooks, (dict, list)):
        hooks.append({"id": "manifest", "path": str(path / MANIFEST_PATH), "payload": manifest_hooks})
    return hooks


def _discover_mcp_servers(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _read_json(path / ".mcp.json")
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        servers = manifest.get("mcpServers") if isinstance(manifest.get("mcpServers"), dict) else {}
    return [
        {"id": str(server_id), "config": config if isinstance(config, dict) else {}, "path": str(path / ".mcp.json")}
        for server_id, config in sorted(servers.items())
    ]


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
