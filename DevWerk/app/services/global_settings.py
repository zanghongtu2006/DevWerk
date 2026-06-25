from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import _normalize_llm_config, reload_settings, settings

_log = logging.getLogger("devwerk.settings")


ENV_KEYS = {
    "llm_config_path": ["DEVWERK_LLM_CONFIG_PATH"],
}

SECTION_TITLES = {
    "llm_config_path": "LLM config file",
}


def get_global_settings() -> dict[str, Any]:
    cfg = settings()
    env_values = _read_env_values()
    path = _llm_config_path()
    llm_config = cfg.llm_config()
    return {
        "ok": True,
        "env_path": str(_env_path()),
        "llm_config_path": str(path),
        "settings": {
            "llms": llm_config.get("llms", {}),
            "routing": llm_config.get("routing", {}),
            "raw": {"DEVWERK_LLM_CONFIG_PATH": env_values.get("DEVWERK_LLM_CONFIG_PATH", cfg.devwerk_llm_config_path)},
        },
    }


def update_global_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = settings().llm_config()
    changed = False
    if "llms" in payload:
        if not isinstance(payload["llms"], dict):
            raise ValueError("llms must be an object")
        current["llms"] = payload["llms"]
        changed = True
    if "routing" in payload:
        if not isinstance(payload["routing"], dict):
            raise ValueError("routing must be an object")
        current["routing"] = payload["routing"]
        changed = True

    if not changed:
        return get_global_settings()

    current = _normalize_llm_config(current, settings()._legacy_llm_config())
    path = _llm_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log.info("updated backend llm settings path=%s", path)
    reload_settings()
    return get_global_settings()


def _group(env_values: dict[str, str], defaults: dict[str, Any], group_name: str) -> dict[str, Any]:
    return {
        key: env_values.get(key, _env_text(defaults.get(key)))
        for key in ENV_KEYS[group_name]
    }


def _env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _llm_config_path() -> Path:
    cfg = settings()
    configured = Path(cfg.devwerk_llm_config_path or "./config/llm.json")
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def _read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    path = _env_path()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(line)
            if parsed is not None:
                key, value = parsed
                values[key] = value
    for keys in ENV_KEYS.values():
        for key in keys:
            if key in os.environ:
                values.setdefault(key, os.environ[key])
    return values


def _write_env(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing:
        parsed = _parse_env_line(line)
        if parsed is None:
            lines.append(line)
            continue
        key, _ = parsed
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)

    missing = [key for keys in ENV_KEYS.values() for key in keys if key in updates and key not in seen]
    if missing and lines and lines[-1].strip():
        lines.append("")
    current_group = None
    for key in missing:
        group_name = _group_for_key(key)
        if group_name != current_group:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"# {SECTION_TITLES[group_name]}")
            current_group = group_name
        lines.append(f"{key}={updates[key]}")

    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _group_for_key(key: str) -> str:
    for group_name, keys in ENV_KEYS.items():
        if key in keys:
            return group_name
    raise KeyError(key)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def _env_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\r", "").replace("\n", " ").strip()
    return text
