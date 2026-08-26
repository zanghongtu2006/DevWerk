from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class WorkflowGlobalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_resume_previous_tasks: bool = False


class GlobalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["devwerk.global-settings.v1"] = "devwerk.global-settings.v1"
    workflow: WorkflowGlobalSettings = Field(default_factory=WorkflowGlobalSettings)


GLOBAL_SETTINGS_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "workflow.auto_resume_previous_tasks",
        "group": "workflow",
        "label": "启动后自动继续未完成任务",
        "description": "开启后，DevWerk 启动时会自动恢复上一次未完成的 Workflow；关闭时等待用户通过 Conversation Agent 恢复。",
        "type": "boolean",
        "restart_required": True,
    },
)


def load_global_settings(path: Path) -> GlobalSettings:
    if not path.is_file():
        raise ValueError(f"Global settings file is required: {path}")
    try:
        source = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Global settings file {path} is not valid YAML: {exc}") from exc
    if not isinstance(source, dict):
        raise ValueError(f"Global settings file {path} must contain a YAML mapping")
    try:
        return GlobalSettings.model_validate(source)
    except Exception as exc:
        raise ValueError(
            f"Global settings file {path} does not match the schema: {exc}"
        ) from exc


def save_global_settings(path: Path, settings: GlobalSettings) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    content = yaml.safe_dump(
        settings.model_dump(mode="python"),
        allow_unicode=True,
        sort_keys=False,
    )
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)


def global_settings_payload(settings: GlobalSettings) -> dict[str, Any]:
    return {
        "schema_version": settings.schema_version,
        "values": settings.model_dump(mode="json"),
        "fields": [dict(item) for item in GLOBAL_SETTINGS_FIELDS],
    }


def restart_required_changes(
    previous: GlobalSettings,
    current: GlobalSettings,
) -> list[str]:
    before = previous.model_dump(mode="json")
    after = current.model_dump(mode="json")
    changed: list[str] = []
    for field in GLOBAL_SETTINGS_FIELDS:
        key = str(field["key"])
        group, name = key.split(".", 1)
        if field.get("restart_required") and before[group][name] != after[group][name]:
            changed.append(key)
    return changed
