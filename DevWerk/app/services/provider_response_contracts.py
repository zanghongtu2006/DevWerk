"""Normalized completion results returned by external model adapters."""

from pydantic import BaseModel, ConfigDict, JsonValue


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_unset=True)


class ProviderToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    arguments: dict[str, JsonValue]


class ProviderCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    tool_calls: list[ProviderToolCall]
    usage: ProviderUsage

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_unset=True)
