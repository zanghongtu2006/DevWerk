"""Shared JSON records for execution checkpoints and wait requests."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, SerializeAsAny, TypeAdapter

from app.v1.protocols.tools.core.capability_protocol import FailureDisposition
from app.v1.domain import ToolResult

_json_object = TypeAdapter(dict[str, JsonValue])


class ExecutionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    failure_disposition: FailureDisposition | None = None
    execution_key: str | None = None
    operation_id: str | None = None
    await_handle_id: str | None = None
    source: str | None = None
    capability_result: JsonValue = None

    @classmethod
    def read(cls, value: dict[str, Any] | None) -> "ExecutionCheckpoint":
        return cls.model_validate(_json_object.validate_python(value or {}))

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class WaitRequestPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    checkpoint: SerializeAsAny[ExecutionCheckpoint] | None = None
    source: str | None = None
    capability: str | None = None

    @classmethod
    def read(cls, value: dict[str, Any] | None) -> "WaitRequestPayload":
        return cls.model_validate(_json_object.validate_python(value or {}))

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class ColumnOutputPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    context: dict[str, JsonValue] | None = None

    @classmethod
    def with_context(cls, output: dict[str, Any], context: dict[str, Any]) -> dict[str, JsonValue]:
        result = cls.model_validate(_json_object.validate_python(output))
        result.context = _json_object.validate_python(context)
        return result.model_dump(mode="json", exclude_none=True)


class SequenceStepResult(ToolResult):
    step: int
    save_as: str

    @classmethod
    def from_result(cls, result: ToolResult, step: int, save_as: str) -> dict[str, JsonValue]:
        return cls(
            step=step, save_as=save_as, ok=result.ok, capability=result.capability,
            status=result.status, output=result.output, error=result.error,
            await_handle_draft=result.await_handle_draft, checkpoint=result.checkpoint,
        ).model_dump(mode="json")


class SequenceCheckpoint(ExecutionCheckpoint):
    executor_kind: Literal["capability_sequence"] = "capability_sequence"
    input: JsonValue
    scope: dict[str, JsonValue]
    results: list[dict[str, JsonValue]]
    next_step_index: int | None = None
    failed_step_index: int | None = None
    failed_result: JsonValue = None
    awaiting_save_as: str | None = None
    awaiting_capability: str | None = None

    def inherit(self, prior: dict[str, Any] | None) -> "SequenceCheckpoint":
        for name, value in ExecutionCheckpoint.read(prior).wire().items():
            if name not in self.model_fields_set:
                setattr(self, name, value)
        return self


class SequenceCompletionOutput(BaseModel):
    summary: str
    steps: list[dict[str, JsonValue]]
