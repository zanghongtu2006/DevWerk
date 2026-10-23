"""Contracts for Requirement selection and scope revision tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator


class RequirementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementCreateInput(RequirementInput):
    scope_key: str = Field(min_length=1, description="Stable key for the delivery scope")
    objective: str = Field(min_length=1, description="Delivery objective")


class RequirementSelectInput(RequirementInput):
    requirement_id: str = Field(min_length=1, description="Existing Requirement identifier")


class RequirementCloseInput(RequirementSelectInput):
    status: Literal["completed", "cancelled"] = Field(description="Closing state")


class ScopeReviseInput(RequirementSelectInput):
    expected_revision: int = Field(ge=1, description="Current Requirement revision")
    expected_workflow_revision_id: str | None = Field(description="Current Workflow revision or null before first publication")
    objective: str = Field(min_length=1, description="Revised delivery objective")
    binding_patch: dict[str, Any] = Field(description="Loop binding changes; empty for a Workflow without a Loop")
    reason: str = Field(min_length=1, description="Reason for revising the Requirement")
    loop_digest: str | None = Field(default=None, description="Optional selected Loop package digest")

    @field_validator("binding_patch")
    @classmethod
    def validate_binding_patch(cls, value: dict[str, Any]) -> dict[str, JsonValue]:
        return TypeAdapter(dict[str, JsonValue]).validate_python(value)


class RequirementVersion(BaseModel):
    model_config = ConfigDict(extra="allow")
    revision: int = Field(ge=1)
    objective: str
    status: str

    @classmethod
    def from_snapshot(cls, current: dict[str, Any], snapshot: Any) -> dict[str, JsonValue]:
        result = cls.model_validate(current)
        result.revision = snapshot['revision']
        result.objective = snapshot['objective']
        result.status = snapshot['status']
        return result.model_dump(mode="json")


class ScopeInspectReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")
    workflow: dict[str, JsonValue] | None = None
    workflow_revision_id: str | None = None
    bindings: dict[str, JsonValue] | None = None
    requirements: list[dict[str, JsonValue]] = Field(default_factory=list)

    @classmethod
    def current(cls, workflow: dict[str, Any], scope: dict[str, Any], requirements: list[dict[str, Any]]) -> dict[str, JsonValue]:
        result = cls.model_validate(scope)
        result.workflow_revision_id = workflow['id']
        result.bindings = TypeAdapter(dict[str, JsonValue]).validate_python(workflow.get('loop_bindings') or {})
        result.requirements = TypeAdapter(list[dict[str, JsonValue]]).validate_python(requirements)
        return result.model_dump(mode="json", exclude_none=True)
