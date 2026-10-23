"""Typed envelopes for bounded, display-only projections of tool evidence."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator

from app.v1.domain import EdgeCheck
from app.v1.protocols.context.context_checkpoint_contracts import TruncationProjection
from app.v1.protocols.tools.planning.capability_loop_receipts import LoopAssetReceipt


_json_value = TypeAdapter(JsonValue)


class ToolResultView(BaseModel):
    model_config = ConfigDict(extra="allow")
    output: JsonValue = None
    projection: JsonValue = None

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "ToolResultView":
        return cls.model_validate(value)

    def with_output(self, output: Any) -> dict[str, JsonValue]:
        changed = self.model_copy(deep=True)
        changed.output = _json_value.validate_python(output)
        return changed.model_dump(mode="json", exclude_unset=True)

    def with_projection(self, projection: BaseModel) -> dict[str, JsonValue]:
        changed = self.model_copy(deep=True)
        changed.projection = projection.model_dump(mode="json")
        return changed.model_dump(mode="json", exclude_unset=True)


class ArchivedValueReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archived_sha256: str


class PlanningTruncationProjection(TruncationProjection):
    note: str = (
        "Complete planning contract fields; omitted prose and graph instructions remain in the archived result. "
        "Acceptance checks are optional."
    )


class LoopWritablePathReference(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)
    reference: str = Field(alias="$ref", min_length=1)


class LoopColumnProjection(BaseModel):
    key: str
    writable_paths: list[str | LoopWritablePathReference]


class LoopInspectPlanningProjection(BaseModel):
    loop_key: str | None = None
    version: str | None = None
    digest: str | None = None
    parameter_schema: dict[str, JsonValue] | None = None
    use_case: str | None = None
    selection_guide: str | None = None
    input: JsonValue = None
    output: JsonValue = None
    flow_unit: JsonValue = None
    task_contract: dict[str, JsonValue] = Field(default_factory=dict)
    columns: list[LoopColumnProjection] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)

    @field_validator("assets", mode="before")
    @classmethod
    def asset_paths(cls, value):
        return [item if isinstance(item, str) else LoopAssetReceipt.model_validate(item).path
                for item in value]


class AppliedWorkflowReference(BaseModel):
    id: str | None = None
    definition_hash: str | None = None
    workflow_plan_id: str | None = None


class AppliedWorkflowPlanReference(BaseModel):
    id: str | None = None
    flow_unit: JsonValue = None
    task_contract: dict[str, JsonValue]


class LoopApplyPlanningProjection(BaseModel):
    workflow: AppliedWorkflowReference
    workflow_plan: AppliedWorkflowPlanReference
    next: str = (
        "Revise this template when needed. Each Task is one independently accepted flow unit and traverses "
        "every Column; use task.plan.contract, task.plan.validate, task.plan.save, then task.create. "
        "Acceptance checks are optional."
    )


class WorkflowEdgeProjection(BaseModel):
    outcome: str | None = None
    target: JsonValue = None
    required_checks: list[EdgeCheck]


class WorkflowColumnProjection(BaseModel):
    key: str
    acceptance_checks: list[JsonValue]
    transitions: list[WorkflowEdgeProjection]


class WorkflowInspectPlanningProjection(BaseModel):
    id: str | None = None
    definition_hash: str | None = None
    workflow_plan_id: str | None = None
    source_loop_key: str | None = None
    source_loop_version: str | None = None
    source_loop_digest: str | None = None
    column_contracts: list[WorkflowColumnProjection] = Field(default_factory=list)


class PagedTextView(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    offset: int = Field(ge=0)
    total_characters: int | None = Field(default=None, ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    content_complete: bool | None = None
    projection_deferred: bool | None = None
    metadata_omitted: bool | None = None

    def page(self, content: str, next_offset: int, total: int) -> dict[str, JsonValue]:
        result = self.model_copy(deep=True)
        result.content = content
        result.total_characters = total
        result.next_offset = next_offset if next_offset < total else None
        result.content_complete = next_offset >= total
        return result.model_dump(mode="json", exclude_unset=True)


class AgentResultReducedView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agent_run_id: str | None = None
    tool_call_id: str | None = None
    field: str | None = None
    offset: int | None = None
    total_characters: int | None = None
    sha256: str | None = None
    content: str = ""
    next_offset: int | None = None
    content_complete: bool = False
    projection_deferred: bool = True


class MemoryReadReducedView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reference: str | None = None
    sha256: str | None = None
    offset: int | None = None
    total_characters: int | None = None
    scope: str | None = None
    scope_id: str | None = None
    scope_resolution: JsonValue = None
    content: str = ""
    next_offset: int | None = None
    content_complete: bool = False
    metadata_omitted: bool | None = None
    projection_deferred: bool | None = None

    @classmethod
    def from_source(cls, output: dict[str, Any]) -> "MemoryReadReducedView":
        result = cls.model_validate(output)
        metadata = output.get("metadata") or {}
        result.scope = metadata.get("scope")
        result.scope_id = metadata.get("scope_id")
        result.scope_resolution = output.get("scope_resolution")
        return result
