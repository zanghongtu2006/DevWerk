"""Input contracts for Loop discovery and generic Project control tools."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, create_model, field_validator

from app.v1.protocols.tools.core.capability_protocol import GovernanceDecisionKind

DYNAMIC_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoopListInput(ToolInput):
    category: str | None = Field(default=None, description="Optional Loop category")
    tag: str | None = Field(default=None, description="Optional Loop tag")
    query: str | None = Field(default=None, description="Optional search text")
    limit: int | None = Field(default=None, ge=1, description="Maximum returned Loops")


class LoopInspectInput(ToolInput):
    loop_key: str = Field(min_length=1, description="Key returned by loop.list")


class LoopApplyInput(ToolInput):
    loop_key: str = Field(min_length=1, description="Loop key to apply")
    bindings: dict[str, Any] = Field(description="Values required by the selected Loop parameter contract")

    @field_validator("bindings")
    @classmethod
    def valid_bindings(cls, value: dict[str, Any]) -> dict[str, JsonValue]:
        return DYNAMIC_JSON_OBJECT.validate_python(value)


class PageLimitInput(ToolInput):
    limit: int | None = Field(default=None, ge=1, description="Maximum returned records")


class EventListInput(ToolInput):
    after: int = Field(default=0, ge=0, description="Exclusive event cursor")
    limit: int | None = Field(default=None, ge=1, le=500, description="Maximum returned events")


class InstructionUpdateInput(ToolInput):
    instruction: str = Field(max_length=60000, description="Replacement Conversation Agent instruction")


class SupervisionScheduleInput(ToolInput):
    reason: str = Field(min_length=1, max_length=4000, description="Reason for a future supervision review")
    due_at: str = Field(min_length=1, description="Review time in the service's accepted timestamp format")


class GovernanceDecisionInput(ToolInput):
    kind: GovernanceDecisionKind = Field(description="Decision category")
    subject_id: str | None = Field(default=None, description="Related Task or entity identifier")
    decision: str = Field(min_length=1, max_length=200, description="Decision name")
    data: dict[str, Any] = Field(description="Category-specific decision data")

    @field_validator("data")
    @classmethod
    def valid_data(cls, value: dict[str, Any]) -> dict[str, JsonValue]:
        return DYNAMIC_JSON_OBJECT.validate_python(value)


def with_maximum_page_limit(model: type[ToolInput], maximum: int) -> type[ToolInput]:
    """Bind a configured page limit on the model field rather than editing JSON Schema."""
    return create_model(
        f"Bound{model.__name__}", __base__=model,
        limit=(int | None, Field(default=None, ge=1, le=maximum, description="Maximum returned records")),
    )
