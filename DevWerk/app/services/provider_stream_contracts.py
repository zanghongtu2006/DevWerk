"""Typed Anthropic SSE events and complete-response assembly."""

from __future__ import annotations

import json
from app.services.visible_stream import publish_tool_preview, publish_visible_text
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.protocols.agent.provider_content import (
    AnthropicAssistantBlock,
    AnthropicRedactedThinkingBlock,
    AnthropicTextBlock,
    AnthropicThinkingBlock,
    AnthropicToolUseBlock,
)


class SSEFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str = Field(min_length=1)
    data: str = Field(min_length=1)


class SSEFrameDecoder:
    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def feed(self, raw_line: bytes | str) -> SSEFrame | None:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if line.startswith("event:"):
            self._event = line[6:].strip()
        elif line.startswith("data:"):
            self._data.append(line[5:].lstrip(" "))
        elif not line:
            if not self._data:
                self._event = ""
                return None
            frame = SSEFrame(event=self._event or "message", data="\n".join(self._data))
            self._event = ""
            self._data = []
            return frame
        return None

    def has_unfinished_frame(self) -> bool:
        return bool(self._event or self._data)


class StreamUsage(BaseModel):
    model_config = ConfigDict(extra="allow")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class StreamMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["assistant"]
    usage: StreamUsage | None = None


class MessageStartEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["message_start"]
    message: StreamMessage


