"""Typed wire request envelopes for OpenAI- and Anthropic-compatible APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class _ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class OpenAIRequiredFunction(_ProviderRequest):
    name: str = Field(min_length=1)


class OpenAIRequiredToolChoice(_ProviderRequest):
    type: Literal["function"] = "function"
    function: OpenAIRequiredFunction


class OpenAICompletionRequest(_ProviderRequest):
    model: str = Field(min_length=1)
    messages: list[dict[str, JsonValue]]
    temperature: float
    top_p: float | None = None
    tools: list[dict[str, JsonValue]] | None = None
    tool_choice: Literal["auto", "required"] | OpenAIRequiredToolChoice | None = None

    @model_validator(mode="after")
    def validate_tool_choice(self) -> "OpenAICompletionRequest":
        if self.tool_choice is not None and not self.tools:
            raise ValueError("tool_choice requires exposed tools")
        return self


class AnthropicToolDefinition(_ProviderRequest):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, JsonValue]


class AnthropicToolChoice(_ProviderRequest):
    type: Literal["any", "tool"]
    name: str | None = None

    @model_validator(mode="after")
    def validate_name(self) -> "AnthropicToolChoice":
        if self.type == "tool" and not self.name:
            raise ValueError("named tool choice requires a tool name")
        if self.type == "any" and self.name is not None:
            raise ValueError("any tool choice cannot name a tool")
        return self


class AnthropicCompletionRequest(_ProviderRequest):
    model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    temperature: float
    system: str
    messages: list[dict[str, JsonValue]]
    top_p: float | None = None
    tools: list[AnthropicToolDefinition] | None = None
    tool_choice: AnthropicToolChoice | None = None


class OllamaOptions(_ProviderRequest):
    temperature: float
    top_p: float | None = None


class OllamaCompletionRequest(_ProviderRequest):
    model: str = Field(min_length=1)
    stream: Literal[False] = False
    messages: list[dict[str, JsonValue]]
    options: OllamaOptions
    tools: list[dict[str, JsonValue]] | None = None
