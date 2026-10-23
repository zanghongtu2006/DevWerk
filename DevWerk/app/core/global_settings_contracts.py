"""Wire contracts for global settings metadata and update receipts."""

from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue


class GlobalSettingField(BaseModel):
    key: str
    group: str
    label: str
    description: str
    type: Literal["boolean"] = "boolean"
    restart_required: bool = False


class GlobalSettingsPayload(BaseModel):
    schema_version: str
    values: dict[str, JsonValue]
    fields: list[GlobalSettingField]


class GlobalSettingsUpdatePayload(GlobalSettingsPayload):
    saved: bool = True
    changed: bool
    restart_required: bool
    restart_scheduled: bool
    restart_changes: list[str] = Field(default_factory=list)

    @classmethod
    def from_base(
        cls, base: GlobalSettingsPayload, *, changed: bool,
        restart_changes: list[str], restart_scheduled: bool,
    ) -> "GlobalSettingsUpdatePayload":
        return cls(
            schema_version=base.schema_version, values=base.values, fields=base.fields,
            changed=changed, restart_required=bool(restart_changes),
            restart_scheduled=restart_scheduled, restart_changes=restart_changes,
        )