class StreamTextStart(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["text"]
    text: str = ""


class StreamThinkingStart(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["thinking"]
    thinking: str = ""
    signature: str = ""


class StreamToolUseStart(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, JsonValue] = Field(default_factory=dict)


StreamBlockStart = (
    StreamTextStart | StreamThinkingStart |
    StreamToolUseStart | AnthropicRedactedThinkingBlock
)


class ContentBlockStartEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["content_block_start"]
    index: int = Field(ge=0)
    content_block: StreamBlockStart


class ContentBlockDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text_delta", "thinking_delta", "signature_delta", "input_json_delta"]
    text: str | None = None
    thinking: str | None = None
    signature: str | None = None
    partial_json: str | None = None


class ContentBlockDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["content_block_delta"]
    index: int = Field(ge=0)
    delta: ContentBlockDelta


class ContentBlockStopEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["content_block_stop"]
    index: int = Field(ge=0)


class MessageDelta(BaseModel):
    model_config = ConfigDict(extra="allow")
    stop_reason: str | None = None


class MessageDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["message_delta"]
    delta: MessageDelta
    usage: StreamUsage | None = None


class MessageStopEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["message_stop"]


STREAM_EVENT_MODELS = {
    "message_start": MessageStartEvent,
    "content_block_start": ContentBlockStartEvent,
    "content_block_delta": ContentBlockDeltaEvent,
    "content_block_stop": ContentBlockStopEvent,
    "message_delta": MessageDeltaEvent,
    "message_stop": MessageStopEvent,
}


class StreamBlock:
    def __init__(self, initial: StreamBlockStart) -> None:
        self.initial = initial
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.signature: list[str] = []
        self.partial_json: list[str] = []
        self.partial_size = 0
        self.stopped = False

    def add(self, delta: ContentBlockDelta) -> None:
        if self.stopped:
            raise ValueError("Anthropic stream sent a delta after content_block_stop")
        if isinstance(self.initial, StreamTextStart) and delta.type == "text_delta":
            self.text.append(delta.text or "")
        elif isinstance(self.initial, StreamThinkingStart) and delta.type == "thinking_delta":
            self.thinking.append(delta.thinking or "")
        elif isinstance(self.initial, StreamThinkingStart) and delta.type == "signature_delta":
            self.signature.append(delta.signature or "")
        elif isinstance(self.initial, StreamToolUseStart) and delta.type == "input_json_delta":
            fragment = delta.partial_json or ""
            self.partial_json.append(fragment)
            self.partial_size += len(fragment)
            publish_tool_preview(self.initial.name, self.partial_json, self.partial_size)
        else:
            raise ValueError(f"Anthropic stream delta {delta.type!r} does not match its content block")

    def finish(self) -> AnthropicAssistantBlock:
        if not self.stopped:
            raise ValueError("Anthropic stream content block did not stop")
        if isinstance(self.initial, StreamTextStart):
            return AnthropicTextBlock(text=self.initial.text + "".join(self.text))
        if isinstance(self.initial, StreamThinkingStart):
            return AnthropicThinkingBlock(
                thinking=self.initial.thinking + "".join(self.thinking),
                signature=self.initial.signature + "".join(self.signature),
            )
        if isinstance(self.initial, StreamToolUseStart):
            arguments = json.loads("".join(self.partial_json)) if self.partial_json else self.initial.input
            if not isinstance(arguments, dict):
                raise ValueError("Anthropic stream tool_use input must be an object")
            return AnthropicToolUseBlock(id=self.initial.id, name=self.initial.name, input=arguments)
        if isinstance(self.initial, AnthropicRedactedThinkingBlock):
            return self.initial
        raise ValueError("Unsupported Anthropic stream content block")


class AnthropicStreamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: list[AnthropicAssistantBlock]
    usage: StreamUsage
    stop_reason: str | None = None


class AnthropicStreamAssembler:
    def __init__(self) -> None:
        self.started = False
        self.finished = False
        self.blocks: dict[int, StreamBlock] = {}
        self.usage = StreamUsage()
        self.stop_reason: str | None = None

    def accept(self, frame: SSEFrame) -> bool:
        if frame.event == "ping":
            return False
        if frame.event == "error":
            raise ValueError(f"Anthropic stream error: {frame.data[:500]}")
        model = STREAM_EVENT_MODELS.get(frame.event)
        if model is None:
            raise ValueError(f"Unsupported Anthropic stream event: {frame.event!r}")
        event = model.model_validate_json(frame.data)
        if self.finished:
            raise ValueError("Anthropic stream continued after message_stop")
        if isinstance(event, MessageStartEvent):
            if self.started:
                raise ValueError("Duplicate Anthropic message_start")
            self.started = True
            if event.message.usage:
                self.usage = event.message.usage
        elif not self.started:
            raise ValueError("Anthropic stream omitted message_start")
        elif isinstance(event, ContentBlockStartEvent):
            if event.index in self.blocks or event.index != len(self.blocks):
                raise ValueError("Anthropic stream content block index is out of order")
            self.blocks[event.index] = StreamBlock(event.content_block)
            if isinstance(event.content_block, StreamTextStart):
                publish_visible_text(event.content_block.text)
        elif isinstance(event, ContentBlockDeltaEvent):
            if event.index not in self.blocks:
                raise ValueError("Anthropic stream delta has no content block")
            self.blocks[event.index].add(event.delta)
            if event.delta.type == "text_delta":
                publish_visible_text(event.delta.text or "")
        elif isinstance(event, ContentBlockStopEvent):
            if event.index not in self.blocks or self.blocks[event.index].stopped:
                raise ValueError("Anthropic stream content block stop is unmatched")
            self.blocks[event.index].stopped = True
        elif isinstance(event, MessageDeltaEvent):
            self.stop_reason = event.delta.stop_reason or self.stop_reason
            if event.usage:
                self.usage = self.usage.model_copy(update=event.usage.model_dump(exclude_none=True))
        elif isinstance(event, MessageStopEvent):
            self.finished = True
            return True
        return False

    def result(self) -> AnthropicStreamResponse:
        if not self.started or not self.finished:
            raise ValueError("Anthropic stream ended without message_stop")
        return AnthropicStreamResponse(
            content=[self.blocks[index].finish() for index in range(len(self.blocks))],
            usage=self.usage,
            stop_reason=self.stop_reason,
        )
