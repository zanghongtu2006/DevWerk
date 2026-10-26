"""Anthropic message and content-block request contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from app.v1.protocols.agent.provider_content import (
    AnthropicAssistantBlock, AnthropicTextBlock, AnthropicToolUseBlock,
)


class AnthropicToolResultBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


AnthropicBlock = AnthropicAssistantBlock | AnthropicToolResultBlock


class AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["assistant", "user"]
    content: str | list[AnthropicBlock]

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")
