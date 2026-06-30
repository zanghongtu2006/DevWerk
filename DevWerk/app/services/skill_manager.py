from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.kanban import get_project_settings, update_project_settings


SKILL_ENTRYPOINT = "SKILL.md"


def list_global_skills() -> list[dict[str, Any]]:
    return [
        _skill_summary(path.parent.name, path, scope="global")
        for path in sorted(_global_skills_root().glob(f"*/{SKILL_ENTRYPOINT}"))
        if path.is_file()
    ]


def get_global_skill(skill_id: str) -> dict[str, Any]:
    sid = _safe_skill_id(skill_id)
    path = _global_skill_path(sid)
    if not path.is_file():
        raise KeyError(f"global skill not found: {sid}")
    return _skill_detail(sid, path, scope="global")


def upsert_global_skill(skill_id: str, skill_md: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    sid = _safe_skill_id(skill_id)
    path = _global_skill_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize_skill_md(skill_md, sid), encoding="utf-8", newline="\n")
    return {**_skill_detail(sid, path, scope="global"), "metadata": metadata or {}}


def list_project_skills(project_id: str) -> list[dict[str, Any]]:
    skills = _project_skill_map(project_id)
    return [
        _project_skill_summary(skill_id, payload)
        for skill_id, payload in sorted(skills.items())
        if isinstance(payload, dict)
    ]


def get_project_skill(project_id: str, skill_id: str) -> dict[str, Any]:
    sid = _safe_skill_id(skill_id)
    payload = _project_skill_map(project_id).get(sid)
    if not isinstance(payload, dict):
        raise KeyError(f"project skill not found: {sid}")
    return _project_skill_detail(sid, payload)


def upsert_project_skill(
    project_id: str,
    skill_id: str,
    skill_md: str,
    *,
    enabled: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = _safe_skill_id(skill_id)
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = dict(settings.get("parameters") or {}) if isinstance(settings, dict) else {}
    skills = dict(parameters.get("skills") or {})
    skills[sid] = {
        "id": sid,
        "enabled": bool(enabled),
        "entrypoint": SKILL_ENTRYPOINT,
        "skill_md": _normalize_skill_md(skill_md, sid),
        "metadata": metadata or {},
    }
    parameters["skills"] = skills
    update_project_settings(project_id, parameters=parameters)
    return get_project_skill(project_id, sid)


def set_project_skill_enabled(project_id: str, skill_id: str, enabled: bool) -> dict[str, Any]:
    sid = _safe_skill_id(skill_id)
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = dict(settings.get("parameters") or {}) if isinstance(settings, dict) else {}
    skills = dict(parameters.get("skills") or {})
    payload = skills.get(sid)
    if not isinstance(payload, dict):
        raise KeyError(f"project skill not found: {sid}")
    payload["enabled"] = bool(enabled)
    skills[sid] = payload
    parameters["skills"] = skills
    update_project_settings(project_id, parameters=parameters)
    return get_project_skill(project_id, sid)


def effective_skill_catalog(project_id: str) -> dict[str, Any]:
    globals_by_id = {item["id"]: item for item in list_global_skills()}
    project_by_id = {item["id"]: item for item in list_project_skills(project_id)}
    return {
        "ok": True,
        "project_id": project_id,
        "entrypoint": SKILL_ENTRYPOINT,
        "global": list(globals_by_id.values()),
        "project": list(project_by_id.values()),
        "effective": [*globals_by_id.values(), *project_by_id.values()],
    }


def resolve_agent_skills(project_id: str, skill_ids: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    project_skills = _project_skill_map(project_id)
    for raw_id in skill_ids or []:
        sid = _safe_skill_id(str(raw_id or ""))
        if not sid or sid in seen:
            continue
        seen.add(sid)
        project_payload = project_skills.get(sid)
        if isinstance(project_payload, dict):
            detail = _project_skill_detail(sid, project_payload)
            if detail.get("enabled", True):
                resolved.append(detail)
                continue
        path = _global_skill_path(sid)
        if path.is_file():
            resolved.append(_skill_detail(sid, path, scope="global"))
        else:
            resolved.append(
                {
                    "id": sid,
                    "scope": "missing",
                    "enabled": False,
                    "entrypoint": SKILL_ENTRYPOINT,
                    "content": "",
                    "summary": f"Skill {sid!r} is referenced but no SKILL.md was found.",
                }
            )
    return resolved


def _project_skill_map(project_id: str) -> dict[str, Any]:
    settings_payload = get_project_settings(project_id)
    settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = settings.get("parameters") if isinstance(settings, dict) else {}
    skills = parameters.get("skills") if isinstance(parameters, dict) else {}
    return skills if isinstance(skills, dict) else {}


def _project_skill_summary(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("skill_md") or "")
    return {
        "id": skill_id,
        "scope": "project",
        "enabled": bool(payload.get("enabled", True)),
        "entrypoint": str(payload.get("entrypoint") or SKILL_ENTRYPOINT),
        "summary": _first_heading_or_line(content),
        "chars": len(content),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }


def _project_skill_detail(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("skill_md") or "")
    return {
        **_project_skill_summary(skill_id, payload),
        "content": content,
    }


def _skill_summary(skill_id: str, path: Path, *, scope: str) -> dict[str, Any]:
    content = _read_text(path)
    return {
        "id": skill_id,
        "scope": scope,
        "enabled": True,
        "entrypoint": SKILL_ENTRYPOINT,
        "path": str(path),
        "summary": _first_heading_or_line(content),
        "chars": len(content),
    }


def _skill_detail(skill_id: str, path: Path, *, scope: str) -> dict[str, Any]:
    return {
        **_skill_summary(skill_id, path, scope=scope),
        "content": _read_text(path),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _first_heading_or_line(content: str) -> str:
    for line in content.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:160]
    return ""


def _normalize_skill_md(skill_md: str, skill_id: str) -> str:
    text = str(skill_md or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        text = f"# {skill_id}\n\nDescribe when this skill applies and how an agent should use it."
    return text + "\n"


def _safe_skill_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not text:
        raise ValueError("skill id is required")
    if text in {".", ".."}:
        raise ValueError("invalid skill id")
    return text[:120]


def _global_skills_root() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "skills"


def _global_skill_path(skill_id: str) -> Path:
    return _global_skills_root() / _safe_skill_id(skill_id) / SKILL_ENTRYPOINT
