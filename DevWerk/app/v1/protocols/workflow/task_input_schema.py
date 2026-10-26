"""Typed authoring view for a Workflow's optional Task-input JSON Schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowTaskInputSchema(BaseModel):
    """Expose common JSON Schema keywords; validate extensions as a full schema later."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_type: str | list[str] | None = Field(
        default=None, alias="type",
        description="JSON Schema type of the Task input; use object for a Task input mapping.",
    )
    properties: dict[str, dict[str, Any] | bool] | None = Field(
        default=None,
        description="Optional mapping from Task input field names to JSON Schema objects or boolean schemas.",
    )
    required: list[str] | None = Field(
        default=None,
        description="Optional array of required Task input field names; never an object or field-to-value map.",
    )
    additional_properties: bool | dict[str, Any] | None = Field(
        default=None, alias="additionalProperties",
        description="Optional JSON Schema rule for Task input fields not listed in properties.",
    )

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_unset=True)
