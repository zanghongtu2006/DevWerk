"""Typed provider-facing declaration of one internal capability."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ToolFunctionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, JsonValue]


class FunctionToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["function"] = "function"
    function: ToolFunctionDefinition

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")
