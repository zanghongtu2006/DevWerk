"""Anthropic message and content-block request contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str


class AnthropicToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, JsonValue]


class AnthropicToolResultBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


AnthropicBlock = AnthropicTextBlock | AnthropicToolUseBlock | AnthropicToolResultBlock


class AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["assistant", "user"]
    content: str | list[AnthropicBlock]

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")
