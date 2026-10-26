"""Validated provider continuation kept alongside normalized Agent output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str


class AnthropicThinkingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str


class AnthropicRedactedThinkingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str


class AnthropicToolUseBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, JsonValue]


AnthropicAssistantBlock = (
    AnthropicTextBlock | AnthropicThinkingBlock |
    AnthropicRedactedThinkingBlock | AnthropicToolUseBlock
)


class AnthropicAssistantContent(BaseModel):
    """The original ordered assistant blocks, private to provider replay."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["anthropic"] = "anthropic"
    blocks: list[AnthropicAssistantBlock] = Field(default_factory=list)

    def with_tool_ids(self, ids: list[str]) -> "AnthropicAssistantContent":
        """Match replayed tool_use IDs to the runner's executed call IDs."""
        blocks = self.blocks.copy()
        positions = [index for index, block in enumerate(blocks)
                     if isinstance(block, AnthropicToolUseBlock)]
        if len(positions) != len(ids):
            raise ValueError("provider assistant content and normalized tool calls differ")
        for position, identity in zip(positions, ids):
            blocks[position] = blocks[position].model_copy(update={"id": identity})
        return self.model_copy(update={"blocks": blocks})
